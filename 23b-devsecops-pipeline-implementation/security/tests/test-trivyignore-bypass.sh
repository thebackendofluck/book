#!/usr/bin/env bash
# Companion code for "The Backend of Luck" - Chapter 23b, DevSecOps Pipeline Implementation.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# test-trivyignore-bypass.sh — Test suite for trivy-ignore-audit.sh
#
# Creates controlled .trivyignore fixtures and asserts correct exit codes.
# Does NOT require trivy to be installed (layer 3 / dynamic is skipped).
#
# Usage:
#   bash tests/test-trivyignore-bypass.sh
#
# Exit code: 0 if all tests pass, 1 if any fail.

set -uo pipefail

AUDIT_SCRIPT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/trivy-ignore-audit.sh"
PASS=0
FAIL=0
SKIP=0

red()   { printf '\033[0;31m[FAIL]\033[0m %s\n' "$*"; }
green() { printf '\033[0;32m[PASS]\033[0m %s\n' "$*"; }
info()  { printf '\033[0;34m[INFO]\033[0m %s\n' "$*"; }

# ── Test harness ──────────────────────────────────────────────────────────────

WORK_DIR="$(mktemp -d /tmp/trivy-audit-test-XXXXXX)"
trap 'rm -rf "$WORK_DIR"' EXIT

# Approved list used for all tests
APPROVED_LIST="${WORK_DIR}/approved-ignores.txt"
cat > "$APPROVED_LIST" << 'APPROVED'
CVE-2022-40897   2099-01-01   sec-team   SEC-100
CVE-2023-3817    2099-01-01   sec-team   SEC-101
APPROVED

run_test() {
    local name="$1"
    local expected_exit="$2"
    local ignore_file="${3:-}"
    local ignore_yaml="${4:-}"

    local env_overrides=(
        "APPROVED_FILE=${APPROVED_LIST}"
        "FAIL_ON_REVIEW=0"   # don't test git layer in unit tests
        "BASE_BRANCH=nonexistent-branch-xyz"  # git diff will produce no output
    )

    [[ -n "$ignore_file" ]] && env_overrides+=("IGNORE_FILE=${ignore_file}")
    [[ -n "$ignore_yaml" ]] && env_overrides+=("IGNORE_YAML=${ignore_yaml}")

    actual_exit=0
    # Run from WORK_DIR so auto-detect never picks up ignore files from other tests
    ( cd "$WORK_DIR" && env "${env_overrides[@]}" bash "$AUDIT_SCRIPT" ) > /dev/null 2>&1 \
        || actual_exit=$?

    if [[ "$actual_exit" -eq "$expected_exit" ]]; then
        green "${name} (exit ${expected_exit})"
        PASS=$(( PASS + 1 ))
    else
        red "${name} — expected exit ${expected_exit}, got ${actual_exit}"
        FAIL=$(( FAIL + 1 ))
        # Re-run with output for debugging
        echo "   ↓ Debug output:"
        ( cd "$WORK_DIR" && env "${env_overrides[@]}" bash "$AUDIT_SCRIPT" ) 2>&1 \
            | sed 's/^/   | /' || true
    fi
}

# ── Test 1: No ignore files → exit 0 ─────────────────────────────────────────

info "Test 1: No ignore files"
run_test "no ignore files → exit 0" 0 "" ""

# ── Test 2: Valid .trivyignore with expiry + approved CVE → exit 0 ────────────

info "Test 2: Valid .trivyignore — approved CVE with future expiry"
GOOD_IGNORE="${WORK_DIR}/trivyignore-good.txt"
cat > "$GOOD_IGNORE" << 'EOF'
# Accept the risk: libssl in base image, no fix available yet
CVE-2022-40897 exp:2099-01-01
CVE-2023-3817 exp:2099-06-01
EOF
run_test "valid .trivyignore (approved + expiry) → exit 0" 0 "$GOOD_IGNORE" ""

# ── Test 3: CVE without expiration date → exit 1 ─────────────────────────────

