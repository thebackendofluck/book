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

set -euo pipefail

# =============================================================================
# GitLab Security Scanning Integration for iGaming Platform
# =============================================================================
# Configures comprehensive security scanning in a GitLab CI/CD environment
# for regulated gambling platforms. Sets up SAST, DAST, dependency scanning,
# container scanning, license compliance, secret detection, and vulnerability
# management with Jira integration.
#
# Usage:
#   ./gitlab-security-integration.sh --gitlab-url <url> --token <api-token> \
#       --project <group/project> [--jira-url <url>] [--jira-token <token>] \
#       [--trivy-db-mirror <url>] [--severity critical,high]
#
# Requirements:
#   - GitLab API access (Maintainer role or higher)
#   - curl, jq
#   - For Jira integration: Jira API token
#
# iGaming context:
#   Gambling regulators (MGA, UKGC, Curacao) require demonstrable security
#   controls in the SDLC. This script integrates scanning tools that produce
#   evidence for audits and automatically escalates critical findings.
# =============================================================================

# -- Defaults --
GITLAB_URL=""
GITLAB_TOKEN=""
PROJECT_PATH=""
JIRA_URL=""
JIRA_USER=""
JIRA_TOKEN=""
JIRA_PROJECT_KEY="SEC"
TRIVY_DB_MIRROR=""
SEVERITY_THRESHOLD="critical,high"
OUTPUT_DIR="./security-config"

# =============================================================================
# Functions
# =============================================================================

usage() {
    cat <<EOF
Usage: $(basename "$0") [OPTIONS]

Configure security scanning integration for iGaming GitLab CI/CD.

Required:
  --gitlab-url <url>          GitLab instance URL
  --token <token>             GitLab API token (Maintainer+)
  --project <path>            Project path (e.g., igaming/platform)

Optional:
  --jira-url <url>            Jira instance URL for vulnerability tickets
  --jira-user <user>          Jira username/email
  --jira-token <token>        Jira API token
  --jira-project <key>        Jira project key (default: SEC)
  --trivy-db-mirror <url>     Trivy DB mirror for air-gapped environments
  --severity <levels>         Severity threshold: critical,high,medium (default: critical,high)
  --output-dir <dir>          Output directory for config files (default: ./security-config)
  --help                      Show this help

Examples:
  # Basic setup
  $(basename "$0") --gitlab-url https://gitlab.local --token glpat-xxx --project igaming/platform

  # Full setup with Jira
  $(basename "$0") --gitlab-url https://gitlab.local --token glpat-xxx \\
      --project igaming/platform --jira-url https://jira.local \\
      --jira-user security@igaming.com --jira-token xxx
EOF
    exit 0
}

log_info() {
    echo "[INFO]  $(date -u +%Y-%m-%dT%H:%M:%SZ) $*"
}

log_warn() {
    echo "[WARN]  $(date -u +%Y-%m-%dT%H:%M:%SZ) $*" >&2
}

log_error() {
    echo "[ERROR] $(date -u +%Y-%m-%dT%H:%M:%SZ) $*" >&2
}

check_prerequisites() {
    local missing=()

    for cmd in curl jq; do
        if ! command -v "${cmd}" &>/dev/null; then
            missing+=("${cmd}")
        fi
    done

    if [[ ${#missing[@]} -gt 0 ]]; then
        log_error "Missing prerequisites: ${missing[*]}"
        exit 1
    fi
}

# GitLab API helper
gitlab_api() {
    local method="$1"
    local endpoint="$2"
    shift 2

    curl -fsSL --request "${method}" \
        --header "PRIVATE-TOKEN: ${GITLAB_TOKEN}" \
        --header "Content-Type: application/json" \
        "${GITLAB_URL}/api/v4${endpoint}" \
        "$@"
}

# Get project ID from path
get_project_id() {
    local encoded_path
    encoded_path="${PROJECT_PATH//\//%2F}"
    gitlab_api GET "/projects/${encoded_path}" | jq -r '.id'
}

# =============================================================================
# Parse arguments
# =============================================================================
while [[ $# -gt 0 ]]; do
    case $1 in
        --gitlab-url)      GITLAB_URL="$2";          shift 2 ;;
        --token)           GITLAB_TOKEN="$2";        shift 2 ;;
        --project)         PROJECT_PATH="$2";        shift 2 ;;
        --jira-url)        JIRA_URL="$2";            shift 2 ;;
        --jira-user)       JIRA_USER="$2";           shift 2 ;;
        --jira-token)      JIRA_TOKEN="$2";          shift 2 ;;
        --jira-project)    JIRA_PROJECT_KEY="$2";    shift 2 ;;
        --trivy-db-mirror) TRIVY_DB_MIRROR="$2";     shift 2 ;;
        --severity)        SEVERITY_THRESHOLD="$2";  shift 2 ;;
        --output-dir)      OUTPUT_DIR="$2";          shift 2 ;;
        --help)            usage ;;
        *)
            log_error "Unknown option: $1"
            usage
            ;;
    esac
