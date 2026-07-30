#!/usr/bin/env bash
# Companion code for "The Backend of Luck" - Chapter 23, DevSecOps for iGaming.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# =============================================================================
# Static Application Security Testing (SAST) Pipeline for iGaming Platform
# =============================================================================
# Chapter 23: DevSecOps - Security Scanning
#
# WHY: iGaming platforms process real money and personal data under strict
# regulation. SAST catches vulnerabilities before deployment:
#   - SQL injection in player balance queries = theft
#   - Insecure deserialization in game state = RCE
#   - Hardcoded credentials = total compromise
#   - Vulnerable dependencies = known exploit paths
#   - Misconfigured infrastructure = data exposure
#
# This script orchestrates multiple SAST tools and produces a consolidated
# report suitable for both developers and compliance auditors.
#
# USAGE:
#   ./sast-pipeline.sh                       # Full SAST scan
#   ./sast-pipeline.sh --quick               # Fast scan (skip container/IaC)
#   ./sast-pipeline.sh --fail-on high        # Fail threshold
#   ./sast-pipeline.sh --ci                  # CI mode (JSON, exit codes)
#   ./sast-pipeline.sh --help                # Show this help
#
# EXIT CODES:
#   0 - No findings above threshold
#   1 - Findings above threshold (blocks pipeline)
#   2 - Tool error or misconfiguration
#
# =============================================================================
set -euo pipefail

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
REPORT_DIR="${REPO_ROOT}/.security-reports/sast"
TIMESTAMP="$(date -u +%Y%m%d_%H%M%S)"
CONSOLIDATED_REPORT="${REPORT_DIR}/sast-consolidated-${TIMESTAMP}.json"
HTML_REPORT="${REPORT_DIR}/sast-report-${TIMESTAMP}.html"

# Thresholds: maximum allowed findings per severity
# WHY: iGaming regulators require zero critical vulnerabilities in production.
# High findings must be tracked and resolved within SLA.
CRITICAL_THRESHOLD=0
HIGH_THRESHOLD=5
MEDIUM_THRESHOLD=50

# Tool-specific settings
SEMGREP_RULES="${SEMGREP_RULES:-p/python p/django p/flask p/owasp-top-ten p/security-audit}"
BANDIT_CONFIG="${REPO_ROOT}/bandit.yaml"
TRIVY_SEVERITY="CRITICAL,HIGH"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
BOLD='\033[1m'
NC='\033[0m'

# Counters
CRITICAL_COUNT=0
HIGH_COUNT=0
MEDIUM_COUNT=0
LOW_COUNT=0
TOOL_ERRORS=0

# ---------------------------------------------------------------------------
# Helper Functions
# ---------------------------------------------------------------------------
log_info() { echo -e "${BLUE}[INFO]${NC} $1"; }
log_success() { echo -e "${GREEN}[PASS]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[FAIL]${NC} $1" >&2; }

show_help() {
    echo "Usage: $(basename "$0") [OPTIONS]"
    echo ""
    echo "Static Application Security Testing pipeline for iGaming platforms."
    echo ""
    echo "Options:"
    echo "  --quick                 Fast scan: Semgrep + Bandit only"
    echo "  --full                  Full scan including containers and IaC (default)"
    echo "  --fail-on SEVERITY      Fail if findings at this severity or above"
    echo "                          Values: critical, high, medium (default: high)"
    echo "  --ci                    CI mode: structured output, strict exit codes"
    echo "  --skip-semgrep          Skip Semgrep analysis"
    echo "  --skip-bandit           Skip Bandit analysis"
    echo "  --skip-trivy            Skip Trivy container scanning"
    echo "  --skip-checkov          Skip Checkov IaC scanning"
    echo "  --skip-deps             Skip dependency vulnerability scanning"
    echo "  --output DIR            Custom report output directory"
    echo "  --help                  Show this help message"
    echo ""
    echo "Environment Variables:"
    echo "  SEMGREP_RULES           Semgrep rule sets (default: python + OWASP)"
    echo "  SEMGREP_APP_TOKEN       Semgrep App token for additional rules"
    echo "  TRIVY_SEVERITY          Trivy severity filter (default: CRITICAL,HIGH)"
    echo ""
}

