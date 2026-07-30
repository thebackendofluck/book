#!/usr/bin/env bash
# Companion code for "The Backend of Luck" - Chapter 24, Security and Compliance.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# scan-python-deps.sh — Scan Python dependencies for known vulnerabilities
# Uses pip-audit (PEP 665 compliant) and safety for comprehensive coverage
# Usage: ./scan-python-deps.sh [requirements.txt] [--strict]
set -euo pipefail

REQUIREMENTS_FILE="${1:-requirements.txt}"
STRICT_MODE="${2:-}"
EXIT_CODE=0
REPORT_DIR="./security-reports/python"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)

echo "=== Python Dependency Security Scan ==="
echo "Requirements file: ${REQUIREMENTS_FILE}"
echo "Timestamp: ${TIMESTAMP}"
echo "Report directory: ${REPORT_DIR}"
echo ""

mkdir -p "${REPORT_DIR}"

# Verify requirements file exists
if [[ ! -f "${REQUIREMENTS_FILE}" ]]; then
    echo "ERROR: Requirements file not found: ${REQUIREMENTS_FILE}"
    echo "Usage: $0 [requirements.txt] [--strict]"
    exit 1
fi

# ─── Tool 1: pip-audit ───
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "Running pip-audit (PyPI Advisory Database)..."
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# Install pip-audit if not present
pip install --quiet pip-audit 2>/dev/null

# Run pip-audit against requirements file
pip-audit \
    --requirement "${REQUIREMENTS_FILE}" \
    --format json \
    --output "${REPORT_DIR}/pip-audit-${TIMESTAMP}.json" \
    --desc \
    --progress-spinner off \
    2>&1 | tee "${REPORT_DIR}/pip-audit-${TIMESTAMP}.log" || {
        echo "WARNING: pip-audit found vulnerabilities"
        EXIT_CODE=1
    }

# Also generate human-readable output
pip-audit \
    --requirement "${REQUIREMENTS_FILE}" \
    --format columns \
    --desc \
    --progress-spinner off \
    2>&1 | tee "${REPORT_DIR}/pip-audit-readable-${TIMESTAMP}.txt" || true

echo ""

# ─── Tool 2: safety ───
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "Running safety (Safety DB)..."
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# Install safety if not present
pip install --quiet safety 2>/dev/null

# Run safety check
safety check \
    --file "${REQUIREMENTS_FILE}" \
    --json \
    --output "${REPORT_DIR}/safety-${TIMESTAMP}.json" \
    2>&1 | tee "${REPORT_DIR}/safety-${TIMESTAMP}.log" || {
        echo "WARNING: safety found vulnerabilities"
        EXIT_CODE=1
    }

# Human-readable output
safety check \
    --file "${REQUIREMENTS_FILE}" \
    --full-report \
    2>&1 | tee "${REPORT_DIR}/safety-readable-${TIMESTAMP}.txt" || true

echo ""

# ─── Tool 3: Check for lock file integrity ───
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "Checking lock file integrity..."
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# Check if lock files exist and are consistent
for lockfile in requirements.lock Pipfile.lock poetry.lock; do
    if [[ -f "${lockfile}" ]]; then
        echo "Found lock file: ${lockfile}"
        # Verify hashes are present (supply chain protection)
        if grep -q "sha256" "${lockfile}" 2>/dev/null; then
            echo "  Hash verification: PRESENT"
        else
            echo "  Hash verification: MISSING — supply chain risk"
            EXIT_CODE=1
        fi
    fi
done

# Check for pinned versions in requirements file
echo ""
echo "Checking version pinning..."
UNPINNED=$(grep -cE '^[a-zA-Z].*[^=]$' "${REQUIREMENTS_FILE}" 2>/dev/null || echo "0")
if [[ "${UNPINNED}" -gt 0 ]]; then
    echo "WARNING: ${UNPINNED} unpinned dependencies found"
    grep -E '^[a-zA-Z].*[^=]$' "${REQUIREMENTS_FILE}" 2>/dev/null || true
    if [[ "${STRICT_MODE}" == "--strict" ]]; then
        EXIT_CODE=1
    fi
else
    echo "All dependencies are pinned"
fi

echo ""

# ─── Tool 4: SBOM Generation ───
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "Generating SBOM (CycloneDX format)..."
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

pip install --quiet cyclonedx-bom 2>/dev/null

cyclonedx-py requirements \
    --input-file "${REQUIREMENTS_FILE}" \
    --output-file "${REPORT_DIR}/sbom-${TIMESTAMP}.json" \
    --schema-version 1.5 \
    --format json \
    2>&1 || echo "SBOM generation requires cyclonedx-py >= 4.0"

echo ""

# ─── Summary ───
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "Scan Summary"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "Reports saved to: ${REPORT_DIR}/"
ls -la "${REPORT_DIR}/"*"${TIMESTAMP}"* 2>/dev/null
echo ""

if [[ "${EXIT_CODE}" -eq 0 ]]; then
    echo "RESULT: PASS — No known vulnerabilities found"
else
    echo "RESULT: FAIL — Vulnerabilities detected, review reports"
    if [[ "${STRICT_MODE}" == "--strict" ]]; then
        echo "Strict mode enabled — failing pipeline"
    fi
fi

exit "${EXIT_CODE}"
