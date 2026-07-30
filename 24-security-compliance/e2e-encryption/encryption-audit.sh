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

# encryption-audit.sh — Full encryption compliance audit
# Runs all encryption tests (transit + rest + deletion), runs the PII scanner,
# and generates a consolidated compliance evidence report.
#
# Usage:
#   ./encryption-audit.sh [--target HOST] [--report-dir /path/to/dir]
#   SSH usage: ssh -i ~/.ssh/id_ed25519 user@ops-host ./encryption-audit.sh
#
# Output:
#   <report-dir>/transit-encryption.log
#   <report-dir>/rest-encryption.log
#   <report-dir>/deletion-security.log
#   <report-dir>/pii-scan.json
#   <report-dir>/compliance-evidence.log   ← Master report
#
# Compliance: PCI DSS v4.0.1 Req.3/4/12.4.2; GDPR Art.32; GLI-33; ISO 27001:2022

set -euo pipefail

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET_HOST="${TARGET_HOST:-localhost}"
REPORT_DIR="${REPORT_DIR:-/opt/e2e-encryption-test-results}"
PG_HOST="${PG_HOST:-${TARGET_HOST}}"
PG_PORT="${PG_PORT:-5432}"
PG_USER="${PG_USER:-postgres}"
OVERALL_PASS=0
OVERALL_FAIL=0
OVERALL_WARN=0

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
log()    { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "${EVIDENCE_LOG}"; }
pass()   { log "  PASS  $*"; OVERALL_PASS=$((OVERALL_PASS + 1)); }
fail()   { log "  FAIL  $*"; OVERALL_FAIL=$((OVERALL_FAIL + 1)); }
warn()   { log "  WARN  $*"; OVERALL_WARN=$((OVERALL_WARN + 1)); }
section(){ log ""; log "======================================================================"; log "  $*"; log "======================================================================"; }

require_cmd() {
    if ! command -v "$1" >/dev/null 2>&1; then
        warn "Optional dependency '$1' not installed — some tests may be skipped"
        return 1
    fi
    return 0
}

count_in_log() {
    local log_file="$1"
    local pattern="$2"
    grep -c "${pattern}" "${log_file}" 2>/dev/null || echo "0"
}

# ---------------------------------------------------------------------------
# Environment detection
# ---------------------------------------------------------------------------
detect_environment() {
    section "Environment Detection"
    log "Hostname:       $(hostname 2>/dev/null || echo unknown)"
    log "OS:             $(uname -sr)"
    log "Kernel:         $(uname -r)"
    log "Date:           $(date)"
    log "Target host:    ${TARGET_HOST}"
    log "PG host:        ${PG_HOST}:${PG_PORT}"
    log "Script dir:     ${SCRIPT_DIR}"

    # Check for key tools
    local tools=(openssl curl psql python3 cryptsetup shred aws docker)
    for tool in "${tools[@]}"; do
        if command -v "${tool}" >/dev/null 2>&1; then
            local ver
            ver=$("${tool}" --version 2>&1 | head -1 || echo "installed")
            log "  TOOL  ${tool}: ${ver}"
        else
            log "  MISS  ${tool}: not installed"
        fi
    done

    # Check Python packages
    if command -v python3 >/dev/null 2>&1; then
        local py_packages=(psycopg2 cryptography)
        for pkg in "${py_packages[@]}"; do
            if python3 -c "import ${pkg}" 2>/dev/null; then
                log "  PKG   python3/${pkg}: installed"
            else
                log "  MISS  python3/${pkg}: not installed (pip install ${pkg}-binary)"
            fi
        done
    fi
}