done

if [[ -z "${GITLAB_URL}" || -z "${GITLAB_TOKEN}" || -z "${PROJECT_PATH}" ]]; then
    log_error "--gitlab-url, --token, and --project are required."
    usage
fi

# =============================================================================
# Main setup
# =============================================================================

check_prerequisites

log_info "Configuring security scanning for ${PROJECT_PATH}"
log_info "GitLab URL: ${GITLAB_URL}"

mkdir -p "${OUTPUT_DIR}"

# -- Step 1: Get project ID --
log_info "Step 1: Resolving project ID..."

PROJECT_ID=$(get_project_id)
if [[ -z "${PROJECT_ID}" || "${PROJECT_ID}" == "null" ]]; then
    log_error "Could not resolve project ID for: ${PROJECT_PATH}"
    exit 1
fi
log_info "Project ID: ${PROJECT_ID}"

# -- Step 2: Configure SAST --
log_info "Step 2: Generating SAST configuration..."

cat > "${OUTPUT_DIR}/sast.gitlab-ci.yml" <<'YAML'
# =============================================================================
# SAST (Static Application Security Testing) for iGaming Platform
# =============================================================================
# Scans source code for security vulnerabilities before deployment.
# Gambling platforms handle financial transactions and PII, making SAST
# essential for regulatory compliance.

include:
  - template: Security/SAST.gitlab-ci.yml

sast:
  variables:
    SAST_EXCLUDED_PATHS: "tests/,docs/,migrations/"
    SAST_EXCLUDED_ANALYZERS: ""
    SEARCH_MAX_DEPTH: 20
  rules:
    - if: '$CI_PIPELINE_SOURCE == "merge_request_event"'
    - if: '$CI_COMMIT_BRANCH == "develop"'
    - if: '$CI_COMMIT_BRANCH == "main"'

# Additional Python-specific scanning (iGaming backends are often Python)
bandit-sast:
  stage: security
  image: python:3.12-slim
  before_script:
    - pip install --quiet bandit
  script:
    - bandit -r src/ -f json -o bandit-sast.json --severity-level medium || true
    - bandit -r src/ --severity-level high
  artifacts:
    paths:
      - bandit-sast.json
    when: always
YAML

log_info "SAST configuration written to ${OUTPUT_DIR}/sast.gitlab-ci.yml"

# -- Step 3: Configure DAST --
log_info "Step 3: Generating DAST configuration..."

cat > "${OUTPUT_DIR}/dast.gitlab-ci.yml" <<'YAML'
# =============================================================================
# DAST (Dynamic Application Security Testing) for iGaming Platform
# =============================================================================
# Scans the running application for vulnerabilities like XSS, SQLi, CSRF.
# Critical for payment endpoints and player account management.

include:
  - template: Security/DAST.gitlab-ci.yml

dast:
  variables:
    DAST_WEBSITE: "${DAST_TARGET_URL}"
    DAST_FULL_SCAN_ENABLED: "true"
    DAST_BROWSER_SCAN: "true"
    DAST_EXCLUDE_URLS: "/api/v1/admin/*,/api/v1/internal/*"
  rules:
    - if: '$CI_COMMIT_BRANCH == "main"'

# iGaming-specific DAST: test payment and auth endpoints
dast-payment-endpoints:
  stage: post-deploy
  image: zaproxy/zap-stable:latest
  variables:
    TARGET: "${DAST_TARGET_URL}"
  script:
    - |
      zap-api-scan.py \
        -t "${TARGET}/api/v1/openapi.json" \
        -f openapi \
        -r dast-api-report.html \
        -J dast-api-report.json \
        -c zap-api-config.conf \
        -I
  artifacts:
    paths:
      - dast-api-report.html
      - dast-api-report.json
    when: always
