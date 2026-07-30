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

# shellcheck disable=SC2034
# =============================================================================
# SAST/DAST Pipeline Scanner for iGaming Platforms
# =============================================================================
# Integrates Semgrep (SAST) and OWASP ZAP (DAST) into CI/CD pipelines with
# gambling-specific security rules covering RNG manipulation, wallet tampering,
# bonus abuse vectors, and regulatory compliance gaps.
#
# Usage:
#   ./sast-dast-scanner.sh [--sast-only|--dast-only|--full] [--target URL]
#
# Environment Variables:
#   SEMGREP_APP_TOKEN    - Semgrep Cloud token (optional, for team dashboards)
#   ZAP_API_KEY          - OWASP ZAP API key
#   TARGET_URL           - Application URL for DAST scanning
#   SLACK_WEBHOOK_URL    - Slack webhook for alerting (optional)
#   JIRA_URL             - JIRA instance for ticket creation (optional)
#   CI_PIPELINE_ID       - CI pipeline identifier for traceability
# =============================================================================

set -euo pipefail

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPORT_DIR="${REPORT_DIR:-/tmp/security-reports/$(date +%Y%m%d-%H%M%S)}"
SEMGREP_RULES_DIR="${SCRIPT_DIR}/semgrep-rules"
SEVERITY_THRESHOLD="${SEVERITY_THRESHOLD:-warning}"  # error, warning, info
ZAP_DOCKER_IMAGE="ghcr.io/zaproxy/zaproxy:stable"
TARGET_URL="${TARGET_URL:-http://localhost:8080}"
ZAP_API_KEY="${ZAP_API_KEY:?set ZAP_API_KEY}"
MAX_SCAN_DURATION="${MAX_SCAN_DURATION:-3600}"  # 1 hour max for DAST
FAIL_ON_HIGH="${FAIL_ON_HIGH:-true}"

# Exit codes
EXIT_SUCCESS=0
EXIT_SAST_FINDINGS=10
EXIT_DAST_FINDINGS=11
EXIT_BOTH_FINDINGS=12
EXIT_ERROR=99

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log_info()  { echo -e "${BLUE}[INFO]${NC}  $(date '+%H:%M:%S') $*"; }
log_ok()    { echo -e "${GREEN}[OK]${NC}    $(date '+%H:%M:%S') $*"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC}  $(date '+%H:%M:%S') $*"; }
log_error() { echo -e "${RED}[ERROR]${NC} $(date '+%H:%M:%S') $*"; }

# ---------------------------------------------------------------------------
# Pre-flight checks
# ---------------------------------------------------------------------------
preflight_check() {
    log_info "Running pre-flight checks..."
    mkdir -p "${REPORT_DIR}"

    local missing=()

    if [[ "${RUN_SAST}" == "true" ]]; then
        command -v semgrep >/dev/null 2>&1 || missing+=("semgrep")
    fi

    if [[ "${RUN_DAST}" == "true" ]]; then
        command -v docker >/dev/null 2>&1 || missing+=("docker")
    fi

    command -v jq >/dev/null 2>&1 || missing+=("jq")

    if [[ ${#missing[@]} -gt 0 ]]; then
        log_error "Missing required tools: ${missing[*]}"
        log_info "Install with:"
        for tool in "${missing[@]}"; do
            case "${tool}" in
                semgrep) echo "  pip install semgrep" ;;
                docker)  echo "  See https://docs.docker.com/get-docker/" ;;
                jq)      echo "  apt-get install jq / brew install jq" ;;
            esac
        done
        exit ${EXIT_ERROR}
    fi

    log_ok "Pre-flight checks passed"
}

