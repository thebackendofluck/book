#!/usr/bin/env python3
# Companion code for "The Backend of Luck" - Chapter 27d, PostgreSQL Aegis.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""postgres-aegis autoscaler.

Watches Prometheus for shard health + load signals and issues add/remove
requests against the existing shell scripts. Safe defaults: strict hysteresis,
cooldown, and minimum/maximum bounds.

Inputs (env or flags):
  PROM_URL          http://prometheus:9090 (default)
  INVENTORY_FILE    path to inventory yaml
  TARGET            lab-server | secondary-host

Metrics it watches (all from postgres_exporter + node_exporter):
  pg_stat_replication_lag_bytes     — per-replica WAL lag
  pg_stat_activity_count            — backends per node (for connection saturation)
  pg_database_size_bytes            — per-shard data volume
  node_filesystem_avail_bytes       — per-VM free disk

Decision logic:
  Scale-out READ replica when ALL of:
      * mean(active_backends / max_connections over 5m) > 0.70
      * max(replication_lag_bytes over 5m) < 50 MB                (replicas healthy)
      * current replica count < READ_MAX
      * last action ≥ COOLDOWN_MIN ago
  Scale-in READ replica when ALL of:
      * mean(active_backends / max_connections over 30m) < 0.20
      * current replica count > READ_MIN
      * last action ≥ COOLDOWN_MIN ago

  Scale-out WRITE shard when ALL of:
      * mean(WAL-rate over 15m) > 80% of writer's 24h p95
      * disk_free on writer VM < 30%
      * current shard count < WRITE_MAX
      * last WRITE action ≥ COOLDOWN_MIN * 4 ago

