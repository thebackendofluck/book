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

# scripts/rotate-dek.sh — rotate the postgres-aegis DEK via YubiHSM.
#
# The DEK is AES-128, wrapped under an AES-256 wrap-key in the HSM.
# Rotation = generate new DEK, wrap, store in OpenBao (new version),
# and trigger pgbouncer rolling reload so every Postgres backend picks it up.
# No row-level re-encryption happens: the ciphertext stays, only the key
# changes. Old ciphertext stays decryptable by the previous DEK version
# until the grace window (7 days) elapses; then `clear-old-dek.sh` runs.

set -euo pipefail

TARGET="${1:?usage: $0 <target>}"
HERE="$(cd "$(dirname "$0")/.." && pwd)"
INVENTORY="$HERE/inventory/${TARGET}.yml"

: "${BAO_TOKEN:?set BAO_TOKEN}"
: "${BAO_ADDR:=https://10.0.0.12:8200}"
HSM_CONNECTOR="${HSM_CONNECTOR:-http://10.0.0.12:12345}"

# 1. Pull current HSM session password from BAO
HSM_PW=$(curl -sfk -H "X-Vault-Token: $BAO_TOKEN" \
  "$BAO_ADDR/v1/casino/postgres/aegis/hsm_auth_pw" | jq -r .data.data.password)
[ -n "$HSM_PW" ] || { echo "cannot fetch HSM password from bao"; exit 2; }

# 2. Generate fresh DEK in the HSM and wrap it
NEW_DEK_HEX=$(yubihsm-shell -a get-pseudo-random --connector "$HSM_CONNECTOR" \
  --authkey 1 --password "$HSM_PW" --count 16 --out-format hex)

WRAPPED_HEX=$(printf '%s' "$NEW_DEK_HEX" | xxd -r -p | \
  yubihsm-shell -a wrap-data --connector "$HSM_CONNECTOR" \
    --authkey 1 --password "$HSM_PW" --wrapkey 0x0100 \
    --in /dev/stdin --out-format hex)

# 3. Store new version in BAO (KV v2 auto-versions)
curl -sfk -H "X-Vault-Token: $BAO_TOKEN" \
  -X POST -H "Content-Type: application/json" \
  -d "{\"data\":{\"wrapped_hex\":\"$WRAPPED_HEX\",\"rotated_at\":\"$(date -u '+%Y-%m-%dT%H:%M:%SZ')\"}}" \
  "$BAO_ADDR/v1/casino/postgres/aegis/dek_wrapped" | jq '.data'

# 4. Trigger rolling reload of Postgres backends
echo "[rotate] rolling reload — signals pg_aegis to re-read unwrapped DEK from shared memory"
for h in $(python3 -c "
import yaml
inv = yaml.safe_load(open('$INVENTORY'))
for g in ('writers','readers'):
    for hn in inv['all']['children'][g]['children']:
        for _, hv in inv['all']['children'][hn]['hosts'].items():
            print(hv['ansible_host'])
"); do
  ssh -o BatchMode=yes "ansible@$h" "sudo systemctl kill --signal=HUP patroni" || true
  sleep 2
done

echo "[rotate] done. Verify with: bao kv get -version=auto casino/postgres/aegis/dek_wrapped"