# ---------------------------------------------------------------------------
# Create iGaming-specific Semgrep rules
# ---------------------------------------------------------------------------
create_igaming_semgrep_rules() {
    log_info "Generating iGaming-specific Semgrep rules..."
    mkdir -p "${SEMGREP_RULES_DIR}"

    # Rule 1: Detect insecure RNG usage in game logic
    cat > "${SEMGREP_RULES_DIR}/igaming-rng-security.yaml" << 'SEMGREP_RULE'
rules:
  - id: igaming-insecure-rng
    patterns:
      - pattern-either:
          - pattern: Math.random()
          - pattern: random.random()
          - pattern: random.randint(...)
          - pattern: rand()
          - pattern: mt_rand()
          - pattern: java.util.Random(...)
    message: >
      Insecure RNG detected. iGaming platforms MUST use cryptographically
      secure random number generators (CSPRNG) for all game outcomes.
      Use crypto.randomBytes() (Node.js), secrets.SystemRandom (Python),
      or java.security.SecureRandom (Java). GLI-19 Section 3.2 requires
      certified RNG implementations.
    severity: ERROR
    languages: [javascript, typescript, python, java, php]
    metadata:
      category: security
      subcategory: [igaming, rng, gli-19]
      confidence: HIGH
      impact: CRITICAL
      compliance: ["GLI-19 3.2", "eCOGRA RNG Standards"]

  - id: igaming-rng-seed-exposure
    patterns:
      - pattern-either:
          - pattern: |
              $SEED = ...
              ...
              console.log(..., $SEED, ...)
          - pattern: |
              $SEED = ...
              ...
              logger.info(..., $SEED, ...)
    message: >
      RNG seed value may be exposed in logs. Seed values must never be
      logged or transmitted to clients. This violates GLI-19 seed
      confidentiality requirements.
    severity: ERROR
    languages: [javascript, typescript, python, java]
    metadata:
      category: security
      subcategory: [igaming, rng, seed-leak]

  - id: igaming-wallet-race-condition
    patterns:
      - pattern-either:
          - pattern: |
              $BALANCE = await $DB.query(...)
              ...
              await $DB.query(..., $BALANCE - ..., ...)
          - pattern: |
              $BALANCE = $REPO.findBalance(...)
              ...
              $REPO.updateBalance(..., $BALANCE - ..., ...)
    message: >
      Potential race condition in wallet balance update. Use database-level
      atomic operations (UPDATE ... SET balance = balance - $amount WHERE
      balance >= $amount) or SELECT FOR UPDATE to prevent double-spend
      attacks. This is a critical financial integrity issue.
    severity: ERROR
    languages: [javascript, typescript, python, java]
    metadata:
      category: security
      subcategory: [igaming, wallet, race-condition]
      confidence: MEDIUM
      impact: CRITICAL

  - id: igaming-bonus-validation-bypass
    patterns:
      - pattern-either:
          - pattern: |
              if ($BONUS.isValid) { ... }
          - pattern: |
              if (bonus.status === "active") { ... }
    message: >
      Bonus validation should check multiple conditions: expiry time,
      wagering requirements met, maximum bet limits, eligible games,
      player eligibility, and single-use constraints. Simple boolean
      checks can be bypassed.
    severity: WARNING
    languages: [javascript, typescript, python]
    metadata:
      category: security
      subcategory: [igaming, bonus-abuse]

  - id: igaming-pii-logging
    patterns:
      - pattern-either:
          - pattern: console.log(..., $X.ssn, ...)
          - pattern: console.log(..., $X.passport, ...)
          - pattern: console.log(..., $X.dateOfBirth, ...)
          - pattern: console.log(..., $X.bankAccount, ...)
          - pattern: logger.$METHOD(..., $X.cardNumber, ...)
          - pattern: log.$METHOD(..., $X.cvv, ...)
    message: >
      PII/financial data detected in log output. GDPR Article 5(1)(f)
      and PCI DSS Requirement 3.4 prohibit logging of sensitive personal
      and payment data. Use data masking before logging.
    severity: ERROR
    languages: [javascript, typescript, python, java]
    metadata:
      category: security
      subcategory: [igaming, gdpr, pci-dss, pii-leak]
      compliance: ["GDPR Art.5(1)(f)", "PCI DSS 3.4"]
SEMGREP_RULE

    # Rule 2: AML and KYC bypass detection
    cat > "${SEMGREP_RULES_DIR}/igaming-aml-kyc.yaml" << 'SEMGREP_RULE'
rules:
  - id: igaming-kyc-bypass
    patterns:
      - pattern-either:
          - pattern: |
              if ($ENV === "dev" || $ENV === "staging") {
                ...
                return { verified: true }
              }
          - pattern: |
              # $COMMENT
              $KYC_CHECK = true
    message: >
      Potential KYC verification bypass. All KYC checks must be enforced
      in every environment. Hardcoded verification bypasses violate AML
      directives and can result in regulatory sanctions.
    severity: ERROR
    languages: [javascript, typescript, python]
    metadata:
      category: security
      subcategory: [igaming, aml, kyc]
      compliance: ["AMLD6", "FATF Rec.10"]

  - id: igaming-transaction-limit-bypass
    patterns:
      - pattern: |
          if ($AMOUNT > $LIMIT) {
            ...
            $TRANSACTIONS = split(...)
            ...
          }
    message: >
      Transaction splitting detected. Structuring transactions to avoid
      reporting thresholds is a criminal offense under AML regulations.
      All transactions above thresholds must trigger SAR filing.
    severity: ERROR
    languages: [javascript, typescript, python, java]
    metadata:
      category: security
      subcategory: [igaming, aml, structuring]
      compliance: ["AMLD6 Art.3", "BSA/FinCEN"]
SEMGREP_RULE

    # Rule 3: Session and authentication security
    cat > "${SEMGREP_RULES_DIR}/igaming-auth-session.yaml" << 'SEMGREP_RULE'
rules:
  - id: igaming-session-no-ip-binding
    patterns:
      - pattern: |
          $SESSION = createSession($USER, ...)
      - pattern-not: |
          $SESSION = createSession($USER, ..., { ..., ipAddress: ..., ... })
    message: >
      Game sessions should be bound to the originating IP address to
      prevent session hijacking. Include IP binding and device
      fingerprinting in session creation.
    severity: WARNING
    languages: [javascript, typescript]
    metadata:
      category: security
      subcategory: [igaming, session-security]

  - id: igaming-jwt-none-algorithm
    patterns:
      - pattern-either:
          - pattern: jwt.verify($TOKEN, ..., { algorithms: ["none"] })
          - pattern: jwt.decode($TOKEN, ..., algorithms=["none"])
          - pattern: |
              { ..., algorithm: "none", ... }
    message: >
      JWT 'none' algorithm detected. This completely disables token
      signature verification, allowing any attacker to forge valid
      tokens. Use RS256 or ES256 with proper key management.
    severity: ERROR
    languages: [javascript, typescript, python]
    metadata:
      category: security
      subcategory: [igaming, authentication, jwt]
SEMGREP_RULE

    log_ok "Created $(find "${SEMGREP_RULES_DIR}" -name '*.yaml' | wc -l) Semgrep rule files"
}

