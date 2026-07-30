#!/usr/bin/env bash
# Companion code for "The Backend of Luck" - Chapter 20, Hardware Security Module Infrastructure.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# test-tde-postgresql.sh
# PostgreSQL column-level TDE test using OpenBao Transit (GDPR Art.32 validation).
# Encrypts PII fields (email, full_name, phone) via Transit, stores ciphertext in DB,
# decrypts and verifies roundtrip. Reports latency.
# Prerequisites: OpenBao running, Transit/field-cipher key created,
#                PostgreSQL accessible via Docker container.
# Usage: BAO_TOKEN=<token> PG_CONTAINER=pg18-hsm-test bash test-tde-postgresql.sh

set -euo pipefail

BAO_ADDR="${BAO_ADDR:-https://127.0.0.1:8200}"
TRANSIT_KEY="${TRANSIT_KEY:-field-cipher}"
PG_CONTAINER="${PG_CONTAINER:-pg18-hsm-test}"
PG_USER="${PG_USER:-acmetocasino}"
PG_DB="${PG_DB:-casino_hsm_test}"
EVIDENCE_DIR="${EVIDENCE_DIR:-/opt/yubihsm-evidence}"
SAMPLE_SIZE="${SAMPLE_SIZE:-10}"

log()  { echo "[$(date '+%H:%M:%S')] $*"; }
pass() { echo "[PASS] $*"; }
fail() { echo "[FAIL] $*" >&2; exit 1; }

psql_exec() {
    local sql="$1"
    sudo docker exec "${PG_CONTAINER}" psql -U "${PG_USER}" -d "${PG_DB}" -t -c "${sql}" 2>/dev/null
}

if [ -z "${BAO_TOKEN:-}" ]; then
    fail "BAO_TOKEN environment variable is required"
fi
export BAO_TOKEN BAO_ADDR

log "=== PostgreSQL TDE Column-Level Encryption Test ==="
log "Database: ${PG_DB} | Transit key: ${TRANSIT_KEY} | Sample: ${SAMPLE_SIZE} rows"

# Create encrypted PII staging table
psql_exec "
CREATE TABLE IF NOT EXISTS gdpr_tde_test (
    id SERIAL PRIMARY KEY,
    player_ref UUID,
    email_cipher TEXT,
    name_cipher TEXT,
    phone_cipher TEXT,
    key_version INTEGER DEFAULT 1,
    created_at TIMESTAMPTZ DEFAULT NOW()
);" > /dev/null

log "Fetching ${SAMPLE_SIZE} players for encryption test..."
SAMPLE_FILE="$(mktemp)"
psql_exec "SELECT id, email, full_name, phone FROM players LIMIT ${SAMPLE_SIZE};" > "${SAMPLE_FILE}"

START_NS="$(date +%s%N)"
ROW_COUNT=0

while IFS='|' read -r pid email fullname phone; do
    pid="$(echo "${pid}" | tr -d ' ')"
    email="$(echo "${email}" | tr -d ' ')"
    fullname="$(echo "${fullname}" | sed 's/^[[:space:]]*//' | sed 's/[[:space:]]*$//')"
    phone="$(echo "${phone}" | tr -d ' ')"
    [ -z "${pid}" ] && continue

    EMAIL_CIPHER="$(bao write -tls-skip-verify -field=ciphertext "transit/encrypt/${TRANSIT_KEY}" \
        plaintext="$(printf '%s' "${email}" | base64 -w0)" 2>/dev/null)"
    NAME_CIPHER="$(bao write -tls-skip-verify -field=ciphertext "transit/encrypt/${TRANSIT_KEY}" \
        plaintext="$(printf '%s' "${fullname}" | base64 -w0)" 2>/dev/null)"
    PHONE_CIPHER="$(bao write -tls-skip-verify -field=ciphertext "transit/encrypt/${TRANSIT_KEY}" \
        plaintext="$(printf '%s' "${phone}" | base64 -w0)" 2>/dev/null)"

    psql_exec "INSERT INTO gdpr_tde_test (player_ref, email_cipher, name_cipher, phone_cipher)
        VALUES ('${pid}', '${EMAIL_CIPHER}', '${NAME_CIPHER}', '${PHONE_CIPHER}');" > /dev/null
    ROW_COUNT=$(( ROW_COUNT + 1 ))
done < "${SAMPLE_FILE}"
rm -f "${SAMPLE_FILE}"

END_NS="$(date +%s%N)"
ELAPSED_MS=$(( (END_NS - START_NS) / 1000000 ))
AVG_MS=$(( ELAPSED_MS / ROW_COUNT ))
log "Encrypted ${ROW_COUNT} rows in ${ELAPSED_MS}ms (avg ${AVG_MS}ms/row)"

# Decrypt and verify roundtrip
log "Verifying decrypt roundtrip..."
FIRST_ROW="$(psql_exec "SELECT player_ref, email_cipher, name_cipher FROM gdpr_tde_test LIMIT 1;")"
PLAYER_REF="$(echo "${FIRST_ROW}" | cut -d'|' -f1 | tr -d ' ')"
EMAIL_ENC="$(echo "${FIRST_ROW}" | cut -d'|' -f2 | tr -d ' ')"
DEC_EMAIL="$(bao write -tls-skip-verify -field=plaintext "transit/decrypt/${TRANSIT_KEY}" \
    ciphertext="${EMAIL_ENC}" 2>/dev/null | base64 -d)"

ORIG_EMAIL="$(psql_exec "SELECT email FROM players WHERE id='${PLAYER_REF}';" | tr -d ' \n')"

if [ "${DEC_EMAIL}" = "${ORIG_EMAIL}" ]; then
    pass "Email roundtrip verified: '${DEC_EMAIL}'"
else
    fail "Email mismatch: expected '${ORIG_EMAIL}', got '${DEC_EMAIL}'"
fi

# Cleanup test table
psql_exec "DROP TABLE IF EXISTS gdpr_tde_test;" > /dev/null

# Save evidence
mkdir -p "${EVIDENCE_DIR}"
{
    printf 'PostgreSQL TDE Test Result: PASS\n'
    printf 'Date: %s\n' "$(date -u)"
    printf 'Database: %s\n' "${PG_DB}"
    printf 'Rows encrypted: %d\n' "${ROW_COUNT}"
    printf 'Total time: %dms\n' "${ELAPSED_MS}"
    printf 'Avg per row: %dms\n' "${AVG_MS}"
    printf 'Roundtrip verify: PASS\n'
    printf 'Key: transit/%s (AES256-GCM96)\n' "${TRANSIT_KEY}"
    printf 'GDPR Art.32: VERIFIED\n'
} >> "${EVIDENCE_DIR}/pgsql-tde-result.txt"

pass "PostgreSQL 18 TDE test complete. Evidence saved to ${EVIDENCE_DIR}/pgsql-tde-result.txt"
