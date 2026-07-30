#!/usr/bin/env bash
# Companion code for "The Backend of Luck" - Chapter 25, GLI-GSF Compliance Framework.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# shellcheck disable=SC2086
###############################################################################
# gts-dry-run.sh - Automated GTS (Game Testing System) Dry Run
# GLI-GSF Phase 4 - Pre-Assessment Vulnerability Scanning
#
# Runs vulnerability assessments across infrastructure, application, and
# network layers using industry-standard tools to simulate what an ISF
# (Independent Security Firm) would test during a GTS assessment.
#
# Scan layers:
#   1. Network scanning (Nmap) - open ports, service versions, OS detection
#   2. Web server scanning (Nikto) - misconfigurations, default files
#   3. Application scanning (OWASP ZAP) - DAST for web vulnerabilities
#   4. Vulnerability scanning (OpenVAS/GVM) - CVE-based assessment
#   5. TLS/SSL analysis (testssl.sh) - cipher suites, certificate validation
#
# GLI-GSF-4 Reference: Section 5.1 - Game Testing System Requirements
#   - Quarterly vulnerability assessments required
#   - All Critical/High findings must be remediated before ISF visit
#   - Evidence package required for each scan
#
# Usage:
#   ./gts-dry-run.sh --target casino.example.com
#   ./gts-dry-run.sh --target 10.0.1.0/24 --full-scan
#   ./gts-dry-run.sh --target casino.example.com --layer network
#   ./gts-dry-run.sh --target casino.example.com --layer application
#
# Requirements:
#   nmap, nikto, zap-cli (OWASP ZAP), openvas-cli (GVM), testssl.sh
#   (Script checks for each tool and skips unavailable scans)
###############################################################################

set -euo pipefail

VERSION="1.0.0"
TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
DATE_STAMP=$(date +"%Y%m%d-%H%M%S")

TARGET=""
LAYER="all"
FULL_SCAN=false
OUTPUT_DIR="./gts-dry-run-${DATE_STAMP}"
ZAP_PORT="${ZAP_PORT:-8090}"
ZAP_API_KEY="${ZAP_API_KEY:?set ZAP_API_KEY}"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; NC='\033[0m'
TOTAL_VULNS=0; CRITICAL=0; HIGH=0; MEDIUM=0; LOW=0

log_info() { echo -e "${CYAN}[INFO]${NC} $*"; }
log_pass() { echo -e "${GREEN}[PASS]${NC} $*"; }
log_fail() { echo -e "${RED}[FAIL]${NC} $*"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $*"; }

usage() {
    cat << 'EOF'
Usage: gts-dry-run.sh --target HOST [OPTIONS]

Required:
  --target HOST        Target host, IP, or CIDR range

Options:
  --layer LAYER        Scan layer: network, web, application, vuln, tls, all (default: all)
  --full-scan          Enable aggressive/thorough scan modes
  --output DIR         Output directory for reports
  --help               Show this help

Examples:
  ./gts-dry-run.sh --target casino.example.com
  ./gts-dry-run.sh --target 10.0.1.0/24 --layer network --full-scan
  ./gts-dry-run.sh --target api.casino.example.com --layer application
EOF
    exit 0
}

parse_args() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --target)    TARGET="$2"; shift 2 ;;
            --layer)     LAYER="$2"; shift 2 ;;
            --full-scan) FULL_SCAN=true; shift ;;
            --output)    OUTPUT_DIR="$2"; shift 2 ;;
            --help|-h)   usage ;;
            *) echo "Unknown: $1"; usage ;;
        esac
    done
    [[ -z "$TARGET" ]] && { echo "Error: --target required"; usage; }
}

check_tools() {
    log_info "Checking available scanning tools..."
    local tools=("nmap" "nikto" "zap-cli" "gvm-cli" "testssl.sh" "testssl")
    for tool in "${tools[@]}"; do
        if command -v "$tool" &>/dev/null; then
            log_pass "$tool available"
        else
            log_warn "$tool not found (scan will be skipped)"
        fi
    done
    echo ""
}