Never scales WRITE DOWN automatically — removing a write shard requires
reshard back into survivors; a human must trigger it.
"""
from __future__ import annotations
import argparse
import json
import os
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

STATE_FILE = Path("/var/lib/postgres-aegis/autoscaler.state")


def prom_query(prom_url: str, q: str) -> float | None:
    """Instant query to Prometheus; returns first value as float, or None."""
    url = f"{prom_url}/api/v1/query?query={urllib.parse.quote(q)}"
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            data = json.loads(r.read())
    except Exception as e:
        print(f"[prom] query failed: {e}", file=sys.stderr)
        return None
    if data.get("status") != "success":
        return None
    res = data["data"]["result"]
    if not res:
        return None
    try:
        return float(res[0]["value"][1])
    except (KeyError, IndexError, ValueError):
        return None


def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {"last_read_action": 0, "last_write_action": 0, "history": []}


def save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2))


def run_script(argv: list[str]) -> int:
    print(f"[autoscaler] exec: {' '.join(argv)}")
    p = subprocess.run(argv, check=False)
    return p.returncode


def current_replica_count(shard: str, target: str, inventory: Path) -> int:
    """Reads the inventory file to count current replicas in a shard."""
    import yaml
    inv = yaml.safe_load(inventory.read_text())
    grp = f"shard_{shard.split('-')[-1]}_readers"
    return len((inv["all"]["children"].get(grp, {}).get("hosts") or {}))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--prom",    default=os.environ.get("PROM_URL", "http://prometheus:9090"))
    ap.add_argument("--target",  default=os.environ.get("TARGET", "lab-server"))
    ap.add_argument("--inventory", type=Path,
                    default=Path(__file__).parent.parent / "inventory" / "lab-server.yml")
    ap.add_argument("--read-min",   type=int, default=3)
    ap.add_argument("--read-max",   type=int, default=15)
    ap.add_argument("--write-min",  type=int, default=2)
    ap.add_argument("--write-max",  type=int, default=8)
    ap.add_argument("--cooldown-min", type=int, default=10)
    ap.add_argument("--dry-run",    action="store_true")
    ap.add_argument("--once",       action="store_true", help="Single evaluation, then exit")
    ap.add_argument("--interval-sec", type=int, default=60)
    args = ap.parse_args()

    state = load_state()
    now = int(time.time())
    cooldown_s = args.cooldown_min * 60

    def should_scale_out_read(shard: str) -> tuple[bool, str]:
        saturation = prom_query(args.prom,
            f'avg_over_time((pg_stat_activity_count{{shard="{shard}"}} / '
            f'pg_settings_max_connections{{shard="{shard}"}})[5m:])')
        lag = prom_query(args.prom,
            f'max_over_time(pg_stat_replication_lag_bytes{{shard="{shard}"}}[5m])')
        if saturation is None or lag is None:
            return False, f"metrics unavailable (sat={saturation}, lag={lag})"
        if saturation < 0.70:
            return False, f"saturation {saturation:.2%} below 70%"
        if lag > 50 * 1024 * 1024:
            return False, f"replication lag {lag/1024/1024:.1f}MB above 50MB"
        count = current_replica_count(shard, args.target, args.inventory)
        if count >= args.read_max:
            return False, f"already at max ({count})"
        if now - state["last_read_action"] < cooldown_s:
            return False, "cooldown"
        return True, (
            f"sat={saturation:.2%} lag={lag/1024/1024:.1f}MB count={count}"
        )

    def should_scale_in_read(shard: str) -> tuple[bool, str]:
        saturation = prom_query(args.prom,
            f'avg_over_time((pg_stat_activity_count{{shard="{shard}"}} / '
            f'pg_settings_max_connections{{shard="{shard}"}})[30m:])')
        if saturation is None:
            return False, "metrics unavailable"
        if saturation > 0.20:
            return False, f"saturation {saturation:.2%} above 20% floor"
        count = current_replica_count(shard, args.target, args.inventory)
        if count <= args.read_min:
            return False, f"already at min ({count})"
        if now - state["last_read_action"] < cooldown_s:
            return False, "cooldown"
        return True, f"sat={saturation:.2%} count={count}"

    def should_scale_out_write() -> tuple[bool, str]:
        wal_rate = prom_query(args.prom,
            'avg_over_time(rate(pg_xlog_position_bytes[1m])[15m:])')
        p95 = prom_query(args.prom,
            'quantile_over_time(0.95, rate(pg_xlog_position_bytes[1m])[24h:])')
        disk = prom_query(args.prom,
            '(node_filesystem_avail_bytes{mountpoint="/var/lib/postgresql/16/main"} / '
            'node_filesystem_size_bytes{mountpoint="/var/lib/postgresql/16/main"})')
        if None in (wal_rate, p95, disk):
            return False, "metrics unavailable"
        # ty cannot narrow `float | None` via `None in tuple` — explicit asserts
        assert wal_rate is not None and p95 is not None and disk is not None
        if wal_rate < 0.80 * p95:
            return False, f"wal_rate {wal_rate:.0f} below 80% of p95 {p95:.0f}"
        if disk > 0.30:
            return False, f"disk free {disk:.0%} above 30%"
        if now - state["last_write_action"] < cooldown_s * 4:
            return False, "cooldown"
        return True, f"wal_rate={wal_rate:.0f} p95={p95:.0f} disk_free={disk:.0%}"

    def action(name: str, argv: list[str]) -> None:
        record = {"at": now, "action": name, "dry_run": args.dry_run}
        state["history"].append(record)
        if not args.dry_run:
            rc = run_script(argv)
            record["rc"] = rc
            if "read" in name:  state["last_read_action"] = now
            if "write" in name: state["last_write_action"] = now
            save_state(state)

    def evaluate_once() -> None:
        for shard in ("shard-a", "shard-b"):
            ok, why = should_scale_out_read(shard)
            if ok:
                next_ip = f"10.0.90.{100 + hash(shard) % 50 + len(state['history'])}"
                action(f"add-read-{shard}",
                       ["bash", str(Path(__file__).parent / "add-read-shard.sh"),
                        shard, next_ip])
                continue
            print(f"[eval] scale-out-read {shard}: no  — {why}")

            ok, why = should_scale_in_read(shard)
            if ok:
                import yaml
                inv = yaml.safe_load(args.inventory.read_text())
                grp = f"shard_{shard.split('-')[-1]}_readers"
                last_host = sorted(inv["all"]["children"][grp]["hosts"].keys())[-1]
                action(f"remove-read-{shard}",
                       ["bash", str(Path(__file__).parent / "remove-read-shard.sh"),
                        last_host, "--destroy-vm"])
                continue
            print(f"[eval] scale-in-read {shard}: no  — {why}")

        ok, why = should_scale_out_write()
        if ok:
            print(f"[eval] scale-out-write signalled  — {why}")
            print("[eval] write scale-out requires human sign-off; not triggering automatically.")
        else:
            print(f"[eval] scale-out-write: no  — {why}")

    if args.once:
        evaluate_once()
        return 0

    while True:
        evaluate_once()
        time.sleep(args.interval_sec)


if __name__ == "__main__":
    sys.exit(main())