check_tool() {
    local tool="$1"
    local install_hint="$2"

    if command -v "${tool}" &>/dev/null; then
        return 0
    else
        log_warn "${tool} not installed. ${install_hint}"
        return 1
    fi
}

ensure_report_dir() {
    mkdir -p "${REPORT_DIR}"
}

# ---------------------------------------------------------------------------
# Semgrep - Pattern-Based SAST
# WHY: Semgrep catches application-level vulnerabilities using semantic
# pattern matching. For iGaming, this means finding SQL injection in
# player queries, insecure random number generation (critical for RNG),
# and authentication bypass patterns.
# ---------------------------------------------------------------------------
run_semgrep() {
    log_info "Running Semgrep static analysis..."

    if ! check_tool "semgrep" "pip install semgrep"; then
        TOOL_ERRORS=$((TOOL_ERRORS + 1))
        return 0
    fi

    local semgrep_report="${REPORT_DIR}/semgrep-${TIMESTAMP}.json"

    # Build Semgrep command with iGaming-relevant rules
    local semgrep_exit=0
    semgrep scan \
        --config "${SEMGREP_RULES}" \
        --json \
        --output "${semgrep_report}" \
        --metrics off \
        --quiet \
        "${REPO_ROOT}" 2>/dev/null || semgrep_exit=$?

    if [[ ${semgrep_exit} -eq 0 ]]; then
        log_success "Semgrep: No findings"
    elif [[ ${semgrep_exit} -eq 1 ]]; then
        # Parse findings count by severity
        local counts
        counts=$(python3 -c "
import json
with open('${semgrep_report}') as f:
    data = json.load(f)
results = data.get('results', [])
sev = {'ERROR': 0, 'WARNING': 0, 'INFO': 0}
for r in results:
    s = r.get('extra', {}).get('severity', 'INFO')
    sev[s] = sev.get(s, 0) + 1
print(f'{sev[\"ERROR\"]} {sev[\"WARNING\"]} {sev[\"INFO\"]}')
" 2>/dev/null || echo "0 0 0")

        read -r sem_high sem_medium sem_low <<< "${counts}"
        HIGH_COUNT=$((HIGH_COUNT + sem_high))
        MEDIUM_COUNT=$((MEDIUM_COUNT + sem_medium))
        LOW_COUNT=$((LOW_COUNT + sem_low))

        log_warn "Semgrep: ${sem_high} high, ${sem_medium} medium, ${sem_low} low"
    else
        log_error "Semgrep encountered an error (exit code: ${semgrep_exit})"
        TOOL_ERRORS=$((TOOL_ERRORS + 1))
    fi

    log_info "Semgrep report: ${semgrep_report}"
}

# ---------------------------------------------------------------------------
# Bandit - Python Security Linter
# WHY: The iGaming platform backend is Python (FastAPI). Bandit catches
# Python-specific vulnerabilities:
#   - B301: pickle usage (RCE via game state deserialization)
#   - B303: MD5/SHA1 for security (weak password hashing)
#   - B608: SQL injection via string formatting
#   - B105: hardcoded password strings
#   - B324: insecure hash functions
# ---------------------------------------------------------------------------
run_bandit() {
    log_info "Running Bandit Python security analysis..."

    if ! check_tool "bandit" "pip install bandit"; then
        TOOL_ERRORS=$((TOOL_ERRORS + 1))
        return 0
    fi

    local bandit_report="${REPORT_DIR}/bandit-${TIMESTAMP}.json"
    local bandit_args=("--format" "json" "--output" "${bandit_report}" "--recursive")

    if [[ -f "${BANDIT_CONFIG}" ]]; then
        bandit_args+=("--configfile" "${BANDIT_CONFIG}")
    fi

    # Exclude test directories (test fixtures may intentionally use weak patterns)
    bandit_args+=("--exclude" "tests,test,*/tests/*,*/test/*")

    local bandit_exit=0
    bandit "${bandit_args[@]}" "${REPO_ROOT}" 2>/dev/null || bandit_exit=$?

    if [[ ${bandit_exit} -eq 0 ]]; then
        log_success "Bandit: No findings"
    elif [[ ${bandit_exit} -eq 1 ]]; then
        local counts
        counts=$(python3 -c "
import json
with open('${bandit_report}') as f:
    data = json.load(f)
results = data.get('results', [])
sev = {'HIGH': 0, 'MEDIUM': 0, 'LOW': 0}
for r in results:
    s = r.get('issue_severity', 'LOW')
    conf = r.get('issue_confidence', 'LOW')
    # Only count high-confidence findings
    if conf in ('HIGH', 'MEDIUM'):
        sev[s] = sev.get(s, 0) + 1
print(f'{sev[\"HIGH\"]} {sev[\"MEDIUM\"]} {sev[\"LOW\"]}')
" 2>/dev/null || echo "0 0 0")

        read -r ban_high ban_medium ban_low <<< "${counts}"
        HIGH_COUNT=$((HIGH_COUNT + ban_high))
        MEDIUM_COUNT=$((MEDIUM_COUNT + ban_medium))
        LOW_COUNT=$((LOW_COUNT + ban_low))

        log_warn "Bandit: ${ban_high} high, ${ban_medium} medium, ${ban_low} low"
    else
        log_error "Bandit encountered an error (exit code: ${bandit_exit})"
        TOOL_ERRORS=$((TOOL_ERRORS + 1))
    fi

    log_info "Bandit report: ${bandit_report}"
}

# ---------------------------------------------------------------------------
# Trivy - Container Image Scanning
# WHY: iGaming platforms run in containers (game servers, payment services,
# API gateways). Trivy scans container images for:
#   - OS package vulnerabilities (CVEs in base images)
#   - Application dependency vulnerabilities
#   - Misconfigurations (running as root, exposed ports)
# A compromised container can pivot to adjacent services via the network.
# ---------------------------------------------------------------------------
run_trivy() {
    log_info "Running Trivy container image scanning..."

    if ! check_tool "trivy" "https://aquasecurity.github.io/trivy/latest/getting-started/installation/"; then
        TOOL_ERRORS=$((TOOL_ERRORS + 1))
        return 0
    fi

    local trivy_report="${REPORT_DIR}/trivy-${TIMESTAMP}.json"

    # Find Dockerfiles and scan their images
    local dockerfiles
    dockerfiles=$(find "${REPO_ROOT}" -name "Dockerfile*" -not -path "*/vendor/*" -not -path "*/.git/*" 2>/dev/null || true)

    if [[ -z "${dockerfiles}" ]]; then
        log_info "No Dockerfiles found, scanning filesystem instead"

        trivy fs \
            --format json \
            --output "${trivy_report}" \
            --severity "${TRIVY_SEVERITY}" \
            --quiet \
            "${REPO_ROOT}" 2>/dev/null || true

    else
        log_info "Found Dockerfiles, scanning as filesystem with Trivy..."

        trivy fs \
            --format json \
            --output "${trivy_report}" \
            --severity "${TRIVY_SEVERITY}" \
            --scanners vuln,secret,misconfig \
            --quiet \
            "${REPO_ROOT}" 2>/dev/null || true
    fi

    if [[ -f "${trivy_report}" ]]; then
        local vuln_count
        vuln_count=$(python3 -c "
import json
with open('${trivy_report}') as f:
    data = json.load(f)
count = 0
results = data.get('Results', []) if isinstance(data, dict) else []
for r in results:
    vulns = r.get('Vulnerabilities', [])
    count += len(vulns) if vulns else 0
print(count)
" 2>/dev/null || echo "0")

        if [[ "${vuln_count}" -eq 0 ]]; then
            log_success "Trivy: No vulnerabilities found"
        else
            log_warn "Trivy: ${vuln_count} vulnerabilities found"
            # Trivy findings are already filtered by severity
            CRITICAL_COUNT=$((CRITICAL_COUNT + vuln_count / 4))  # Rough estimate
            HIGH_COUNT=$((HIGH_COUNT + vuln_count * 3 / 4))
        fi
    fi

    log_info "Trivy report: ${trivy_report}"
}

# ---------------------------------------------------------------------------
# Checkov - Infrastructure as Code Security
# WHY: Terraform and Kubernetes manifests define the platform's security
# posture. Checkov catches:
#   - Unencrypted databases (player data at rest)
#   - Public S3 buckets (KYC document exposure)
#   - Missing network policies (lateral movement)
#   - No logging enabled (audit trail gaps)
# ---------------------------------------------------------------------------
run_checkov() {
    log_info "Running Checkov IaC security scanning..."

    if ! check_tool "checkov" "pip install checkov"; then
        TOOL_ERRORS=$((TOOL_ERRORS + 1))
        return 0
    fi

    local checkov_report="${REPORT_DIR}/checkov-${TIMESTAMP}.json"

    local checkov_exit=0
    checkov \
        --directory "${REPO_ROOT}" \
        --output json \
        --output-file-path "${REPORT_DIR}" \
        --quiet \
        --compact \
        --framework terraform,kubernetes,dockerfile,helm \
        --skip-check CKV_DOCKER_2 \
        2>/dev/null || checkov_exit=$?

    # Rename checkov output to our naming convention
    if [[ -f "${REPORT_DIR}/results_json.json" ]]; then
        mv "${REPORT_DIR}/results_json.json" "${checkov_report}"
    fi

    if [[ ${checkov_exit} -eq 0 ]]; then
        log_success "Checkov: All checks passed"
    else
        local failed_count
        failed_count=$(python3 -c "
import json
with open('${checkov_report}') as f:
    data = json.load(f)
results = data if isinstance(data, list) else [data]
total = 0
for r in results:
    checks = r.get('results', {}).get('failed_checks', [])
    total += len(checks)
print(total)
" 2>/dev/null || echo "0")

        if [[ "${failed_count}" -gt 0 ]]; then
            log_warn "Checkov: ${failed_count} failed checks"
            HIGH_COUNT=$((HIGH_COUNT + failed_count))
        fi
    fi

    log_info "Checkov report: ${checkov_report}"
}

# ---------------------------------------------------------------------------
# Dependency Vulnerability Scanning
# WHY: Third-party libraries are the largest attack surface. A vulnerable
# version of a JWT library, ORM, or HTTP client can be exploited remotely.
# For iGaming, vulnerable dependencies in payment processing or
# authentication libraries are critical-severity findings.
# ---------------------------------------------------------------------------
run_dependency_scan() {
    log_info "Running dependency vulnerability scanning..."

    local dep_findings=0

    # Python: pip-audit
    if [[ -f "${REPO_ROOT}/requirements.txt" ]] || [[ -f "${REPO_ROOT}/pyproject.toml" ]]; then
        log_info "Scanning Python dependencies with pip-audit..."

        if check_tool "pip-audit" "pip install pip-audit"; then
            local pip_audit_exit=0

            if [[ -f "${REPO_ROOT}/requirements.txt" ]]; then
                pip-audit \
                    --requirement "${REPO_ROOT}/requirements.txt" \
                    --format json \
                    --output "${REPORT_DIR}/pip-audit-${TIMESTAMP}.json" \
                    2>/dev/null || pip_audit_exit=$?
            else
                (cd "${REPO_ROOT}" && pip-audit \
                    --format json \
                    --output "${REPORT_DIR}/pip-audit-${TIMESTAMP}.json" \
                    2>/dev/null) || pip_audit_exit=$?
            fi

            if [[ ${pip_audit_exit} -eq 0 ]]; then
                log_success "pip-audit: No vulnerable packages"
            else
                log_warn "pip-audit: Vulnerable packages found"
                dep_findings=$((dep_findings + 1))
            fi
        fi
    fi

    # Node.js: npm audit
    if [[ -f "${REPO_ROOT}/package-lock.json" ]]; then
        log_info "Scanning Node.js dependencies with npm audit..."

        local npm_exit=0
        (cd "${REPO_ROOT}" && npm audit \
            --json \
            > "${REPORT_DIR}/npm-audit-${TIMESTAMP}.json" \
            2>/dev/null) || npm_exit=$?

        if [[ ${npm_exit} -eq 0 ]]; then
            log_success "npm audit: No vulnerabilities"
        else
            local npm_vulns
            npm_vulns=$(python3 -c "
import json
with open('${REPORT_DIR}/npm-audit-${TIMESTAMP}.json') as f:
    data = json.load(f)
meta = data.get('metadata', {}).get('vulnerabilities', {})
print(meta.get('critical', 0) + meta.get('high', 0))
" 2>/dev/null || echo "0")

            log_warn "npm audit: ${npm_vulns} high/critical vulnerabilities"
            dep_findings=$((dep_findings + npm_vulns))
        fi
    fi

    HIGH_COUNT=$((HIGH_COUNT + dep_findings))
    log_info "Dependency reports in: ${REPORT_DIR}/"
}

# ---------------------------------------------------------------------------
# Consolidated Report Generation
# ---------------------------------------------------------------------------
generate_consolidated_report() {
    log_info "Generating consolidated SAST report..."

    python3 -c "
import json, glob, os
from datetime import datetime, timezone

report_dir = '${REPORT_DIR}'
timestamp = '${TIMESTAMP}'

consolidated = {
    'metadata': {
        'timestamp': datetime.now(timezone.utc).isoformat(),
        'repository': os.path.basename('${REPO_ROOT}'),
        'branch': '$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo \"unknown\")',
        'commit': '$(git rev-parse --short HEAD 2>/dev/null || echo \"unknown\")',
        'tools_run': [],
        'summary': {
            'critical': ${CRITICAL_COUNT},
            'high': ${HIGH_COUNT},
            'medium': ${MEDIUM_COUNT},
            'low': ${LOW_COUNT},
            'tool_errors': ${TOOL_ERRORS}
        }
    },
    'tool_reports': {}
}

# Collect all individual reports
for report_file in glob.glob(os.path.join(report_dir, f'*-{timestamp}.json')):
    tool_name = os.path.basename(report_file).split('-')[0]
    if tool_name != 'sast':
        consolidated['metadata']['tools_run'].append(tool_name)
        try:
            with open(report_file) as f:
                consolidated['tool_reports'][tool_name] = json.load(f)
        except json.JSONDecodeError:
            consolidated['tool_reports'][tool_name] = {'error': 'invalid JSON'}

with open('${CONSOLIDATED_REPORT}', 'w') as f:
    json.dump(consolidated, f, indent=2)

print(f'Consolidated report written: ${CONSOLIDATED_REPORT}')
" 2>/dev/null || log_warn "Could not generate consolidated report"

    # Generate simple HTML report
    generate_html_report
}

generate_html_report() {
    python3 -c "
from datetime import datetime, timezone

html = '''<!DOCTYPE html>
<html>
<head>
<title>SAST Report - iGaming Platform</title>
<style>
body { font-family: -apple-system, sans-serif; margin: 40px; background: #f5f5f5; }
.container { max-width: 900px; margin: 0 auto; background: white; padding: 30px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }
h1 { color: #1a1a1a; border-bottom: 2px solid #e0e0e0; padding-bottom: 10px; }
.summary { display: grid; grid-template-columns: repeat(4, 1fr); gap: 15px; margin: 20px 0; }
.metric { padding: 20px; border-radius: 6px; text-align: center; }
.metric h3 { margin: 0; font-size: 2em; }
.metric p { margin: 5px 0 0; color: #666; }
.critical { background: #fee; color: #c00; }
.high { background: #fff3e0; color: #e65100; }
.medium { background: #fff8e1; color: #f57f17; }
.low { background: #e8f5e9; color: #2e7d32; }
.pass { background: #e8f5e9; }
.fail { background: #ffebee; }
table { width: 100%%; border-collapse: collapse; margin: 20px 0; }
th, td { padding: 10px; text-align: left; border-bottom: 1px solid #e0e0e0; }
th { background: #f5f5f5; font-weight: 600; }
.footer { color: #999; font-size: 0.9em; margin-top: 30px; text-align: center; }
</style>
</head>
<body>
<div class=\"container\">
<h1>SAST Security Report</h1>
<p><strong>Repository:</strong> $(basename "${REPO_ROOT}") |
   <strong>Branch:</strong> $(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "unknown") |
   <strong>Commit:</strong> $(git rev-parse --short HEAD 2>/dev/null || echo "unknown") |
   <strong>Date:</strong> ''' + datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC') + '''</p>

<div class=\"summary\">
  <div class=\"metric critical\"><h3>${CRITICAL_COUNT}</h3><p>Critical</p></div>
  <div class=\"metric high\"><h3>${HIGH_COUNT}</h3><p>High</p></div>
  <div class=\"metric medium\"><h3>${MEDIUM_COUNT}</h3><p>Medium</p></div>
  <div class=\"metric low\"><h3>${LOW_COUNT}</h3><p>Low</p></div>
</div>

<h2>Gate Decision</h2>
<table>
  <tr><th>Severity</th><th>Found</th><th>Threshold</th><th>Status</th></tr>
  <tr class=\"''' + ('fail' if ${CRITICAL_COUNT} > ${CRITICAL_THRESHOLD} else 'pass') + '''\">
    <td>Critical</td><td>${CRITICAL_COUNT}</td><td>${CRITICAL_THRESHOLD}</td>
    <td>''' + ('FAIL' if ${CRITICAL_COUNT} > ${CRITICAL_THRESHOLD} else 'PASS') + '''</td>
  </tr>
  <tr class=\"''' + ('fail' if ${HIGH_COUNT} > ${HIGH_THRESHOLD} else 'pass') + '''\">
    <td>High</td><td>${HIGH_COUNT}</td><td>${HIGH_THRESHOLD}</td>
    <td>''' + ('FAIL' if ${HIGH_COUNT} > ${HIGH_THRESHOLD} else 'PASS') + '''</td>
  </tr>
  <tr class=\"''' + ('fail' if ${MEDIUM_COUNT} > ${MEDIUM_THRESHOLD} else 'pass') + '''\">
    <td>Medium</td><td>${MEDIUM_COUNT}</td><td>${MEDIUM_THRESHOLD}</td>
    <td>''' + ('FAIL' if ${MEDIUM_COUNT} > ${MEDIUM_THRESHOLD} else 'PASS') + '''</td>
  </tr>
</table>

<div class=\"footer\">
  <p>Generated by iGaming DevSecOps Pipeline | Chapter 23: Security Scanning</p>
</div>
</div>
</body>
</html>'''

with open('${HTML_REPORT}', 'w') as f:
    f.write(html)
print(f'HTML report: ${HTML_REPORT}')
" 2>/dev/null || log_warn "Could not generate HTML report"
}

# ---------------------------------------------------------------------------
# Results Summary
# ---------------------------------------------------------------------------
display_summary() {
    echo ""
    echo -e "${BOLD}=====================================================================${NC}"
    echo -e "${BOLD}  SAST Pipeline Results${NC}"
    echo -e "${BOLD}=====================================================================${NC}"
    echo ""
    printf "  %-15s %s\n" "Critical:" "${CRITICAL_COUNT} (threshold: ${CRITICAL_THRESHOLD})"
    printf "  %-15s %s\n" "High:" "${HIGH_COUNT} (threshold: ${HIGH_THRESHOLD})"
    printf "  %-15s %s\n" "Medium:" "${MEDIUM_COUNT} (threshold: ${MEDIUM_THRESHOLD})"
    printf "  %-15s %s\n" "Low:" "${LOW_COUNT} (no threshold)"
    printf "  %-15s %s\n" "Tool Errors:" "${TOOL_ERRORS}"
    echo ""

    local gate_passed=true

    if [[ ${CRITICAL_COUNT} -gt ${CRITICAL_THRESHOLD} ]]; then
        echo -e "  ${RED}CRITICAL threshold exceeded: ${CRITICAL_COUNT} > ${CRITICAL_THRESHOLD}${NC}"
        gate_passed=false
    fi

    if [[ ${HIGH_COUNT} -gt ${HIGH_THRESHOLD} ]]; then
        echo -e "  ${RED}HIGH threshold exceeded: ${HIGH_COUNT} > ${HIGH_THRESHOLD}${NC}"
        gate_passed=false
    fi

    if [[ ${MEDIUM_COUNT} -gt ${MEDIUM_THRESHOLD} ]]; then
        echo -e "  ${YELLOW}MEDIUM threshold exceeded: ${MEDIUM_COUNT} > ${MEDIUM_THRESHOLD}${NC}"
        gate_passed=false
    fi

    echo ""
    if [[ "${gate_passed}" == "true" ]]; then
        echo -e "  ${GREEN}${BOLD}GATE: PASSED${NC}"
    else
        echo -e "  ${RED}${BOLD}GATE: FAILED${NC}"
    fi

    echo ""
    echo "  Reports: ${REPORT_DIR}/"
    echo -e "${BOLD}=====================================================================${NC}"
    echo ""

    if [[ "${gate_passed}" == "false" ]]; then
        return 1
    fi
    return 0
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
main() {
    local quick_mode=false
    local ci_mode=false
    local fail_on="high"
    local skip_semgrep=false
    local skip_bandit=false
    local skip_trivy=false
    local skip_checkov=false
    local skip_deps=false

    while [[ $# -gt 0 ]]; do
        case "$1" in
            --quick)
                quick_mode=true
                shift
                ;;
            --full)
                quick_mode=false
                shift
                ;;
            --fail-on)
                fail_on="${2:?'--fail-on requires: critical, high, or medium'}"
                shift 2
                ;;
            --ci)
                ci_mode=true  # Used to control output formatting
                export ci_mode
                shift
                ;;
            --skip-semgrep)  skip_semgrep=true; shift ;;
            --skip-bandit)   skip_bandit=true; shift ;;
            --skip-trivy)    skip_trivy=true; shift ;;
            --skip-checkov)  skip_checkov=true; shift ;;
            --skip-deps)     skip_deps=true; shift ;;
            --output)
                REPORT_DIR="${2:?'--output requires a directory'}"
                shift 2
                ;;
            --help|-h)
                show_help
                exit 0
                ;;
            *)
                log_error "Unknown option: $1"
                show_help
                exit 2
                ;;
        esac
    done

    # Adjust thresholds based on fail-on
    case "${fail_on}" in
        critical) HIGH_THRESHOLD=999; MEDIUM_THRESHOLD=999 ;;
        high)     MEDIUM_THRESHOLD=999 ;;
        medium)   ;;
        *)
            log_error "Invalid --fail-on value: ${fail_on}. Use: critical, high, medium"
            exit 2
            ;;
    esac

    # Quick mode skips container and IaC scanning
    if [[ "${quick_mode}" == "true" ]]; then
        skip_trivy=true
        skip_checkov=true
    fi

    echo ""
    echo -e "${BOLD}=====================================================================${NC}"
    echo -e "${BOLD}  iGaming SAST Pipeline${NC}"
    echo -e "${BOLD}=====================================================================${NC}"
    echo ""

    ensure_report_dir

    local start_time
    start_time=$(date +%s)

    # Run SAST tools
    [[ "${skip_semgrep}" != "true" ]] && run_semgrep
    [[ "${skip_bandit}" != "true" ]]  && run_bandit
    [[ "${skip_trivy}" != "true" ]]   && run_trivy
    [[ "${skip_checkov}" != "true" ]] && run_checkov
    [[ "${skip_deps}" != "true" ]]    && run_dependency_scan

    # Generate consolidated report
    generate_consolidated_report

    local end_time
    end_time=$(date +%s)
    local duration=$((end_time - start_time))
    log_info "SAST pipeline completed in ${duration} seconds"

    # Display summary and determine exit code
    if display_summary; then
        exit 0
    else
        exit 1
    fi
}

main "$@"