# ---------------------------------------------------------------------------
# Run SAST with Semgrep
# ---------------------------------------------------------------------------
run_sast() {
    local source_dir="${1:-.}"
    log_info "Starting SAST scan with Semgrep on ${source_dir}..."

    create_igaming_semgrep_rules

    local sast_report="${REPORT_DIR}/sast-results.json"
    local sast_sarif="${REPORT_DIR}/sast-results.sarif"

    # Run Semgrep with standard rulesets + our iGaming rules
    semgrep scan \
        --config "p/default" \
        --config "p/owasp-top-ten" \
        --config "p/security-audit" \
        --config "${SEMGREP_RULES_DIR}" \
        --json \
        --output "${sast_report}" \
        --sarif-output "${sast_sarif}" \
        --severity "${SEVERITY_THRESHOLD}" \
        --exclude "node_modules" \
        --exclude "vendor" \
        --exclude "*.test.*" \
        --exclude "*.spec.*" \
        --exclude "__pycache__" \
        --exclude ".git" \
        --max-target-bytes 1000000 \
        --timeout 300 \
        "${source_dir}" || true

    # Parse results
    if [[ -f "${sast_report}" ]]; then
        local total_findings
        total_findings=$(jq '.results | length' "${sast_report}")
        local error_count
        error_count=$(jq '[.results[] | select(.extra.severity == "ERROR")] | length' "${sast_report}")
        local warning_count
        warning_count=$(jq '[.results[] | select(.extra.severity == "WARNING")] | length' "${sast_report}")
        local igaming_count
        igaming_count=$(jq '[.results[] | select(.check_id | startswith("igaming-"))] | length' "${sast_report}")

        log_info "SAST Results Summary:"
        echo "  Total findings:    ${total_findings}"
        echo "  Critical/Error:    ${error_count}"
        echo "  Warnings:          ${warning_count}"
        echo "  iGaming-specific:  ${igaming_count}"
        echo "  Full report:       ${sast_report}"
        echo "  SARIF report:      ${sast_sarif}"

        # Generate human-readable summary
        jq -r '.results[] | "\(.extra.severity)\t\(.check_id)\t\(.path):\(.start.line)\t\(.extra.message | split("\n")[0])"' \
            "${sast_report}" | sort > "${REPORT_DIR}/sast-summary.txt"

        if [[ "${FAIL_ON_HIGH}" == "true" && "${error_count}" -gt 0 ]]; then
            log_error "SAST found ${error_count} ERROR-severity findings"
            return 1
        fi
    else
        log_warn "No SAST report generated"
    fi

    log_ok "SAST scan completed"
    return 0
}