# ---------------------------------------------------------------------------
# Run transit encryption tests
# ---------------------------------------------------------------------------
run_transit_tests() {
    section "Pillar 1: Encryption in Transit"
    local transit_log="${REPORT_DIR}/transit-encryption.log"

    if [ -f "${SCRIPT_DIR}/test-transit-encryption.sh" ]; then
        bash "${SCRIPT_DIR}/test-transit-encryption.sh" \
            --target "${TARGET_HOST}" \
            --report "${transit_log}" 2>&1 | tee -a "${EVIDENCE_LOG}" || true

        local t_pass t_fail t_warn
        t_pass=$(count_in_log "${transit_log}" "  PASS  ")
        t_fail=$(count_in_log "${transit_log}" "  FAIL  ")
        t_warn=$(count_in_log "${transit_log}" "  WARN  ")

        OVERALL_PASS=$((OVERALL_PASS + t_pass))
        OVERALL_FAIL=$((OVERALL_FAIL + t_fail))
        OVERALL_WARN=$((OVERALL_WARN + t_warn))

        log ""
        log "Transit tests: PASS=${t_pass} FAIL=${t_fail} WARN=${t_warn}"
        log "Full report:   ${transit_log}"
    else
        warn "test-transit-encryption.sh not found in ${SCRIPT_DIR}"
    fi
}

# ---------------------------------------------------------------------------
# Run at-rest encryption tests
# ---------------------------------------------------------------------------
run_rest_tests() {
    section "Pillar 2: Encryption at Rest"
    local rest_log="${REPORT_DIR}/rest-encryption.log"

    if [ -f "${SCRIPT_DIR}/test-rest-encryption.sh" ]; then
        bash "${SCRIPT_DIR}/test-rest-encryption.sh" \
            --target "${TARGET_HOST}" \
            --pg-host "${PG_HOST}" \
            --pg-user "${PG_USER}" \
            --report "${rest_log}" 2>&1 | tee -a "${EVIDENCE_LOG}" || true

        local r_pass r_fail r_warn
        r_pass=$(count_in_log "${rest_log}" "  PASS  ")
        r_fail=$(count_in_log "${rest_log}" "  FAIL  ")
        r_warn=$(count_in_log "${rest_log}" "  WARN  ")

        OVERALL_PASS=$((OVERALL_PASS + r_pass))
        OVERALL_FAIL=$((OVERALL_FAIL + r_fail))
        OVERALL_WARN=$((OVERALL_WARN + r_warn))

        log ""
        log "At-rest tests: PASS=${r_pass} FAIL=${r_fail} WARN=${r_warn}"
        log "Full report:   ${rest_log}"
    else
        warn "test-rest-encryption.sh not found in ${SCRIPT_DIR}"
    fi
}

# ---------------------------------------------------------------------------
# Run deletion security tests
# ---------------------------------------------------------------------------
run_deletion_tests() {
    section "Pillar 4: Secure Deletion"
    local del_log="${REPORT_DIR}/deletion-security.log"

    if [ -f "${SCRIPT_DIR}/test-deletion-security.sh" ]; then
        bash "${SCRIPT_DIR}/test-deletion-security.sh" \
            --target "${TARGET_HOST}" \
            --pg-host "${PG_HOST}" \
            --pg-user "${PG_USER}" \
            --report "${del_log}" 2>&1 | tee -a "${EVIDENCE_LOG}" || true

        local d_pass d_fail d_warn
        d_pass=$(count_in_log "${del_log}" "  PASS  ")
        d_fail=$(count_in_log "${del_log}" "  FAIL  ")
        d_warn=$(count_in_log "${del_log}" "  WARN  ")

        OVERALL_PASS=$((OVERALL_PASS + d_pass))
        OVERALL_FAIL=$((OVERALL_FAIL + d_fail))
        OVERALL_WARN=$((OVERALL_WARN + d_warn))

        log ""
        log "Deletion tests: PASS=${d_pass} FAIL=${d_fail} WARN=${d_warn}"
        log "Full report:    ${del_log}"
    else
        warn "test-deletion-security.sh not found in ${SCRIPT_DIR}"
    fi
}

