#!/bin/sh
# Companion code for "The Backend of Luck" - Chapter 27d, PostgreSQL Aegis.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# yubihsm_dek_wrap.sh
#
# Wraps a freshly generated AES-128 DEK under a YubiHSM 2 wrap-key.
# Intended to be run from `lab-server`, where the yubihsm-connector daemon is
# bound to 127.0.0.1:12345 and the YubiHSM 2 USB device is attached.
#
# Output is a hex-encoded wrapped DEK that goes into OpenBao at
#   casino/postgres/aegis/dek_wrapped
# The unwrap happens inside Postgres (pg_aegis) after pulling from OpenBao
# via external-secrets.

set -eu

CONNECTOR="${YUBIHSM_CONNECTOR:-http://127.0.0.1:12345}"
AUTH_KEY_ID="${YUBIHSM_AUTH_KEY_ID:-1}"
AUTH_PW="${YUBIHSM_AUTH_PW:?set YUBIHSM_AUTH_PW (default factory: password)}"
WRAP_KEY_ID="${YUBIHSM_WRAP_KEY_ID:-0x0100}"
BAO_PATH="${BAO_PATH:-casino/postgres/aegis/dek_wrapped}"

command -v yubihsm-shell >/dev/null || {
  echo "yubihsm-shell missing - apt install yubihsm-shell"
  exit 1
}
command -v bao >/dev/null || command -v vault >/dev/null || {
  echo "warn: bao/vault CLI missing - the wrapped DEK will only be printed"
}

echo "[hsm] generating fresh DEK (AES-128, 16 bytes)"
DEK_HEX=$(yubihsm-shell -a get-pseudo-random --connector "$CONNECTOR" \
  --authkey "$AUTH_KEY_ID" --password "$AUTH_PW" --count 16 --out-format hex)
[ -z "$DEK_HEX" ] && { echo "DEK generation failed"; exit 2; }

echo "[hsm] wrapping DEK under wrap-key id=$WRAP_KEY_ID"
WRAPPED_HEX=$(printf '%s' "$DEK_HEX" | xxd -r -p | \
  yubihsm-shell -a wrap-data --connector "$CONNECTOR" \
    --authkey "$AUTH_KEY_ID" --password "$AUTH_PW" \
    --wrapkey "$WRAP_KEY_ID" --in /dev/stdin --out-format hex)

if command -v bao >/dev/null; then
  echo "[hsm] storing wrapped DEK at OpenBao path $BAO_PATH"
  bao kv put "$BAO_PATH" wrapped_hex="$WRAPPED_HEX" \
    wrapped_at="$(date -u '+%Y-%m-%dT%H:%M:%SZ')" \
    wrap_key_id="$WRAP_KEY_ID"
elif command -v vault >/dev/null; then
  vault kv put "$BAO_PATH" wrapped_hex="$WRAPPED_HEX" \
    wrap_key_id="$WRAP_KEY_ID"
else
  printf 'WRAPPED_DEK_HEX=%s\n' "$WRAPPED_HEX"
fi

echo "[hsm] done. external-secrets-operator will sync into k8s next refreshInterval."