# ---------------------------------------------------------------------------
# Layer 1: Network Scanning (Nmap)
# ---------------------------------------------------------------------------
scan_network() {
    log_info "=== Layer 1: Network Scanning (Nmap) ==="

    if ! command -v nmap &>/dev/null; then
        log_warn "nmap not installed. Install: apt-get install nmap"
        return
    fi

    local nmap_opts="-sV -sC --open -oA ${OUTPUT_DIR}/nmap-scan"
    if [[ "$FULL_SCAN" == true ]]; then
        nmap_opts="-sV -sC -O -A --open -p- -oA ${OUTPUT_DIR}/nmap-full"
    fi

    log_info "Running: nmap ${nmap_opts} ${TARGET}"
    nmap ${nmap_opts} "${TARGET}" 2>&1 | tee "${OUTPUT_DIR}/nmap-output.txt"

    # Parse results for gambling-critical services
    log_info "Checking for gambling-critical service exposure..."
    local critical_ports=(
        "5432:PostgreSQL" "3306:MySQL" "27017:MongoDB" "6379:Redis"
        "9200:Elasticsearch" "5672:RabbitMQ" "2181:Zookeeper"
        "8080:Management" "9090:Prometheus" "3000:Grafana"
    )

    for entry in "${critical_ports[@]}"; do
        local port="${entry%%:*}"
        local service="${entry##*:}"
        if grep -q "^${port}/tcp.*open" "${OUTPUT_DIR}/nmap-output.txt" 2>/dev/null; then
            log_fail "CRITICAL: ${service} (port ${port}) exposed externally"
            ((CRITICAL++)); ((TOTAL_VULNS++))
        fi
    done

    # Check for RNG service exposure (gambling-specific)
    if grep -qi "rng\|random\|entropy" "${OUTPUT_DIR}/nmap-output.txt" 2>/dev/null; then
        log_fail "CRITICAL: RNG-related service detected on external interface"
        ((CRITICAL++)); ((TOTAL_VULNS++))
    fi

    log_pass "Network scan complete: ${OUTPUT_DIR}/nmap-scan.*"
}

# ---------------------------------------------------------------------------
# Layer 2: Web Server Scanning (Nikto)
# ---------------------------------------------------------------------------
scan_web_server() {
    log_info "=== Layer 2: Web Server Scanning (Nikto) ==="

    if ! command -v nikto &>/dev/null; then
        log_warn "nikto not installed. Install: apt-get install nikto"
        return
    fi

    local nikto_opts="-h ${TARGET} -output ${OUTPUT_DIR}/nikto-report.html -Format html"
    if [[ "$FULL_SCAN" == true ]]; then
        nikto_opts="-h ${TARGET} -C all -output ${OUTPUT_DIR}/nikto-report.html -Format html"
    fi

    log_info "Running: nikto ${nikto_opts}"
    nikto ${nikto_opts} 2>&1 | tee "${OUTPUT_DIR}/nikto-output.txt" || true

    # Count findings
    local findings
    findings=$(grep -c "+" "${OUTPUT_DIR}/nikto-output.txt" 2>/dev/null || echo "0")
    log_info "Nikto found ${findings} items to review"
    ((TOTAL_VULNS += findings))

    # Check gambling-specific misconfigurations
    if grep -qi "TRACE\|TRACK" "${OUTPUT_DIR}/nikto-output.txt" 2>/dev/null; then
        log_fail "HTTP TRACE/TRACK enabled (session hijacking risk)"
        ((HIGH++))
    fi
    if grep -qi "directory listing\|index of" "${OUTPUT_DIR}/nikto-output.txt" 2>/dev/null; then
        log_fail "Directory listing enabled"
        ((MEDIUM++))
    fi

    log_pass "Web scan complete: ${OUTPUT_DIR}/nikto-report.html"
}

