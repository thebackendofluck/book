#!/bin/bash
# Companion code for "The Backend of Luck" - Chapter 27, Data Residency and Backup/Recovery.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# Verify no real secrets or internal infrastructure details leaked into
# the published scripts in this directory.
#
# Usage: bash verify-sanitization.sh
# Expected: zero matches for all patterns (exit 0)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FAILED=0

check_pattern() {
    local description="$1"
    local pattern="$2"
    local matches
    # Exclude this script itself to avoid self-referential false positives
    matches=$(grep -rn --include="*.sh" --include="*.py" --include="Dockerfile" \
        -E "${pattern}" "${SCRIPT_DIR}" 2>/dev/null \
        | grep -v "verify-sanitization.sh" || true)
    if [ -n "${matches}" ]; then
        echo "FAIL [${description}]: found matches:"
        echo "${matches}"
        FAILED=$((FAILED + 1))
    else
        echo "OK   [${description}]: no matches"
    fi
}

echo "=== Sanitization check: ${SCRIPT_DIR} ==="
echo ""

# Real API key prefixes (Wasabi keys start with various uppercase patterns — none should appear)
check_pattern "Wasabi/AWS access key literals" "AKIA[A-Z0-9]{16}|[A-Z0-9]{20}[[:space:]]*="

# Common secret token prefixes
check_pattern "GitLab tokens"     "glpat-"
check_pattern "GitHub tokens"     "gho_|ghp_|ghs_|ghr_"

# Internal IP ranges used by this infrastructure
check_pattern "Public server IP"  "203\.0\.113\."
check_pattern "LAN subnet 50.x"   "192\.168\.50\."
check_pattern "LAN subnet 1.x"    "192\.168\.1\."

# Internal hostnames
# Add your own internal hostnames here, one check_pattern per name.
check_pattern "Internal hostname" "\bops-host\b"

# Any inline passphrase / password assignments
check_pattern "Inline passphrase"  "(passphrase|password|secret)[[:space:]]*=[[:space:]]*['\"][^'\"$]"

# Hardcoded key-like strings (40+ char hex or base64 not inside a variable reference)
check_pattern "Hardcoded long secrets" "['\"][A-Za-z0-9+/]{40,}['\"]"

echo ""
if [ "${FAILED}" -eq 0 ]; then
    echo "All sanitization checks passed."
    exit 0
else
    echo "${FAILED} check(s) failed — review matches above before publishing."
    exit 1
fi
