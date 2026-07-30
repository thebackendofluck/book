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

# =============================================================================
# download-crs-rules.sh — Download OWASP Core Rule Set for Coraza WAF
# =============================================================================
# Downloads the specified CRS version, extracts the rules into ./crs-rules/,
# and copies the example setup config.
#
# Usage:
#   ./download-crs-rules.sh [version]
#   ./download-crs-rules.sh 4.7.0      # specific version
#   ./download-crs-rules.sh            # uses CRS_VERSION default below
# =============================================================================
set -euo pipefail

# Default CRS version — update when a new stable release is available.
# Check: https://github.com/coreruleset/coreruleset/releases
CRS_VERSION="${1:-4.7.0}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CRS_DIR="${SCRIPT_DIR}/crs-rules"
TMP_DIR="$(mktemp -d)"

cleanup() {
    rm -rf "${TMP_DIR}"
}
trap cleanup EXIT

log() { echo "[$(date '+%H:%M:%S')] $*"; }

# ---------------------------------------------------------------------------
# 1. Validate dependencies
# ---------------------------------------------------------------------------
for cmd in wget tar; do
    if ! command -v "${cmd}" &>/dev/null; then
        echo "ERROR: required command '${cmd}' not found" >&2
        exit 1
    fi
done

log "Downloading OWASP CRS v${CRS_VERSION}..."

# ---------------------------------------------------------------------------
# 2. Download the CRS archive
# ---------------------------------------------------------------------------
CRS_ARCHIVE="v${CRS_VERSION}.tar.gz"
CRS_URL="https://github.com/coreruleset/coreruleset/archive/refs/tags/${CRS_ARCHIVE}"

wget --quiet --show-progress \
    --tries=3 \
    --timeout=30 \
    -O "${TMP_DIR}/${CRS_ARCHIVE}" \
    "${CRS_URL}" || {
    echo "ERROR: Failed to download CRS from ${CRS_URL}" >&2
    echo "       Check your internet connection or verify the version exists." >&2
    exit 1
}

log "Download complete. Extracting..."

# ---------------------------------------------------------------------------
# 3. Extract and stage rules
# ---------------------------------------------------------------------------
tar xzf "${TMP_DIR}/${CRS_ARCHIVE}" -C "${TMP_DIR}"

CRS_EXTRACTED="${TMP_DIR}/coreruleset-${CRS_VERSION}"

if [[ ! -d "${CRS_EXTRACTED}" ]]; then
    # Some releases use a different directory name
    CRS_EXTRACTED="$(find "${TMP_DIR}" -maxdepth 1 -type d -name 'coreruleset-*' | head -1)"
    if [[ -z "${CRS_EXTRACTED}" ]]; then
        echo "ERROR: Unexpected archive structure — could not find coreruleset-* directory" >&2
        exit 1
    fi
fi

# ---------------------------------------------------------------------------
# 4. Install rules
# ---------------------------------------------------------------------------
mkdir -p "${CRS_DIR}"

# Remove old rules (keep crs-setup.conf, which we manage separately)
rm -f "${CRS_DIR}"/*.conf "${CRS_DIR}"/*.data 2>/dev/null || true

# Copy rule files
cp "${CRS_EXTRACTED}/rules/"*.conf "${CRS_DIR}/"
cp "${CRS_EXTRACTED}/rules/"*.data "${CRS_DIR}/" 2>/dev/null || true

RULE_COUNT="$(find "${CRS_DIR}" -name "*.conf" | wc -l | tr -d ' ')"
log "Installed ${RULE_COUNT} rule files to ${CRS_DIR}/"

# ---------------------------------------------------------------------------
# 5. Copy the example CRS setup config (for reference only)
# ---------------------------------------------------------------------------
# We do NOT overwrite crs-setup.conf — the one in this repo is the tuned
# iGaming version. Instead, we save the upstream example as a reference.
EXAMPLE_DEST="${SCRIPT_DIR}/crs-setup.conf.upstream-example"
cp "${CRS_EXTRACTED}/crs-setup.conf.example" "${EXAMPLE_DEST}"
log "Upstream example config saved to: ${EXAMPLE_DEST}"
log "(Do not replace crs-setup.conf — use the iGaming-tuned version in this directory)"

# ---------------------------------------------------------------------------
# 6. Verify rule integrity (spot check known rule IDs)
# ---------------------------------------------------------------------------
log "Verifying rule integrity..."

REQUIRED_RULES=(
    "941100"  # XSS
    "942100"  # SQL injection
    "930100"  # Path traversal
    "920100"  # Invalid request line
)

for rule_id in "${REQUIRED_RULES[@]}"; do
    if grep -rl "id:${rule_id}" "${CRS_DIR}" &>/dev/null; then
        log "  [OK] Rule ${rule_id} found"
    else
        echo "  [WARN] Rule ${rule_id} NOT found — CRS may be incomplete" >&2
    fi
done

# ---------------------------------------------------------------------------
# 7. Print summary
# ---------------------------------------------------------------------------
cat <<EOF

=============================================================================
OWASP CRS v${CRS_VERSION} installed successfully
=============================================================================
Rules directory : ${CRS_DIR}/
Rule files      : ${RULE_COUNT}
Next steps:
  1. Review crs-setup.conf — confirm paranoia level and exclusions
  2. Review coraza.conf — confirm platform-specific exclusions
  3. Run: ./test-coraza.sh (after starting the container)
  4. When ready: ./deploy-coraza.sh --env staging
=============================================================================
EOF