info "Test 3: .trivyignore with no expiry date"
NO_EXPIRY_IGNORE="${WORK_DIR}/trivyignore-no-expiry.txt"
cat > "$NO_EXPIRY_IGNORE" << 'EOF'
CVE-2022-40897
EOF
run_test "missing expiry → exit 1" 1 "$NO_EXPIRY_IGNORE" ""

# ── Test 4: Expired ignore entry still in file → exit 1 ──────────────────────

info "Test 4: Expired ignore entry still present"
EXPIRED_IGNORE="${WORK_DIR}/trivyignore-expired.txt"
cat > "$EXPIRED_IGNORE" << 'EOF'
CVE-2022-40897 exp:2020-01-01
EOF
run_test "expired entry still in file → exit 1" 1 "$EXPIRED_IGNORE" ""

# ── Test 5: Unapproved CVE (not in allowlist) → exit 1 ───────────────────────

info "Test 5: CVE not in approved allowlist"
UNAPPROVED_IGNORE="${WORK_DIR}/trivyignore-unapproved.txt"
cat > "$UNAPPROVED_IGNORE" << 'EOF'
CVE-2099-99999 exp:2099-01-01
EOF
run_test "unapproved CVE → exit 1" 1 "$UNAPPROVED_IGNORE" ""

# ── Test 6: Valid .trivyignore.yaml with expiry + statement → exit 0 ──────────

info "Test 6: Valid .trivyignore.yaml"
GOOD_YAML="${WORK_DIR}/trivyignore-good.yaml"
cat > "$GOOD_YAML" << 'EOF'
vulnerabilities:
  - id: CVE-2023-3817
    statement: "libssl1.1 in debian:bookworm base image — no fix available, CVSS 5.3 LOW, reviewed SEC-101"
    expired_at: 2099-06-01
    purls:
      - "pkg:deb/debian/libssl1.1"
EOF
run_test "valid .trivyignore.yaml → exit 0" 0 "" "$GOOD_YAML"

# ── Test 7: .trivyignore.yaml missing expired_at → exit 1 ────────────────────

info "Test 7: .trivyignore.yaml missing expired_at"
YAML_NO_EXPIRY="${WORK_DIR}/trivyignore-no-expiry.yaml"
cat > "$YAML_NO_EXPIRY" << 'EOF'
vulnerabilities:
  - id: CVE-2023-2650
    statement: "Risk accepted by TL"
EOF
run_test ".trivyignore.yaml missing expired_at → exit 1" 1 "" "$YAML_NO_EXPIRY"

# ── Test 8: .trivyignore.yaml with expired_at in the past → exit 1 ───────────

info "Test 8: .trivyignore.yaml expired_at in the past"
YAML_EXPIRED="${WORK_DIR}/trivyignore-expired.yaml"
cat > "$YAML_EXPIRED" << 'EOF'
vulnerabilities:
  - id: CVE-2023-3817
    statement: "Old acceptance"
    expired_at: 2020-01-01
EOF
run_test ".trivyignore.yaml expired → exit 1" 1 "" "$YAML_EXPIRED"

# ── Test 9: .trivyignore.yaml missing statement (warning only, not violation) ─

info "Test 9: .trivyignore.yaml missing statement — should warn, not fail"
YAML_NO_STMT="${WORK_DIR}/trivyignore-no-statement.yaml"
cat > "$YAML_NO_STMT" << 'EOF'
vulnerabilities:
  - id: CVE-2023-3817
    expired_at: 2099-06-01
    purls:
      - "pkg:deb/debian/libssl1.1"
EOF
# Warning only — exit 0 (statement is advisory, not a hard gate)
run_test ".trivyignore.yaml missing statement → warning, exit 0" 0 "" "$YAML_NO_STMT"

# ── Test 10: Mixed: good YAML + bad plain-text (no expiry) → exit 1 ───────────

info "Test 10: Mixed good YAML + bad plain-text"
run_test "good yaml + bad plain-text → exit 1" 1 "$NO_EXPIRY_IGNORE" "$GOOD_YAML"

# ── Summary ───────────────────────────────────────────────────────────────────

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Results: ${PASS} passed / ${FAIL} failed / ${SKIP} skipped"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

[[ "$FAIL" -eq 0 ]]