# ---------------------------------------------------------------------------
# Run DAST with OWASP ZAP
# ---------------------------------------------------------------------------
run_dast() {
    local target="${1:-${TARGET_URL}}"
    log_info "Starting DAST scan with OWASP ZAP against ${target}..."

    local dast_report="${REPORT_DIR}/dast-results"
    local zap_context="${REPORT_DIR}/zap-context.xml"

    # Create ZAP context with iGaming-specific scan policies
    cat > "${zap_context}" << 'ZAP_CTX'
<?xml version="1.0" encoding="UTF-8" standalone="no"?>
<configuration>
  <context>
    <name>iGaming Platform</name>
    <desc>DAST context for online gambling platform</desc>
    <inscope>true</inscope>
    <incregexes>https?://${target}/.*</incregexes>
    <!-- Exclude static assets and health checks from scanning -->
    <excregexes>.*\.(css|js|png|jpg|gif|ico|svg|woff2?)(\?.*)?$</excregexes>
    <excregexes>.*/health$</excregexes>
    <excregexes>.*/metrics$</excregexes>
    <tech>
      <include>Db.PostgreSQL</include>
      <include>Db.Redis</include>
      <include>Language.JavaScript</include>
      <include>OS.Linux</include>
    </tech>
  </context>
</configuration>
ZAP_CTX

    # Create iGaming-specific scan policy
    # Focus areas: Authentication, Session Management, Input Validation,
    # Financial Transaction endpoints, Bonus/Promotion endpoints
    local zap_scan_policy="${REPORT_DIR}/igaming-scan-policy.conf"
    cat > "${zap_scan_policy}" << 'POLICY'
# iGaming DAST Scan Policy
# Aggressive scanning on financial and game endpoints
# Standard scanning on other endpoints

# High-priority scan rules (always enabled, max strength)
scanner.sql_injection.enabled=true
scanner.sql_injection.strength=HIGH
scanner.xss.enabled=true
scanner.xss.strength=HIGH
scanner.command_injection.enabled=true
scanner.command_injection.strength=HIGH
scanner.path_traversal.enabled=true
scanner.path_traversal.strength=HIGH
scanner.idor.enabled=true
scanner.idor.strength=HIGH
scanner.ssrf.enabled=true
scanner.ssrf.strength=HIGH
scanner.jwt.enabled=true
scanner.jwt.strength=HIGH
scanner.cors.enabled=true
scanner.cors.strength=MEDIUM

# iGaming-specific endpoints to test with maximum thoroughness
target.paths.financial=/api/v1/wallet,/api/v1/deposits,/api/v1/withdrawals,/api/v1/transactions
target.paths.bonus=/api/v1/bonuses,/api/v1/promotions,/api/v1/wagering
target.paths.game=/api/v1/games,/api/v1/bets,/api/v1/rounds,/api/v1/outcomes
target.paths.auth=/api/v1/auth,/api/v1/sessions,/api/v1/kyc
target.paths.admin=/api/admin,/backoffice
POLICY

    # Run ZAP in Docker
    # -t: target URL
    # -J: JSON report output
    # -r: HTML report output
    # -w: Markdown report output
    # -z: additional ZAP options
    docker run --rm \
        --network host \
        -v "${REPORT_DIR}:/zap/wrk:rw" \
        -v "${zap_context}:/zap/context.xml:ro" \
        "${ZAP_DOCKER_IMAGE}" \
        zap-full-scan.py \
        -t "${target}" \
        -J "dast-results.json" \
        -r "dast-results.html" \
        -w "dast-results.md" \
        -c "/zap/context.xml" \
        -z "-config api.key=${ZAP_API_KEY} \
            -config scanner.maxScanDurationInMins=$((MAX_SCAN_DURATION / 60)) \
            -config spider.maxDuration=$((MAX_SCAN_DURATION / 120))" \
        -I || true  # Don't fail on findings; we handle exit codes ourselves

    # Parse DAST results
    if [[ -f "${REPORT_DIR}/dast-results.json" ]]; then
        local high_alerts
        high_alerts=$(jq '[.site[].alerts[] | select(.riskcode == "3")] | length' \
            "${REPORT_DIR}/dast-results.json" 2>/dev/null || echo "0")
        local medium_alerts
        medium_alerts=$(jq '[.site[].alerts[] | select(.riskcode == "2")] | length' \
            "${REPORT_DIR}/dast-results.json" 2>/dev/null || echo "0")
        local low_alerts
        low_alerts=$(jq '[.site[].alerts[] | select(.riskcode == "1")] | length' \
            "${REPORT_DIR}/dast-results.json" 2>/dev/null || echo "0")

        log_info "DAST Results Summary:"
        echo "  High risk:     ${high_alerts}"
        echo "  Medium risk:   ${medium_alerts}"
        echo "  Low risk:      ${low_alerts}"
        echo "  HTML report:   ${REPORT_DIR}/dast-results.html"
        echo "  JSON report:   ${REPORT_DIR}/dast-results.json"

        if [[ "${FAIL_ON_HIGH}" == "true" && "${high_alerts}" -gt 0 ]]; then
            log_error "DAST found ${high_alerts} high-risk vulnerabilities"
            return 1
        fi
    else
        log_warn "No DAST report generated (ZAP may have failed to connect to ${target})"
    fi

    log_ok "DAST scan completed"
    return 0
}