YAML

log_info "DAST configuration written to ${OUTPUT_DIR}/dast.gitlab-ci.yml"

# -- Step 4: Configure Dependency Scanning --
log_info "Step 4: Generating dependency scanning configuration..."

cat > "${OUTPUT_DIR}/dependency-scanning.gitlab-ci.yml" <<'YAML'
# =============================================================================
# Dependency Scanning for iGaming Platform
# =============================================================================
# Identifies vulnerable dependencies in pip, npm, and maven packages.
# Gambling platforms must patch known CVEs to maintain their operating license.

include:
  - template: Security/Dependency-Scanning.gitlab-ci.yml

dependency_scanning:
  variables:
    DS_EXCLUDED_PATHS: "tests/,docs/"
    DS_MAX_DEPTH: 10
  rules:
    - if: '$CI_PIPELINE_SOURCE == "merge_request_event"'
    - if: '$CI_COMMIT_BRANCH == "develop"'
    - if: '$CI_COMMIT_BRANCH == "main"'

# pip-audit for Python-specific deep scanning
pip-audit-scan:
  stage: security
  image: python:3.12-slim
  before_script:
    - pip install --quiet pip-audit
  script:
    - pip-audit -r requirements.txt --format json --output pip-audit-report.json || true
    - pip-audit -r requirements.txt --desc
  artifacts:
    paths:
      - pip-audit-report.json
    when: always
YAML

log_info "Dependency scanning config written to ${OUTPUT_DIR}/dependency-scanning.gitlab-ci.yml"

# -- Step 5: Configure Container Scanning --
log_info "Step 5: Generating container scanning configuration..."

TRIVY_EXTRA=""
if [[ -n "${TRIVY_DB_MIRROR}" ]]; then
    TRIVY_EXTRA="
    TRIVY_DB_REPOSITORY: \"${TRIVY_DB_MIRROR}\""
fi

cat > "${OUTPUT_DIR}/container-scanning.gitlab-ci.yml" <<YAML
# =============================================================================
# Container Scanning for iGaming Platform
# =============================================================================
# Scans Docker images for OS-level and library vulnerabilities.
# All iGaming services run in containers; images must be clean before deployment.

include:
  - template: Security/Container-Scanning.gitlab-ci.yml

container_scanning:
  variables:
    CS_IMAGE: "\${CI_REGISTRY_IMAGE}/\${SERVICE}:\${CI_COMMIT_SHORT_SHA}"
    CS_SEVERITY_THRESHOLD: "HIGH"${TRIVY_EXTRA}
  rules:
    - if: '\$CI_COMMIT_BRANCH == "develop"'
    - if: '\$CI_COMMIT_BRANCH == "main"'

# Scan all platform service images
.trivy-scan-template:
  stage: security
  image: aquasec/trivy:0.50.0
  script:
    - trivy image --severity HIGH,CRITICAL --format json
        --output "trivy-\${SERVICE}-report.json"
        "\${CI_REGISTRY_IMAGE}/\${SERVICE}:\${CI_COMMIT_SHORT_SHA}"
    - trivy image --severity CRITICAL --exit-code 1
        "\${CI_REGISTRY_IMAGE}/\${SERVICE}:\${CI_COMMIT_SHORT_SHA}"
  artifacts:
    paths:
      - "trivy-\${SERVICE}-report.json"
    when: always

trivy-pam:
  extends: .trivy-scan-template
  variables:
    SERVICE: pam

trivy-wallet:
  extends: .trivy-scan-template
  variables:
    SERVICE: wallet

trivy-gal:
  extends: .trivy-scan-template
  variables:
    SERVICE: gal
YAML

log_info "Container scanning config written to ${OUTPUT_DIR}/container-scanning.gitlab-ci.yml"

# -- Step 6: Configure License Compliance --
log_info "Step 6: Generating license compliance configuration..."

cat > "${OUTPUT_DIR}/license-compliance.gitlab-ci.yml" <<'YAML'
# =============================================================================
# License Compliance for iGaming Platform
# =============================================================================
# Ensures all dependencies use licenses compatible with proprietary gambling
# software. AGPL, SSPL, and certain copyleft licenses are typically banned
# in commercial gambling platforms.

include:
  - template: Security/License-Scanning.gitlab-ci.yml