# ---------------------------------------------------------------------------
# Layer 3: Application Scanning (OWASP ZAP)
# ---------------------------------------------------------------------------
scan_application() {
    log_info "=== Layer 3: Application Scanning (OWASP ZAP) ==="

    if ! command -v zap-cli &>/dev/null; then
        log_warn "zap-cli not installed. Install: pip install python-owasp-zap-v2.4"
        log_info "Alternative: docker run -t owasp/zap2docker-stable zap-baseline.py -t https://${TARGET}"
        return
    fi

    log_info "Starting ZAP scan against https://${TARGET}"

    # Spider the target
    zap-cli --zap-url http://localhost:${ZAP_PORT} --api-key "${ZAP_API_KEY}" \
        spider "https://${TARGET}" 2>&1 || true

    # Active scan
    local scan_type="quick-scan"
    [[ "$FULL_SCAN" == true ]] && scan_type="active-scan"

    zap-cli --zap-url http://localhost:${ZAP_PORT} --api-key "${ZAP_API_KEY}" \
        ${scan_type} "https://${TARGET}" 2>&1 || true

    # Export report
    zap-cli --zap-url http://localhost:${ZAP_PORT} --api-key "${ZAP_API_KEY}" \
        report -o "${OUTPUT_DIR}/zap-report.html" -f html 2>&1 || true

    # Get alerts summary
    zap-cli --zap-url http://localhost:${ZAP_PORT} --api-key "${ZAP_API_KEY}" \
        alerts 2>&1 | tee "${OUTPUT_DIR}/zap-alerts.txt" || true

    local zap_high
    zap_high=$(grep -ci "high" "${OUTPUT_DIR}/zap-alerts.txt" 2>/dev/null || echo "0")
    local zap_medium
    zap_medium=$(grep -ci "medium" "${OUTPUT_DIR}/zap-alerts.txt" 2>/dev/null || echo "0")

    ((HIGH += zap_high)); ((MEDIUM += zap_medium))
    ((TOTAL_VULNS += zap_high + zap_medium))

    log_pass "Application scan complete: ${OUTPUT_DIR}/zap-report.html"
}

# ---------------------------------------------------------------------------
# Layer 4: Vulnerability Scanning (OpenVAS/GVM)
# ---------------------------------------------------------------------------
scan_vulnerabilities() {
    log_info "=== Layer 4: Vulnerability Scanning (OpenVAS/GVM) ==="

    if ! command -v gvm-cli &>/dev/null; then
        log_warn "gvm-cli not installed."
        log_info "Alternative: Run OpenVAS via Docker:"
        log_info "  docker run -d -p 443:443 greenbone/openvas-scanner"
        return
    fi

    log_info "Running OpenVAS scan against ${TARGET}"
    log_info "(This may take 30-60 minutes for a full scan)"

    # In production, this would create a task and wait for completion
    # Simplified for dry-run demonstration
    log_info "OpenVAS integration requires running GVM daemon"
    log_info "See: https://greenbone.github.io/docs/latest/"

    log_pass "Vulnerability scan placeholder complete"
}

# ---------------------------------------------------------------------------
# Layer 5: TLS/SSL Analysis
# ---------------------------------------------------------------------------
scan_tls() {
    log_info "=== Layer 5: TLS/SSL Analysis ==="

    local testssl_cmd=""
    if command -v testssl.sh &>/dev/null; then
        testssl_cmd="testssl.sh"
    elif command -v testssl &>/dev/null; then
        testssl_cmd="testssl"
    else
        log_warn "testssl.sh not installed. Install from: https://testssl.sh/"
        # Fallback to openssl
        if command -v openssl &>/dev/null; then
            log_info "Using openssl for basic TLS check..."
            local domain
            domain=$(echo "${TARGET}" | sed 's|https\?://||' | cut -d/ -f1)

            echo | openssl s_client -connect "${domain}:443" -servername "${domain}" \
                2>/dev/null > "${OUTPUT_DIR}/openssl-output.txt" || true

            # Check TLS version
            if grep -q "TLSv1.3" "${OUTPUT_DIR}/openssl-output.txt"; then
                log_pass "TLS 1.3 supported"
            elif grep -q "TLSv1.2" "${OUTPUT_DIR}/openssl-output.txt"; then
                log_pass "TLS 1.2 supported"
            else
                log_fail "Outdated TLS version"; ((HIGH++)); ((TOTAL_VULNS++))
            fi

            # Check certificate expiry
            echo | openssl s_client -connect "${domain}:443" -servername "${domain}" \
                2>/dev/null | openssl x509 -noout -dates 2>/dev/null \
                > "${OUTPUT_DIR}/cert-dates.txt" || true

            if [[ -s "${OUTPUT_DIR}/cert-dates.txt" ]]; then
                log_pass "Certificate details saved"
            fi
        fi
        return
    fi

    log_info "Running: ${testssl_cmd} --jsonfile ${OUTPUT_DIR}/testssl.json ${TARGET}"
    ${testssl_cmd} --jsonfile "${OUTPUT_DIR}/testssl.json" \
        --htmlfile "${OUTPUT_DIR}/testssl.html" \
        "${TARGET}" 2>&1 | tee "${OUTPUT_DIR}/testssl-output.txt" || true

    # Check for critical TLS issues
    if grep -qi "VULNERABLE\|NOT ok\|WEAK" "${OUTPUT_DIR}/testssl-output.txt" 2>/dev/null; then
        local tls_issues
        tls_issues=$(grep -ci "VULNERABLE\|NOT ok" "${OUTPUT_DIR}/testssl-output.txt" 2>/dev/null || echo "0")
        log_fail "${tls_issues} TLS/SSL issues found"
        ((HIGH += tls_issues)); ((TOTAL_VULNS += tls_issues))
    else
        log_pass "No critical TLS issues detected"
    fi
}