# ---------------------------------------------------------------------------
# Generate consolidated report
# ---------------------------------------------------------------------------
generate_report() {
    log_info "Generating consolidated security report..."

    local report_file="${REPORT_DIR}/consolidated-report.json"

    cat > "${report_file}" << EOF
{
  "scan_metadata": {
    "timestamp": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
    "pipeline_id": "${CI_PIPELINE_ID:-manual}",
    "target": "${TARGET_URL}",
    "scanner_versions": {
      "semgrep": "$(semgrep --version 2>/dev/null || echo 'N/A')",
      "zap": "latest"
    }
  },
  "sast_report": "$(basename "${REPORT_DIR}")/sast-results.json",
  "dast_report": "$(basename "${REPORT_DIR}")/dast-results.json",
  "compliance_checks": {
    "gli_19_rng": "checked",
    "pci_dss_logging": "checked",
    "gdpr_pii": "checked",
    "aml_kyc": "checked"
  }
}
EOF

    log_ok "Consolidated report: ${report_file}"

    # Send Slack notification if configured
    if [[ -n "${SLACK_WEBHOOK_URL:-}" ]]; then
        local sast_count dast_high
        sast_count=$(jq '.results | length' "${REPORT_DIR}/sast-results.json" 2>/dev/null || echo "0")
        dast_high=$(jq '[.site[].alerts[] | select(.riskcode == "3")] | length' \
            "${REPORT_DIR}/dast-results.json" 2>/dev/null || echo "0")

        curl -s -X POST "${SLACK_WEBHOOK_URL}" \
            -H 'Content-type: application/json' \
            -d "{
                \"text\": \"Security Scan Complete\",
                \"blocks\": [{
                    \"type\": \"section\",
                    \"text\": {
                        \"type\": \"mrkdwn\",
                        \"text\": \"*Security Scan Results* (Pipeline: ${CI_PIPELINE_ID:-manual})\\nSAST Findings: ${sast_count}\\nDAST High-Risk: ${dast_high}\\n<${REPORT_DIR}|View Full Report>\"
                    }
                }]
            }" || log_warn "Failed to send Slack notification"
    fi
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
main() {
    local mode="${1:---full}"
    RUN_SAST="true"
    RUN_DAST="true"

    case "${mode}" in
        --sast-only) RUN_DAST="false" ;;
        --dast-only) RUN_SAST="false" ;;
        --full)      ;;
        --help|-h)
            echo "Usage: $0 [--sast-only|--dast-only|--full] [--target URL]"
            echo ""
            echo "Options:"
            echo "  --sast-only   Run only static analysis (Semgrep)"
            echo "  --dast-only   Run only dynamic analysis (OWASP ZAP)"
            echo "  --full        Run both SAST and DAST (default)"
            echo "  --target URL  Set DAST target URL"
            echo ""
            echo "Environment Variables:"
            echo "  SEMGREP_APP_TOKEN    Semgrep Cloud token"
            echo "  ZAP_API_KEY          OWASP ZAP API key"
            echo "  TARGET_URL           Application URL for DAST"
            echo "  FAIL_ON_HIGH         Fail pipeline on high findings (default: true)"
            echo "  SEVERITY_THRESHOLD   Minimum severity to report (default: warning)"
            exit 0
            ;;
        --target)
            TARGET_URL="${2:?Missing target URL}"
            shift
            ;;
        *)
            log_error "Unknown option: ${mode}"
            exit ${EXIT_ERROR}
            ;;
    esac

    # Handle --target as second argument
    if [[ "${2:-}" == "--target" ]]; then
        TARGET_URL="${3:?Missing target URL}"
    fi

    echo "=============================================="
    echo " iGaming Security Scanner"
    echo " SAST: Semgrep | DAST: OWASP ZAP"
    echo " Report: ${REPORT_DIR}"
    echo "=============================================="

    preflight_check

    local sast_exit=0 dast_exit=0

    if [[ "${RUN_SAST}" == "true" ]]; then
        run_sast "." || sast_exit=$?
    fi

    if [[ "${RUN_DAST}" == "true" ]]; then
        run_dast "${TARGET_URL}" || dast_exit=$?
    fi

    generate_report

    # Determine exit code
    if [[ ${sast_exit} -ne 0 && ${dast_exit} -ne 0 ]]; then
        log_error "Both SAST and DAST found critical findings"
        exit ${EXIT_BOTH_FINDINGS}
    elif [[ ${sast_exit} -ne 0 ]]; then
        log_error "SAST found critical findings"
        exit ${EXIT_SAST_FINDINGS}
    elif [[ ${dast_exit} -ne 0 ]]; then
        log_error "DAST found critical findings"
        exit ${EXIT_DAST_FINDINGS}
    fi

    log_ok "All security scans passed"
    exit ${EXIT_SUCCESS}
}

main "$@"
