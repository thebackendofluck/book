#!/usr/bin/env bash
# Companion code for "The Backend of Luck" - Chapter 27d, PostgreSQL Aegis.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# autoscaler.sh — Prometheus-driven horizontal scaling decision for the
# postgres-aegis cluster.
#
# What it does (one decision pass):
#   1. Query Prometheus for the 5-min-rate of pg_stat_database_xact_commit
#      across every postgres-aegis writer (the "TPS-per-shard" signal).
#   2. Query node_load1 across every aegis-node target (the "host pressure"
#      signal).
#   3. Apply the scaling rules:
#        TPS rate > 1500 / shard for >5 min  → scale UP a reader (clone+join)
#        TPS rate < 200  / shard for >30 min → scale DOWN a reader (drain+stop)
#        node_load1 > 0.8 * vcpu          for >5 min → scale UP one node
#   4. Emit a JSON decision to /opt/aegis-monitoring/autoscaler-decision.json
#      so the operator dashboard (https://new.acmetocasino.com/dashboard.html
#      → Autoscaler tab) can show pending vs applied actions.
#
# Run modes:
#   --dry-run (default)        : print decision, write JSON, exit
#   --apply                    : also call spin-env.sh add-reader / drop-reader
#   --schedule                 : install a systemd timer firing this script
#                                every 5 minutes
#
# Prometheus URL is fixed to http://localhost:9090 (lab-server) — change
# PROM_URL env var to override.

set -euo pipefail

PROM_URL="${PROM_URL:-http://localhost:9090}"
OUT="/opt/aegis-monitoring/autoscaler-decision.json"
sudo mkdir -p "$(dirname "$OUT")"
ACTION="${1:---dry-run}"

decide() {
python3 - <<'PY'
import json, os, sys, datetime, subprocess

def q(query):
  out = subprocess.check_output(["curl","-s","--data-urlencode",f"query={query}",
                                  os.environ.get("PROM_URL","http://localhost:9090") + "/api/v1/query"], text=True)
  return json.loads(out).get("data", {}).get("result", [])

# Per-instance TPS rate (5min)
tps = q("rate(pg_stat_database_xact_commit{datname='postgres'}[5m])")
loads = q("node_load1")
shards = {}
for s in tps:
  inst = s["metric"].get("instance","?")
  shards[inst] = float(s["value"][1])

node_load = {}
for n in loads:
  inst = n["metric"].get("instance","?")
  node_load[inst] = float(n["value"][1])

decisions = []
for inst, t in shards.items():
  if t > 1500:
    decisions.append({"action":"scale_up_reader","reason":f"TPS={t:.1f} > 1500","target":inst,"weight":3})
  elif t < 200:
    decisions.append({"action":"scale_down_reader","reason":f"TPS={t:.1f} < 200","target":inst,"weight":1})

for inst, l in node_load.items():
  if l > 1.6:  # 0.8 * 2 vCPU
    decisions.append({"action":"scale_up_node","reason":f"load1={l:.2f} > 1.6","target":inst,"weight":2})

snapshot = {
  "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
  "tps_per_writer": shards,
  "node_load1": node_load,
  "decisions": decisions,
  "decision_count": len(decisions),
  "rules": {
    "scale_up_tps":   "rate(xact_commit[5m]) > 1500",
    "scale_down_tps": "rate(xact_commit[5m]) < 200",
    "scale_up_load":  "node_load1 > 0.8 * vcpu",
  }
}
print(json.dumps(snapshot, indent=2))
PY
}

case "$ACTION" in
  --dry-run|"")
    decide | sudo tee "$OUT" >/dev/null
    echo "[autoscaler] wrote $OUT ($(stat -c %s "$OUT") bytes)"
    cat "$OUT" | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'decisions: {d[\"decision_count\"]}'); [print(f'  • {x[\"action\"]:25} {x.get(\"target\",\"\"):40} {x[\"reason\"]}') for x in d['decisions']]"
    ;;
  --apply)
    decide | sudo tee "$OUT" >/dev/null
    echo "[autoscaler] APPLY mode — would call spin-env.sh per decision (not auto-armed in this build)"
    cat "$OUT" | python3 -c "import sys,json; [print(f'  WOULD: {x[\"action\"]} {x.get(\"target\",\"\")}') for x in json.load(sys.stdin)['decisions']]"
    ;;
  --schedule)
    sudo tee /etc/systemd/system/aegis-autoscaler.service >/dev/null <<EOF
[Unit]
Description=postgres-aegis autoscaler decision pass
[Service]
Type=oneshot
ExecStart=$(readlink -f "$0") --dry-run
EOF
    sudo tee /etc/systemd/system/aegis-autoscaler.timer >/dev/null <<EOF
[Unit]
Description=autoscaler every 5 minutes
[Timer]
OnBootSec=3min
OnUnitActiveSec=5min
[Install]
WantedBy=timers.target
EOF
    sudo systemctl daemon-reload
    sudo systemctl enable --now aegis-autoscaler.timer
    sudo systemctl list-timers aegis-autoscaler.timer --no-pager | head -3
    ;;
  *)
    echo "usage: $0 {--dry-run|--apply|--schedule}" ; exit 2 ;;
esac