license_scanning:
  variables:
    LICENSE_FINDER_CLI_OPTS: "--recursive"
  rules:
    - if: '$CI_COMMIT_BRANCH == "develop"'
    - if: '$CI_COMMIT_BRANCH == "main"'

# Explicit license policy enforcement
license-policy-check:
  stage: security
  image: python:3.12-slim
  before_script:
    - pip install --quiet pip-licenses
  script:
    - echo "Generating license report..."
    - pip-licenses --format=json --output-file=licenses.json
    - echo "Checking for banned licenses..."
    - |
      BANNED="AGPL-3.0-only AGPL-3.0-or-later SSPL-1.0 EUPL-1.2 GPL-3.0-only"
      VIOLATIONS=0
      for license in ${BANNED}; do
        COUNT=$(jq "[.[] | select(.License == \"${license}\")] | length" licenses.json)
        if [ "${COUNT}" -gt 0 ]; then
          echo "VIOLATION: Found ${COUNT} package(s) with banned license: ${license}"
          jq ".[] | select(.License == \"${license}\") | .Name" licenses.json
          VIOLATIONS=$((VIOLATIONS + COUNT))
        fi
      done
      if [ "${VIOLATIONS}" -gt 0 ]; then
        echo "FAILED: ${VIOLATIONS} license violation(s) found"
        exit 1
      fi
      echo "PASSED: No banned licenses detected"
  artifacts:
    paths:
      - licenses.json
    when: always
YAML

log_info "License compliance config written to ${OUTPUT_DIR}/license-compliance.gitlab-ci.yml"

# -- Step 7: Configure Secret Detection --
log_info "Step 7: Generating secret detection configuration..."

cat > "${OUTPUT_DIR}/secret-detection.gitlab-ci.yml" <<'YAML'
# =============================================================================
# Secret Detection for iGaming Platform
# =============================================================================
# Detects accidentally committed secrets (API keys, database passwords,
# payment gateway credentials). Critical in gambling where leaked payment
# credentials could lead to financial fraud and license revocation.

include:
  - template: Security/Secret-Detection.gitlab-ci.yml

secret_detection:
  variables:
    SECRET_DETECTION_HISTORIC_SCAN: "true"
    SECRET_DETECTION_EXCLUDED_PATHS: "tests/fixtures/"
  rules:
    - if: '$CI_PIPELINE_SOURCE == "merge_request_event"'
    - if: '$CI_COMMIT_BRANCH == "develop"'
    - if: '$CI_COMMIT_BRANCH == "main"'

# Additional Gitleaks scan with custom rules for iGaming
gitleaks-igaming:
  stage: security
  image: zricethezav/gitleaks:latest
  script:
    - |
      gitleaks detect \
        --source . \
        --config .gitleaks.toml \
        --report-format sarif \
        --report-path gitleaks-report.sarif \
        --verbose
  artifacts:
    paths:
      - gitleaks-report.sarif
    reports:
      sast: gitleaks-report.sarif
    when: always
YAML

# Create custom Gitleaks config for iGaming patterns
cat > "${OUTPUT_DIR}/.gitleaks.toml" <<'TOML'
# Gitleaks configuration for iGaming platform
# Detects gambling-specific secrets and credentials

title = "iGaming Platform Secret Detection"

# Extend default rules
[extend]
useDefault = true

# Custom rules for payment gateway credentials
[[rules]]
id = "payment-gateway-api-key"
description = "Payment gateway API key (Stripe, Adyen, Nuvei, Paysafe)"
regex = '''(?i)(stripe|adyen|nuvei|paysafe)[_-]?(api|secret|live|test)[_-]?key\s*[=:]\s*['"]?[a-zA-Z0-9_-]{20,}'''
tags = ["payment", "critical"]

[[rules]]
id = "gambling-license-key"
description = "Gambling license or regulatory API key"
regex = '''(?i)(mga|ukgc|curacao|kahnawake|gibraltar)[_-]?(license|api|auth)[_-]?key\s*[=:]\s*['"]?[a-zA-Z0-9_-]{10,}'''
tags = ["compliance", "critical"]

[[rules]]
id = "database-connection-string"
description = "Database connection string with credentials"
regex = '''(?i)postgres(ql)?://[^:]+:[^@]+@[^/]+/[^\s'"]+'''
tags = ["database", "high"]

[[rules]]
id = "rng-seed-value"
description = "RNG seed or secret (must never be exposed)"
regex = '''(?i)rng[_-]?(seed|secret|key)\s*[=:]\s*['"]?[a-zA-Z0-9_-]{8,}'''
tags = ["compliance", "critical"]

