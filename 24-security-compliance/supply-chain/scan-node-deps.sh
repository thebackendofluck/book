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

# scan-node-deps.sh — Scan Node.js dependencies for known vulnerabilities
# Uses npm audit and optionally snyk for comprehensive coverage
# Usage: ./scan-node-deps.sh [project-dir] [--strict]
set -euo pipefail

PROJECT_DIR="${1:-.}"
STRICT_MODE="${2:-}"
EXIT_CODE=0
REPORT_DIR="${PROJECT_DIR}/security-reports/node"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)

echo "=== Node.js Dependency Security Scan ==="
echo "Project directory: ${PROJECT_DIR}"
echo "Timestamp: ${TIMESTAMP}"
echo "Report directory: ${REPORT_DIR}"
echo ""

mkdir -p "${REPORT_DIR}"

# Verify package.json exists
if [[ ! -f "${PROJECT_DIR}/package.json" ]]; then
    echo "ERROR: package.json not found in: ${PROJECT_DIR}"
    echo "Usage: $0 [project-dir] [--strict]"
    exit 1
fi

cd "${PROJECT_DIR}"

# ─── Tool 1: npm audit ───
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "Running npm audit..."
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# JSON report
npm audit --json > "${REPORT_DIR}/npm-audit-${TIMESTAMP}.json" 2>&1 || {
    echo "npm audit found vulnerabilities"
    EXIT_CODE=1
}

# Human-readable report
npm audit 2>&1 | tee "${REPORT_DIR}/npm-audit-readable-${TIMESTAMP}.txt" || true