# ---------------------------------------------------------------------------
# Run crypto-shredding demonstration
# ---------------------------------------------------------------------------
run_crypto_shred_demo() {
    section "Pillar 3/4: Crypto-Shredding Demonstration"

    if ! command -v python3 >/dev/null 2>&1; then
        warn "python3 not available — skipping crypto-shredding demo"
        return
    fi

    if [ -f "${SCRIPT_DIR}/demo-crypto-shredding.py" ]; then
        python3 "${SCRIPT_DIR}/demo-crypto-shredding.py" \
            --pg-host "${PG_HOST}" \
            --pg-port "${PG_PORT}" \
            --pg-user "${PG_USER}" \
            --pg-password "${PG_PASSWORD:-}" \
            --test-mode 2>&1 | tee -a "${EVIDENCE_LOG}" || {
            warn "Crypto-shredding demo failed (DB may not be available)"
        }
    else
        warn "demo-crypto-shredding.py not found"
    fi
}

# ---------------------------------------------------------------------------
# Run pseudonymisation demonstration
# ---------------------------------------------------------------------------
run_pseudonymisation_demo() {
    section "Pillar 4: GDPR Art.17 Pseudonymisation Demonstration"

    if ! command -v python3 >/dev/null 2>&1; then
        warn "python3 not available — skipping pseudonymisation demo"
        return
    fi

    if [ -f "${SCRIPT_DIR}/demo-pseudonymisation.py" ]; then
        python3 "${SCRIPT_DIR}/demo-pseudonymisation.py" \
            --pg-host "${PG_HOST}" \
            --pg-port "${PG_PORT}" \
            --pg-user "${PG_USER}" \
            --pg-password "${PG_PASSWORD:-}" 2>&1 | tee -a "${EVIDENCE_LOG}" || {
            warn "Pseudonymisation demo failed (DB may not be available)"
        }
    else
        warn "demo-pseudonymisation.py not found"
    fi
}