# Allowlist for test fixtures and documentation
[allowlist]
paths = [
    '''tests/fixtures/''',
    '''docs/examples/''',
    '''\.md$''',
]
TOML

log_info "Secret detection config written to ${OUTPUT_DIR}/secret-detection.gitlab-ci.yml"
log_info "Gitleaks custom config written to ${OUTPUT_DIR}/.gitleaks.toml"

# -- Step 8: Set up vulnerability management variables --
log_info "Step 8: Configuring project-level CI/CD variables..."

set_project_variable() {
    local key="$1"
    local value="$2"
    local masked="${3:-false}"
    local protected="${4:-true}"

    # Check if variable exists
    local existing
    existing=$(gitlab_api GET "/projects/${PROJECT_ID}/variables/${key}" 2>/dev/null || echo "")

    if [[ -n "${existing}" && "${existing}" != "" ]]; then
        gitlab_api PUT "/projects/${PROJECT_ID}/variables/${key}" \
            -d "{\"value\": \"${value}\", \"masked\": ${masked}, \"protected\": ${protected}}" > /dev/null
        log_info "Updated variable: ${key}"
    else
        gitlab_api POST "/projects/${PROJECT_ID}/variables" \
            -d "{\"key\": \"${key}\", \"value\": \"${value}\", \"masked\": ${masked}, \"protected\": ${protected}}" > /dev/null
        log_info "Created variable: ${key}"
    fi
}

set_project_variable "SECURITY_SEVERITY_THRESHOLD" "${SEVERITY_THRESHOLD}" "false" "false"

if [[ -n "${TRIVY_DB_MIRROR}" ]]; then
    set_project_variable "TRIVY_DB_MIRROR" "${TRIVY_DB_MIRROR}" "false" "false"
fi

# -- Step 9: Jira integration for critical vulnerabilities --
if [[ -n "${JIRA_URL}" && -n "${JIRA_TOKEN}" ]]; then
    log_info "Step 9: Configuring Jira integration for vulnerability tickets..."

    set_project_variable "JIRA_URL" "${JIRA_URL}" "false" "true"
    set_project_variable "JIRA_USER" "${JIRA_USER}" "false" "true"
    set_project_variable "JIRA_TOKEN" "${JIRA_TOKEN}" "true" "true"
    set_project_variable "JIRA_PROJECT_KEY" "${JIRA_PROJECT_KEY}" "false" "true"

    # Generate the Jira ticket creation job
    cat > "${OUTPUT_DIR}/vulnerability-jira.gitlab-ci.yml" <<'YAML'
# =============================================================================
# Auto-create Jira tickets for critical vulnerabilities
# =============================================================================

create-jira-vulnerability-tickets:
  stage: post-deploy
  image: python:3.12-slim
  before_script:
    - pip install --quiet requests
  script:
    - |
      python3 << 'PYTHON'
      import json
      import os
      import glob
      import requests

      jira_url = os.environ["JIRA_URL"]
      jira_user = os.environ["JIRA_USER"]
      jira_token = os.environ["JIRA_TOKEN"]
      project_key = os.environ.get("JIRA_PROJECT_KEY", "SEC")
      pipeline_url = os.environ.get("CI_PIPELINE_URL", "unknown")
      commit_sha = os.environ.get("CI_COMMIT_SHORT_SHA", "unknown")

      # Collect all security scan reports
      reports = glob.glob("*-report.json") + glob.glob("trivy-*-report.json")
      critical_findings = []

      for report_path in reports:
          try:
              with open(report_path) as f:
                  data = json.load(f)
              # Handle Trivy format
              if isinstance(data, dict) and "Results" in data:
                  for result in data["Results"]:
                      for vuln in result.get("Vulnerabilities", []):
                          if vuln.get("Severity", "").upper() in ("CRITICAL", "HIGH"):
                              critical_findings.append({
                                  "id": vuln.get("VulnerabilityID", "UNKNOWN"),
                                  "severity": vuln.get("Severity", "UNKNOWN"),
                                  "package": vuln.get("PkgName", "unknown"),
                                  "source": report_path,
                              })
          except (json.JSONDecodeError, KeyError):
              continue

      print(f"Found {len(critical_findings)} critical/high findings")

      # Create Jira tickets (deduplicate by CVE ID)
      seen = set()
      for finding in critical_findings:
          if finding["id"] in seen:
              continue
          seen.add(finding["id"])

          summary = f"[{finding['severity']}] {finding['id']} in {finding['package']}"
          description = (
              f"*Vulnerability:* {finding['id']}\n"
              f"*Severity:* {finding['severity']}\n"
              f"*Package:* {finding['package']}\n"
              f"*Source:* {finding['source']}\n"
              f"*Pipeline:* {pipeline_url}\n"
              f"*Commit:* {commit_sha}\n"
          )

          resp = requests.post(
              f"{jira_url}/rest/api/2/issue",
              auth=(jira_user, jira_token),
              json={
                  "fields": {
                      "project": {"key": project_key},
                      "summary": summary,
                      "description": description,
                      "issuetype": {"name": "Bug"},
                      "priority": {"name": "Highest" if finding["severity"] == "CRITICAL" else "High"},
                      "labels": ["security", "vulnerability", "auto-created"],
                  }
              },
          )

          if resp.status_code == 201:
              ticket = resp.json()["key"]
              print(f"Created: {ticket} - {summary}")
          else:
              print(f"Failed to create ticket for {finding['id']}: {resp.status_code}")
      PYTHON
  rules:
    - if: '$CI_COMMIT_BRANCH == "main"'
  when: on_failure