# Count vulnerabilities by severity
echo ""
echo "Vulnerability summary:"
for severity in critical high moderate low; do
    COUNT=$(node -e "
        const data = require('./${REPORT_DIR}/npm-audit-${TIMESTAMP}.json');
        const vulns = data.metadata?.vulnerabilities || data.vulnerabilities || {};
        console.log(vulns['${severity}'] || 0);
    " 2>/dev/null || echo "N/A")
    echo "  ${severity}: ${COUNT}"
done

echo ""

# ─── Tool 2: snyk (if available) ───
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "Running snyk test (if installed)..."
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

if command -v snyk &>/dev/null; then
    # JSON report
    snyk test \
        --json \
        --severity-threshold=medium \
        > "${REPORT_DIR}/snyk-${TIMESTAMP}.json" 2>&1 || {
            echo "snyk found vulnerabilities"
            EXIT_CODE=1
        }

    # Human-readable report
    snyk test \
        --severity-threshold=medium \
        2>&1 | tee "${REPORT_DIR}/snyk-readable-${TIMESTAMP}.txt" || true

    # Also run snyk monitor to track in dashboard
    echo ""
    echo "Running snyk monitor (continuous monitoring)..."
    snyk monitor 2>&1 || echo "snyk monitor failed (authentication may be required)"
else
    echo "snyk not installed. Install with: npm install -g snyk"
    echo "Then authenticate: snyk auth"
    echo "Skipping snyk scan."
fi

echo ""

# ─── Tool 3: Lock file integrity ───
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "Checking lock file integrity..."
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# Check package-lock.json exists and has integrity hashes
if [[ -f "package-lock.json" ]]; then
    echo "Found: package-lock.json"
    INTEGRITY_COUNT=$(grep -c '"integrity"' package-lock.json 2>/dev/null || echo "0")
    TOTAL_DEPS=$(grep -c '"resolved"' package-lock.json 2>/dev/null || echo "0")
    echo "  Dependencies with integrity hashes: ${INTEGRITY_COUNT}/${TOTAL_DEPS}"

    if [[ "${INTEGRITY_COUNT}" -lt "${TOTAL_DEPS}" ]]; then
        echo "  WARNING: Some dependencies missing integrity hashes"
        if [[ "${STRICT_MODE}" == "--strict" ]]; then
            EXIT_CODE=1
        fi
    fi

    # Verify lock file is in sync with package.json
    echo "  Verifying lock file sync..."
    npm ls --json > /dev/null 2>&1 || {
        echo "  WARNING: package-lock.json may be out of sync with package.json"
        echo "  Run 'npm install' to synchronize"
    }
elif [[ -f "yarn.lock" ]]; then
    echo "Found: yarn.lock"
    INTEGRITY_COUNT=$(grep -c 'integrity' yarn.lock 2>/dev/null || echo "0")
    echo "  Integrity hashes found: ${INTEGRITY_COUNT}"
elif [[ -f "pnpm-lock.yaml" ]]; then
    echo "Found: pnpm-lock.yaml"
    echo "  pnpm uses content-addressable storage with built-in integrity"
else
    echo "WARNING: No lock file found — supply chain risk"
    echo "  Generate with: npm install --package-lock-only"
    EXIT_CODE=1
fi

echo ""

# ─── Tool 4: Check for known malicious packages ───
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "Checking for known malicious packages..."
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# Known malicious packages and typosquats relevant to iGaming
MALICIOUS_PATTERNS=(
    "event-stream"    # Compromised in 2018, crypto-stealing malware
    "flatmap-stream"  # Malicious dependency of event-stream
    "getcookies"      # Backdoor package
    "mailparser"      # Typosquat
    "crossenv"        # Typosquat of cross-env
    "babelcli"        # Typosquat of babel-cli
    "d3.js"           # Typosquat of d3
    "gruntcli"        # Typosquat of grunt-cli
    "http-proxy.js"   # Typosquat of http-proxy
    "jquery.js"       # Typosquat of jquery
    "mongose"         # Typosquat of mongoose
    "shadowsock"      # Typosquat of shadowsocks
)

FOUND_MALICIOUS=0
for pkg in "${MALICIOUS_PATTERNS[@]}"; do
    if grep -q "\"${pkg}\"" package-lock.json 2>/dev/null || \
       grep -q "\"${pkg}\"" package.json 2>/dev/null; then
        echo "CRITICAL: Found known malicious/typosquat package: ${pkg}"
        FOUND_MALICIOUS=1
        EXIT_CODE=1
    fi
done

if [[ "${FOUND_MALICIOUS}" -eq 0 ]]; then
    echo "No known malicious packages detected in dependency tree"
fi

echo ""

# ─── Tool 5: SBOM Generation ───
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "Generating SBOM (CycloneDX format)..."
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

if command -v cyclonedx-npm &>/dev/null; then
    cyclonedx-npm --output-file "${REPORT_DIR}/sbom-${TIMESTAMP}.json" 2>&1 || true
elif npx --yes @cyclonedx/cyclonedx-npm --help &>/dev/null 2>&1; then
    npx --yes @cyclonedx/cyclonedx-npm \
        --output-file "${REPORT_DIR}/sbom-${TIMESTAMP}.json" \
        2>&1 || echo "SBOM generation failed"
else
    echo "Install CycloneDX: npm install -g @cyclonedx/cyclonedx-npm"
fi

echo ""

# ─── Tool 6: License compliance ───
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "Checking license compliance..."
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

if command -v license-checker &>/dev/null || npx --yes license-checker --help &>/dev/null 2>&1; then
    npx --yes license-checker \
        --json \
        --out "${REPORT_DIR}/licenses-${TIMESTAMP}.json" \
        2>&1 || true

    # Check for problematic licenses in gambling context
    for license in "GPL-3.0" "AGPL-3.0" "SSPL" "BSL"; do
        COUNT=$(grep -c "\"${license}\"" "${REPORT_DIR}/licenses-${TIMESTAMP}.json" 2>/dev/null || echo "0")
        if [[ "${COUNT}" -gt 0 ]]; then
            echo "WARNING: ${COUNT} packages with ${license} license (review for compliance)"
        fi
    done
else
    echo "Install license-checker: npm install -g license-checker"
fi

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
    echo "RESULT: FAIL — Issues detected, review reports"
    if [[ "${STRICT_MODE}" == "--strict" ]]; then
        echo "Strict mode enabled — failing pipeline"
    fi
fi

exit "${EXIT_CODE}"