# ---------------------------------------------------------------------------
# Run PII scanner
# ---------------------------------------------------------------------------
run_pii_scanner() {
    section "Pillar 2/3: PII Scanner"

    if ! command -v python3 >/dev/null 2>&1; then
        warn "python3 not available — skipping PII scan"
        return
    fi

    local pii_json="${REPORT_DIR}/pii-scan.json"

    if [ -f "${SCRIPT_DIR}/pii-scanner.py" ]; then
        # Scan database + common log directories
        python3 "${SCRIPT_DIR}/pii-scanner.py" \
            --pg-host "${PG_HOST}" \
            --pg-port "${PG_PORT}" \
            --pg-user "${PG_USER}" \
            --pg-password "${PG_PASSWORD:-}" \
            --scan-db \
            --scan-logs \
            --output "${pii_json}" \
            --max-files 500 2>&1 | tee -a "${EVIDENCE_LOG}" || {
            # Non-zero exit can mean findings were found — not a script error
            true
        }

        if [ -f "${pii_json}" ]; then
            local critical_count
            critical_count=$(python3 -c "
import json, sys
with open('${pii_json}') as f:
    d = json.load(f)
print(d.get('by_severity', {}).get('CRITICAL', 0))
" 2>/dev/null || echo "0")

            if [ "${critical_count}" -gt 0 ]; then
                fail "PII scanner: ${critical_count} CRITICAL findings (unencrypted PAN or equivalent)"
                OVERALL_FAIL=$((OVERALL_FAIL + 1))
            else
                pass "PII scanner: no CRITICAL findings"
                OVERALL_PASS=$((OVERALL_PASS + 1))
            fi
        fi
    else
        warn "pii-scanner.py not found"
    fi
}

# ---------------------------------------------------------------------------
# System-level encryption checks
# ---------------------------------------------------------------------------
run_system_checks() {
    section "System-Level Encryption Checks"

    # Check kernel crypto modules
    if [ -r /proc/crypto ]; then
        local aes_modules
        aes_modules=$(grep -c "name.*:.*aes" /proc/crypto 2>/dev/null || echo "0")
        if [ "${aes_modules}" -gt 0 ]; then
            pass "Kernel AES crypto modules loaded (${aes_modules} variants)"
        fi

        if grep -q "aes-ni" /proc/crypto 2>/dev/null || \
           grep -q "aesni" /proc/cpuinfo 2>/dev/null; then
            pass "AES-NI hardware acceleration available (reduces TDE overhead)"
        else
            warn "AES-NI not detected — TDE may have higher CPU overhead"
        fi
    fi

    # Check for swap encryption
    if [ -f /proc/swaps ]; then
        local swap_size
        swap_size=$(awk 'NR>1{sum+=$3}END{print sum+0}' /proc/swaps)
        if [ "${swap_size}" -gt 0 ]; then
            # Check if swap is on encrypted device
            local swap_device
            swap_device=$(awk 'NR>1{print $1}' /proc/swaps | head -1)
            local swap_luks
            swap_luks=$(dmsetup table "${swap_device##*/}" 2>/dev/null | grep -c "crypt" || echo "0")
            if [ "${swap_luks}" -gt 0 ]; then
                pass "Swap is on encrypted device: ${swap_device}"
            else
                warn "Swap is active and may NOT be encrypted (${swap_device}) — key material may leak"
                warn "  Recommendation: disable swap for key management processes or use encrypted swap"
            fi
        else
            pass "No active swap (key material cannot leak via paging)"
        fi
    fi

    # Check memory overcommit (relevant for ZeroizeOnDrop guarantee)
    if [ -r /proc/sys/vm/overcommit_memory ]; then
        local overcommit
        overcommit=$(cat /proc/sys/vm/overcommit_memory)
        log "  INFO  vm.overcommit_memory: ${overcommit} (0=heuristic, 1=always, 2=strict)"
    fi

    # Check THP (Transparent Huge Pages) — can prevent mlock from working
    if [ -r /sys/kernel/mm/transparent_hugepage/enabled ]; then
        local thp
        thp=$(cat /sys/kernel/mm/transparent_hugepage/enabled)
        if echo "${thp}" | grep -q "\[never\]"; then
            pass "Transparent Huge Pages disabled (mlock works correctly for key processes)"
        else
            warn "Transparent Huge Pages enabled: ${thp} — verify mlock works for key processes"
        fi
    fi
}

# ---------------------------------------------------------------------------
# OpenBao / Vault connectivity check
# ---------------------------------------------------------------------------
run_vault_check() {
    section "Key Management: OpenBao / Vault Check"

    local vault_addr="${VAULT_ADDR:-http://openbao.svc.cluster.local:8200}"

    if require_cmd vault 2>/dev/null; then
        local vault_status
        vault_status=$(VAULT_ADDR="${vault_addr}" vault status -format=json 2>/dev/null || echo "{}")

        if echo "${vault_status}" | python3 -c "import sys,json; d=json.load(sys.stdin); exit(0 if not d.get('sealed',True) else 1)" 2>/dev/null; then
            pass "OpenBao/Vault is unsealed and operational"
            local ha_enabled
            ha_enabled=$(echo "${vault_status}" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('ha_enabled', False))" 2>/dev/null || echo "unknown")
            log "  INFO  HA enabled: ${ha_enabled}"
        elif echo "${vault_status}" | grep -q "sealed.*true"; then
            fail "OpenBao/Vault is SEALED — key operations will fail"
        else
            warn "OpenBao/Vault status unknown (${vault_addr} may not be reachable from this host)"
        fi
    else
        warn "vault CLI not available — OpenBao status not checked"
    fi
}

# ---------------------------------------------------------------------------
# Compliance evidence summary
# ---------------------------------------------------------------------------
generate_compliance_evidence() {
    section "Compliance Evidence Summary"

    log ""
    log "  Organisation:    iGaming Platform"
    log "  Audit date:      $(date)"
    log "  Auditor:         encryption-audit.sh v1.0"
    log "  Target system:   ${TARGET_HOST}"
    log ""
    log "  ┌─────────────────────────────────────────────────────────────┐"
    log "  │  Compliance Framework Coverage                              │"
    log "  ├─────────────────────────────────────────────────────────────┤"
    log "  │  PCI DSS v4.0.1  Req.3.5.1  Encryption at rest (PAN)       │"
    log "  │  PCI DSS v4.0.1  Req.4.2.1  Encryption in transit          │"
    log "  │  PCI DSS v4.0.1  Req.3.7    Key management                 │"
    log "  │  GDPR Art.32     Technical security measures                │"
    log "  │  GDPR Art.17     Right to erasure (crypto-shredding)        │"
    log "  │  GDPR Art.25     Privacy by design                          │"
    log "  │  GLI-33 Sec.6    Data encryption requirements               │"
    log "  │  ISO 27001:2022  A.8.24 Use of cryptography                 │"
    log "  │  ISO 27001:2022  A.8.10 Information deletion                │"
    log "  │  FATF Rec.11     AML record retention (5 years)             │"
    log "  └─────────────────────────────────────────────────────────────┘"
    log ""
    log "  Test Results:"
    log "    PASS:  ${OVERALL_PASS}"
    log "    WARN:  ${OVERALL_WARN}"
    log "    FAIL:  ${OVERALL_FAIL}"
    log ""

    if [ "${OVERALL_FAIL}" -gt 0 ]; then
        log "  AUDIT STATUS: FAIL"
        log ""
        log "  ${OVERALL_FAIL} test(s) failed. These represent compliance gaps that must"
        log "  be remediated before a formal PCI DSS or ISO 27001 audit."
        log "  Review the individual test logs in ${REPORT_DIR}/"
    elif [ "${OVERALL_WARN}" -gt 0 ]; then
        log "  AUDIT STATUS: CONDITIONAL PASS"
        log ""
        log "  ${OVERALL_WARN} warning(s) noted. No critical failures, but warnings"
        log "  should be reviewed and addressed before the next audit cycle."
    else
        log "  AUDIT STATUS: PASS"
        log ""
        log "  All encryption tests passed. This evidence log may be submitted"
        log "  as supporting documentation for PCI DSS Req.12.4.2 (periodic"
        log "  security controls testing) and ISO 27001:2022 audit reviews."
    fi

    log ""
    log "  Files:"
    log "    Transit test:    ${REPORT_DIR}/transit-encryption.log"
    log "    Rest test:       ${REPORT_DIR}/rest-encryption.log"
    log "    Deletion test:   ${REPORT_DIR}/deletion-security.log"
    log "    PII scan:        ${REPORT_DIR}/pii-scan.json"
    log "    Master report:   ${EVIDENCE_LOG}"
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
main() {
    while [ $# -gt 0 ]; do
        case "$1" in
            --target)     TARGET_HOST="$2"; PG_HOST="${PG_HOST:-$2}"; shift 2 ;;
            --report-dir) REPORT_DIR="$2"; shift 2 ;;
            --pg-host)    PG_HOST="$2";    shift 2 ;;
            --pg-user)    PG_USER="$2";    shift 2 ;;
            *) echo "Unknown option: $1"; exit 1 ;;
        esac
    done

    mkdir -p "${REPORT_DIR}"
    EVIDENCE_LOG="${REPORT_DIR}/compliance-evidence.log"
    : >"${EVIDENCE_LOG}"

    log "=== End-to-End Encryption Compliance Audit ==="
    log "Started:    $(date)"
    log "Host:       $(hostname)"
    log "Target:     ${TARGET_HOST}"
    log "Report dir: ${REPORT_DIR}"

    detect_environment
    run_system_checks
    run_vault_check
    run_transit_tests
    run_rest_tests
    run_deletion_tests
    run_crypto_shred_demo
    run_pseudonymisation_demo
    run_pii_scanner
    generate_compliance_evidence

    if [ "${OVERALL_FAIL}" -gt 0 ]; then
        return 1
    fi
    return 0
}

main "$@"