YAML

    log_info "Jira vulnerability integration written to ${OUTPUT_DIR}/vulnerability-jira.gitlab-ci.yml"
else
    log_info "Step 9: Skipping Jira integration (not configured)"
fi

# -- Step 10: Generate main security include file --
log_info "Step 10: Generating main security include file..."

cat > "${OUTPUT_DIR}/security-scanning.gitlab-ci.yml" <<YAML
# =============================================================================
# Security Scanning - Master Include for iGaming Platform
# =============================================================================
# Include this file in your main .gitlab-ci.yml to enable all security scans.
#
# Usage in .gitlab-ci.yml:
#   include:
#     - local: 'security-config/security-scanning.gitlab-ci.yml'
# =============================================================================

include:
  - local: 'security-config/sast.gitlab-ci.yml'
  - local: 'security-config/dast.gitlab-ci.yml'
  - local: 'security-config/dependency-scanning.gitlab-ci.yml'
  - local: 'security-config/container-scanning.gitlab-ci.yml'
  - local: 'security-config/license-compliance.gitlab-ci.yml'
  - local: 'security-config/secret-detection.gitlab-ci.yml'
$(if [[ -n "${JIRA_URL}" ]]; then echo "  - local: 'security-config/vulnerability-jira.gitlab-ci.yml'"; fi)
YAML

log_info "Master include file written to ${OUTPUT_DIR}/security-scanning.gitlab-ci.yml"

# -- Summary --
cat <<SUMMARY

=============================================================================
  Security Scanning Integration Complete
=============================================================================
  Project:        ${PROJECT_PATH} (ID: ${PROJECT_ID})
  GitLab URL:     ${GITLAB_URL}
  Severity:       ${SEVERITY_THRESHOLD}
  Jira:           $(if [[ -n "${JIRA_URL}" ]]; then echo "Enabled (${JIRA_URL})"; else echo "Not configured"; fi)
  Output:         ${OUTPUT_DIR}/

  Generated files:
    - sast.gitlab-ci.yml              SAST with Bandit
    - dast.gitlab-ci.yml              DAST with ZAP
    - dependency-scanning.gitlab-ci.yml  Dependency scanning + pip-audit
    - container-scanning.gitlab-ci.yml   Container scanning with Trivy
    - license-compliance.gitlab-ci.yml   License policy enforcement
    - secret-detection.gitlab-ci.yml     Secret detection + Gitleaks
    - .gitleaks.toml                     Custom iGaming secret patterns
    - security-scanning.gitlab-ci.yml    Master include file
$(if [[ -n "${JIRA_URL}" ]]; then echo "    - vulnerability-jira.gitlab-ci.yml   Auto-create Jira tickets"; fi)

  Next steps:
  1. Copy ${OUTPUT_DIR}/ to your repository root
  2. Add to .gitlab-ci.yml:
       include:
         - local: 'security-config/security-scanning.gitlab-ci.yml'
  3. Commit and push to trigger first scan
  4. Review findings in Security > Vulnerability Report
=============================================================================
SUMMARY
