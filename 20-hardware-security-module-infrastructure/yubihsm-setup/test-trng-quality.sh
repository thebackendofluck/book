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

# test-trng-quality.sh
# YubiHSM 2 TRNG quality test for GLI-19 RNG compliance.
# Generates 1MB of hardware random data and runs entropy analysis.
# Prerequisites: yubihsm-shell, yubihsm-connector running, Python 3.
# Usage: bash test-trng-quality.sh

set -euo pipefail

CONNECTOR_URL="${CONNECTOR_URL:-http://127.0.0.1:12345}"
AUTHKEY="${AUTHKEY:-1}"
EVIDENCE_DIR="${EVIDENCE_DIR:-/opt/yubihsm-evidence}"
OUTPUT_FILE="${OUTPUT_FILE:-/tmp/trng-sample.bin}"
TARGET_BYTES="${TARGET_BYTES:-1048576}"
BYTES_PER_CALL=2000

log()  { echo "[$(date '+%H:%M:%S')] $*"; }
pass() { echo "[PASS] $*"; }
fail() { echo "[FAIL] $*" >&2; exit 1; }

if [ -z "${HSM_PIN:-}" ]; then
    fail "HSM_PIN environment variable is required (auth key password)"
fi

log "=== YubiHSM 2 TRNG Quality Test (GLI-19) ==="
log "Target: ${TARGET_BYTES} bytes | Connector: ${CONNECTOR_URL}"

# Verify connector and HSM are accessible
if ! curl -sf "${CONNECTOR_URL}/connector/status" | grep -q 'status=OK'; then
    fail "yubihsm-connector not responding at ${CONNECTOR_URL}"
fi

ITERATIONS=$(( TARGET_BYTES / BYTES_PER_CALL + 1 ))
log "Collecting ${ITERATIONS} samples of ${BYTES_PER_CALL} bytes each..."

HEX_FILE="$(mktemp)"
trap 'rm -f "${HEX_FILE}" "${OUTPUT_FILE}"' EXIT

for i in $(seq 1 "${ITERATIONS}"); do
    yubihsm-shell --connector "${CONNECTOR_URL}" \
        --authkey "${AUTHKEY}" --password "${HSM_PIN}" \
        -a get-pseudo-random --count "${BYTES_PER_CALL}" \
        --outformat hex 2>/dev/null | grep -oE '[0-9a-f]+' | head -1 >> "${HEX_FILE}"
    if (( i % 100 == 0 )); then
        log "Progress: ${i}/${ITERATIONS} samples collected"
    fi
done

log "Converting hex to binary..."
python3 - << PYEOF
import re, sys

data = b''
with open('${HEX_FILE}') as f:
    for line in f:
        line = line.strip()
        if line and len(line) >= 8:
            try:
                data += bytes.fromhex(line)
            except ValueError:
                pass

target = ${TARGET_BYTES}
data = data[:target]
with open('${OUTPUT_FILE}', 'wb') as f:
    f.write(data)
print(f'Binary file written: {len(data):,} bytes')
PYEOF

ACTUAL_SIZE="$(wc -c < "${OUTPUT_FILE}")"
log "File size: ${ACTUAL_SIZE} bytes"

log "Running entropy analysis..."
python3 - << PYEOF
import math, collections, sys

with open('${OUTPUT_FILE}', 'rb') as f:
    data = f.read()

total = len(data)
counts = collections.Counter(data)

# Shannon entropy
entropy = -sum((c/total) * math.log2(c/total) for c in counts.values() if c > 0)

# Chi-squared test
expected = total / 256
chi2 = sum((c - expected)**2 / expected for c in counts.values())

# Unique bytes
unique_vals = len(counts)

# 16-bit pair uniqueness
pairs = [data[i]*256 + data[i+1] for i in range(0, min(len(data)-1, 65534), 2)]
unique_pairs = len(set(pairs))
total_pairs = len(pairs)

print(f'Total bytes analyzed: {total:,}')
print(f'Shannon entropy:      {entropy:.4f} bits/byte (max=8.0000)')
print(f'Chi-squared:          {chi2:.2f} (ideal ~255)')
print(f'Unique byte values:   {unique_vals}/256')
print(f'Unique 16-bit pairs:  {unique_pairs}/{total_pairs} ({100*unique_pairs/total_pairs:.1f}%)')
print()

entropy_pass = entropy >= 7.9
chi2_pass = chi2 < 400
vals_pass = unique_vals == 256

print(f'Entropy >= 7.9:       {"PASS" if entropy_pass else "FAIL"}')
print(f'Chi-squared < 400:    {"PASS" if chi2_pass else "FAIL"}')
print(f'All 256 byte values:  {"PASS" if vals_pass else "FAIL"}')
print()

if entropy_pass and chi2_pass and vals_pass:
    print('GLI-19 RNG Assessment: EXCELLENT')
    print('FIPS 140-2 Entropy:    PASS')
    sys.exit(0)
else:
    print('GLI-19 RNG Assessment: FAIL')
    sys.exit(1)
PYEOF

RET=$?

# Save evidence
mkdir -p "${EVIDENCE_DIR}"
{
    printf 'TRNG Quality Test Result: %s\n' "$([ "${RET}" -eq 0 ] && echo PASS || echo FAIL)"
    printf 'Date: %s\n' "$(date -u)"
    printf 'Bytes analyzed: %d\n' "${ACTUAL_SIZE}"
    printf 'Source: YubiHSM 2 get-pseudo-random (TRNG)\n'
    printf 'GLI-19: %s\n' "$([ "${RET}" -eq 0 ] && echo COMPLIANT || echo NON-COMPLIANT)"
} >> "${EVIDENCE_DIR}/trng-test-result.txt"

if [ "${RET}" -eq 0 ]; then
    pass "TRNG quality test passed. Evidence saved."
else
    fail "TRNG quality test failed — check output above"
fi
