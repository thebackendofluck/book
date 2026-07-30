#!/bin/bash
# Companion code for "The Backend of Luck" - Chapter 20, Hardware Security Module Infrastructure.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# openbao-unseal.sh — Unseal OpenBao using Shamir keys from init file.
#
# Deploy:
#   sudo cp openbao-auto-unseal.sh /opt/openbao-unseal.sh
#   sudo chmod 700 /opt/openbao-unseal.sh
#   sudo chown root:root /opt/openbao-unseal.sh
#
# Systemd service (openbao-unseal.service):
#   [Unit]
#   Description=OpenBao Unseal
#   After=openbao.service
#   Requires=openbao.service
#   ConditionPathExists=/opt/yubihsm-evidence/openbao-init.json
#
#   [Service]
#   Type=oneshot
#   ExecStartPre=/bin/sleep 3
#   ExecStart=/opt/openbao-unseal.sh
#   User=root
#   StandardOutput=journal
#   StandardError=journal
#   RemainAfterExit=no
#   TimeoutStartSec=120
#
#   [Install]
#   WantedBy=multi-user.target
#
# Security notes:
#   - Init file MUST be 600 root:root — script refuses to run otherwise
#   - Script itself MUST be 700 root:root — no other user should trigger it
#   - Uses BAO_CACERT with proper PKI chain — no skip-verify
#   - Health endpoint returns 503 when sealed; any HTTP response = API is up
#
# Compliance: PCI DSS Req. 4 — TLS verification is mandatory.
# BAO_SKIP_VERIFY is never set in this script.

set -uo pipefail

INIT_FILE="/opt/yubihsm-evidence/openbao-init.json"
BAO_ADDR="https://127.0.0.1:8200"
BAO_CACERT="/etc/ssl/certs/openbao-ca.pem"
MAX_WAIT=60
INTERVAL=2

export BAO_ADDR
export BAO_CACERT
unset BAO_SKIP_VERIFY 2>/dev/null || true

# ── Security check: init file permissions ─────────────────────────────────────
stat_perms=$(stat -c '%a' "$INIT_FILE" 2>/dev/null || echo '000')
if [ "$stat_perms" != '600' ]; then
    echo "[ERROR] $INIT_FILE has permissions $stat_perms — expected 600. Aborting." >&2
    exit 1
fi

# ── Wait for OpenBao API ───────────────────────────────────────────────────────
# The /v1/sys/health endpoint returns 503 when sealed, 200 when active.
# Any valid HTTP response code (3-digit) means TLS handshake succeeded and the
# API is up — that is sufficient to proceed with unseal.
elapsed=0
until curl -s --cacert "$BAO_CACERT" -o /dev/null -w '%{http_code}' \
      "$BAO_ADDR/v1/sys/health" 2>/dev/null | grep -qE '^[0-9]{3}$'; do
    sleep $INTERVAL
    elapsed=$((elapsed + INTERVAL))
    if [ $elapsed -ge $MAX_WAIT ]; then
        echo "[ERROR] OpenBao did not become reachable within ${MAX_WAIT}s" >&2
        exit 1
    fi
done
echo "[INFO] OpenBao API is reachable."

# ── Check seal state ──────────────────────────────────────────────────────────
# bao status exits 2 when sealed (even with -format=json), so capture with || true
sealed_json=$(bao status -format=json 2>/dev/null || true)
sealed=$(echo "$sealed_json" | python3 -c \
    'import json,sys; print(json.load(sys.stdin)["sealed"])' 2>/dev/null || echo 'True')

if [ "$sealed" = 'False' ]; then
    echo "[INFO] OpenBao is already unsealed. Nothing to do."
    exit 0
fi
echo "[INFO] OpenBao is sealed. Proceeding with unseal..."

# ── Extract unseal keys ───────────────────────────────────────────────────────
KEY1=$(python3 -c "import json; print(json.load(open('$INIT_FILE'))['unseal_keys_b64'][0])")
KEY2=$(python3 -c "import json; print(json.load(open('$INIT_FILE'))['unseal_keys_b64'][1])")
KEY3=$(python3 -c "import json; print(json.load(open('$INIT_FILE'))['unseal_keys_b64'][2])")

# ── Apply the shares ──────────────────────────────────────────────────────────
# Shares go in on stdin, never as arguments: argv is world readable through
# /proc/<pid>/cmdline, so passing them on the command line exposes every share
# to any local user for the duration of the unseal, regardless of the file
# permission check above.
#
# NOTE ON SPLIT KNOWLEDGE: reading every share from one local file means this
# host can unseal itself unattended, which is convenient and is NOT split
# knowledge or dual control. Anyone who reads that file holds a complete unseal
# capability. Do not present this arrangement as satisfying PCI DSS 3.7.6. If
# you need that control, the shares must be held by separate custodians or
# fetched at unseal time from a system this host cannot read on its own.
echo "[INFO] Applying unseal key 1/3..."
printf '%s' "$KEY1" | bao operator unseal - > /dev/null || { echo "[ERROR] Unseal key 1 failed" >&2; exit 1; }
echo "[INFO] Applying unseal key 2/3..."
printf '%s' "$KEY2" | bao operator unseal - > /dev/null || { echo "[ERROR] Unseal key 2 failed" >&2; exit 1; }
echo "[INFO] Applying unseal key 3/3..."
printf '%s' "$KEY3" | bao operator unseal - > /dev/null || { echo "[ERROR] Unseal key 3 failed" >&2; exit 1; }

# ── Verify ────────────────────────────────────────────────────────────────────
sealed_after_json=$(bao status -format=json 2>/dev/null || true)
sealed_after=$(echo "$sealed_after_json" | python3 -c \
    'import json,sys; print(json.load(sys.stdin)["sealed"])' 2>/dev/null || echo 'True')

if [ "$sealed_after" = 'False' ]; then
    echo "[INFO] OpenBao unsealed successfully."
else
    echo "[ERROR] OpenBao is still sealed after unseal attempt." >&2
    exit 1
fi
