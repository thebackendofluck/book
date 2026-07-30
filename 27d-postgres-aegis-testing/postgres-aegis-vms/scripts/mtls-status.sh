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

# mtls-status.sh — emit a JSON snapshot of the postgres-aegis mTLS estate
# for consumption by the operator dashboard
# (https://new.acmetocasino.com/dashboard.html → "HSM Security" tab).
#
# Output: /opt/aegis-monitoring/mtls-status.json (atomic write).
# Schedule: every 5 minutes via systemd timer (the `schedule` subcmd
# below installs it).
#
# Dashboard scraping pattern (mirrors cf-cert-poller):
#   the dashboard expects a poll endpoint serving this JSON; if the
#   ops-dashboard backend already polls /metrics or static files,
#   add this path to its allow-list.

set -euo pipefail

OUT_DIR=/opt/aegis-monitoring
OUT="$OUT_DIR/mtls-status.json"
INVENTORY="${INVENTORY:-/tmp/aegis-test/inventory/proxmox-secondary-host.yml}"

mkdir -p "$OUT_DIR"

emit() {
  python3 - <<'PY'
import json, os, subprocess, glob, datetime, pathlib, sys

PKI_DIR = "/etc/pki/postgres-aegis"
INVENTORY = os.environ.get("INVENTORY", "/tmp/aegis-test/inventory/proxmox-secondary-host.yml")

def cert_info(crt):
    out = subprocess.check_output(
        ["openssl", "x509", "-in", crt, "-noout",
         "-subject", "-issuer", "-startdate", "-enddate", "-serial",
         "-fingerprint", "-sha256"], text=True)
    fields = dict(line.split("=", 1) for line in out.strip().splitlines() if "=" in line)
    end = subprocess.check_output(["date", "-d", fields["notAfter"], "+%s"], text=True).strip()
    days = (int(end) - int(datetime.datetime.utcnow().timestamp())) // 86400
    return {
        "subject": fields.get("subject", "").strip(),
        "issuer": fields.get("issuer", "").strip(),
        "not_before": fields.get("notBefore", "").strip(),
        "not_after": fields.get("notAfter", "").strip(),
        "serial": fields.get("serial", "").strip(),
        "fingerprint_sha256": fields.get("SHA256 Fingerprint", "").strip(),
        "remaining_days": days,
        "status": "ok" if days > 14 else ("warning" if days > 0 else "expired"),
    }

ca_path = f"{PKI_DIR}/ca.crt"
ca = cert_info(ca_path) if os.path.exists(ca_path) else None

servers = []
for crt in sorted(glob.glob(f"{PKI_DIR}/server-*.crt")):
    if crt.endswith(".old"):
        continue
    host = pathlib.Path(crt).stem.replace("server-", "")
    info = cert_info(crt)
    info["host"] = host
    servers.append(info)

# Read rotation log for last-rotation summary
log = "/var/log/postgres-aegis-mtls-rotate.log"
last_rot = "never"
rotated_24h = 0
if os.path.exists(log):
    text = pathlib.Path(log).read_text()
    passes = [l for l in text.splitlines() if l.startswith("===== rotation pass")]
    if passes:
        last_rot = passes[-1].split("pass ", 1)[-1].rstrip(" =")
    rotated_24h = sum(1 for l in text.splitlines() if "RE-ISSUING" in l)

snapshot = {
    "generated_at": datetime.datetime.utcnow().isoformat() + "Z",
    "ca": ca,
    "servers": servers,
    "server_count": len(servers),
    "min_remaining_days": min((s["remaining_days"] for s in servers), default=None),
    "last_rotation_pass": last_rot,
    "rotations_in_log": rotated_24h,
    "next_scheduled_rotation": subprocess.check_output(
        ["systemctl", "show", "-p", "NextElapseUSecRealtime", "--value",
         "postgres-aegis-mtls-rotate.timer"],
        text=True, stderr=subprocess.DEVNULL).strip() or "n/a",
    "rotation_timer_active": subprocess.run(
        ["systemctl", "is-active", "postgres-aegis-mtls-rotate.timer"],
        stdout=subprocess.DEVNULL).returncode == 0,
}
print(json.dumps(snapshot, indent=2))
PY
}

emit > "$OUT.tmp"
mv "$OUT.tmp" "$OUT"
chmod 0644 "$OUT"
echo "[mtls-status] wrote $OUT ($(wc -c <"$OUT") bytes)"

case "${1:-}" in
  schedule)
    cat > /etc/systemd/system/aegis-mtls-status.service <<EOF
[Unit]
Description=postgres-aegis mTLS status snapshot
[Service]
Type=oneshot
ExecStart=$(readlink -f "$0")
EOF
    cat > /etc/systemd/system/aegis-mtls-status.timer <<EOF
[Unit]
Description=mTLS status snapshot every 5 minutes
[Timer]
OnBootSec=2min
OnUnitActiveSec=5min
[Install]
WantedBy=timers.target
EOF
    systemctl daemon-reload
    systemctl enable --now aegis-mtls-status.timer
    systemctl list-timers aegis-mtls-status.timer --no-pager | tail -3
    ;;
esac