# ---------------------------------------------------------------------------
# Report Generation
# ---------------------------------------------------------------------------
generate_report() {
    log_info "=== Generating GTS Dry Run Report ==="

    cat > "${OUTPUT_DIR}/gts-dry-run-summary.json" << REPORT
{
  "document_type": "GTS Dry Run Report",
  "gli_gsf_reference": "GLI-GSF-4, Section 5.1",
  "target": "${TARGET}",
  "scan_date": "${TIMESTAMP}",
  "scan_type": "${LAYER}",
  "full_scan": ${FULL_SCAN},
  "summary": {
    "total_findings": ${TOTAL_VULNS},
    "critical": ${CRITICAL},
    "high": ${HIGH},
    "medium": ${MEDIUM},
    "low": ${LOW}
  },
  "remediation_sla": {
    "critical": "24 hours",
    "high": "7 days",
    "medium": "30 days",
    "low": "Next quarterly cycle"
  },
  "isf_readiness": "$([ $((CRITICAL + HIGH)) -eq 0 ] && echo 'READY' || echo 'NOT READY - remediate Critical/High findings first')",
  "tools_used": {
    "nmap": $(command -v nmap &>/dev/null && echo true || echo false),
    "nikto": $(command -v nikto &>/dev/null && echo true || echo false),
    "zap": $(command -v zap-cli &>/dev/null && echo true || echo false),
    "openvas": $(command -v gvm-cli &>/dev/null && echo true || echo false),
    "testssl": $(command -v testssl.sh &>/dev/null || command -v testssl &>/dev/null && echo true || echo false)
  }
}
REPORT

    echo ""
    echo "================================================================"
    echo "  GTS DRY RUN SUMMARY"
    echo "================================================================"
    echo ""
    echo "  Target:     ${TARGET}"
    echo "  Date:       ${TIMESTAMP}"
    echo ""
    echo -e "  Critical:   ${RED}${CRITICAL}${NC}"
    echo -e "  High:       ${RED}${HIGH}${NC}"
    echo -e "  Medium:     ${YELLOW}${MEDIUM}${NC}"
    echo -e "  Low:        ${GREEN}${LOW}${NC}"
    echo "  Total:      ${TOTAL_VULNS}"
    echo ""

    if [[ $((CRITICAL + HIGH)) -gt 0 ]]; then
        echo -e "  ISF Ready:  ${RED}NO${NC} - Remediate ${CRITICAL} Critical and ${HIGH} High findings"
    else
        echo -e "  ISF Ready:  ${GREEN}YES${NC}"
    fi

    echo ""
    echo "  Reports:    ${OUTPUT_DIR}/"
    echo "================================================================"
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
main() {
    parse_args "$@"
    mkdir -p "$OUTPUT_DIR"

    echo ""
    echo "================================================================"
    echo "  GLI-GSF GTS Dry Run Scanner v${VERSION}"
    echo "  Target: ${TARGET}"
    echo "  Layer:  ${LAYER}"
    echo "  Mode:   $([ "$FULL_SCAN" == true ] && echo 'Full' || echo 'Standard')"
    echo "================================================================"
    echo ""

    check_tools

    case "$LAYER" in
        network)     scan_network ;;
        web)         scan_web_server ;;
        application) scan_application ;;
        vuln)        scan_vulnerabilities ;;
        tls)         scan_tls ;;
        all)
            scan_network
            scan_web_server
            scan_application
            scan_vulnerabilities
            scan_tls
            ;;
        *) echo "Unknown layer: ${LAYER}"; usage ;;
    esac

    generate_report
}

main "$@"
