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

# pre-deploy-check.sh
# Master pre-deployment verification script for iGaming platforms.
# Runs syntax checks, security scans, secret detection, and container
# validation across all modified files before any production deployment.
#
# Usage:
#   ./pre-deploy-check.sh [--base-ref <git-ref>] [--severity <critical|high|medium>]
#                         [--report-dir <path>] [--fail-fast]
#
# Exit codes:
#   0  All checks passed
#   1  One or more checks failed
#   2  Required tool not found

set -euo pipefail

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
BASE_REF="${BASE_REF:-HEAD~1}"
SEVERITY_THRESHOLD="${SEVERITY_THRESHOLD:-high}"   # critical | high | medium
REPORT_DIR="${REPORT_DIR:-./deploy-check-reports}"
FAIL_FAST="${FAIL_FAST:-false}"
LOG_PREFIX="[pre-deploy]"

PASS=0
FAIL=1
SKIP=2

declare -A CHECK_RESULTS
declare -A CHECK_MESSAGES
OVERALL_STATUS=0

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
log()     { echo "${LOG_PREFIX} [$(date -u +%H:%M:%SZ)] $*"; }
log_ok()  { echo "${LOG_PREFIX} [$(date -u +%H:%M:%SZ)] ✓  $*"; }
log_err() { echo "${LOG_PREFIX} [$(date -u +%H:%M:%SZ)] ✗  $*" >&2; }
log_warn(){ echo "${LOG_PREFIX} [$(date -u +%H:%M:%SZ)] ⚠  $*"; }
log_skip(){ echo "${LOG_PREFIX} [$(date -u +%H:%M:%SZ)] –  $*"; }

require_tool() {
    local tool="$1"
    if ! command -v "${tool}" >/dev/null 2>&1; then
        log_warn "Tool not found: ${tool} (skipping related checks)"
        return 1
    fi
    return 0
}

record_result() {
    local check_name="$1"
    local status="$2"       # PASS | FAIL | SKIP
    local message="${3:-}"
    CHECK_RESULTS["${check_name}"]="${status}"
    CHECK_MESSAGES["${check_name}"]="${message}"
    if [[ "${status}" == "FAIL" ]]; then
        OVERALL_STATUS=1
        log_err "${check_name}: ${message}"
        if [[ "${FAIL_FAST}" == "true" ]]; then
            generate_report
            exit 1
        fi
    fi
}

# ---------------------------------------------------------------------------
# Collect modified files
# ---------------------------------------------------------------------------
mkdir -p "${REPORT_DIR}"

log "Collecting modified files since ${BASE_REF}..."
MODIFIED_FILES=$(git diff --name-only "${BASE_REF}" HEAD 2>/dev/null || git diff --name-only HEAD 2>/dev/null || true)

if [[ -z "${MODIFIED_FILES}" ]]; then
    log_warn "No modified files detected -- running against all tracked files"
    MODIFIED_FILES=$(git ls-files)
fi

# Collect by extension
PY_FILES=$(echo "${MODIFIED_FILES}" | grep '\.py$' || true)
SH_FILES=$(echo "${MODIFIED_FILES}" | grep '\.sh$' || true)
JS_FILES=$(echo "${MODIFIED_FILES}" | grep -E '\.(js|mjs|cjs)$' || true)
TS_FILES=$(echo "${MODIFIED_FILES}" | grep -E '\.(ts|tsx)$' || true)
TF_FILES=$(echo "${MODIFIED_FILES}" | grep '\.tf$' || true)
GO_FILES=$(echo "${MODIFIED_FILES}" | grep '\.go$' || true)
YAML_FILES=$(echo "${MODIFIED_FILES}" | grep -E '\.(yml|yaml)$' || true)
SQL_FILES=$(echo "${MODIFIED_FILES}" | grep '\.sql$' || true)
DOCKER_FILES=$(echo "${MODIFIED_FILES}" | grep -iE '(Dockerfile|\.dockerfile)$' || true)

log "Modified files: $(echo "${MODIFIED_FILES}" | wc -l | tr -d ' ')"

# ===========================================================================
# SECTION 1: SYNTAX VERIFICATION BY LANGUAGE
# ===========================================================================
log "=== SECTION 1: Syntax Verification ==="

# ---------------------------------------------------------------------------
# Python
# ---------------------------------------------------------------------------
if [[ -n "${PY_FILES}" ]]; then
    log "Checking Python files..."

    # python -m py_compile: basic syntax check
    PY_COMPILE_ERRORS=""
    while IFS= read -r f; do
        [[ -f "${f}" ]] || continue
        if ! python3 -m py_compile "${f}" 2>>"${REPORT_DIR}/py_compile.log"; then
            PY_COMPILE_ERRORS+="${f} "
        fi
    done <<< "${PY_FILES}"

    if [[ -n "${PY_COMPILE_ERRORS}" ]]; then
        record_result "python_compile" "FAIL" "Syntax errors in: ${PY_COMPILE_ERRORS}"
    else
        record_result "python_compile" "PASS"
        log_ok "python -m py_compile: all files clean"
    fi

    # ruff: linting and style
    if require_tool ruff; then
        if ! ruff check ${PY_FILES} --output-format=json \
                >"${REPORT_DIR}/ruff.json" 2>&1; then
            record_result "python_ruff" "FAIL" "ruff found issues (see ${REPORT_DIR}/ruff.json)"
        else
            record_result "python_ruff" "PASS"
            log_ok "ruff: no issues"
        fi
    else
        record_result "python_ruff" "SKIP" "ruff not installed"
    fi

    # mypy: type checking
    if require_tool mypy; then
        if ! mypy ${PY_FILES} --ignore-missing-imports \
                --no-error-summary \
                >"${REPORT_DIR}/mypy.log" 2>&1; then
            record_result "python_mypy" "FAIL" "mypy type errors (see ${REPORT_DIR}/mypy.log)"
        else
            record_result "python_mypy" "PASS"
            log_ok "mypy: no type errors"
        fi
    else
        record_result "python_mypy" "SKIP" "mypy not installed"
    fi

    # ty: Astral's fast type checker (project rules requirement)
    if require_tool ty; then
        if ! ty check ${PY_FILES} >"${REPORT_DIR}/ty.log" 2>&1; then
            record_result "python_ty" "FAIL" "ty diagnostics found (see ${REPORT_DIR}/ty.log)"
        else
            record_result "python_ty" "PASS"
            log_ok "ty: no diagnostics"
        fi
    else
        record_result "python_ty" "SKIP" "ty not installed"
        log_skip "ty (https://github.com/astral-sh/ty) -- install for faster type checking"
    fi
fi

# ---------------------------------------------------------------------------
# Shell
# ---------------------------------------------------------------------------
if [[ -n "${SH_FILES}" ]]; then
    log "Checking shell scripts..."

    # bash -n: syntax-only parse
    SH_SYNTAX_ERRORS=""
    while IFS= read -r f; do
        [[ -f "${f}" ]] || continue
        if ! bash -n "${f}" 2>>"${REPORT_DIR}/bash_syntax.log"; then
            SH_SYNTAX_ERRORS+="${f} "
        fi
    done <<< "${SH_FILES}"

    if [[ -n "${SH_SYNTAX_ERRORS}" ]]; then
        record_result "shell_syntax" "FAIL" "Syntax errors: ${SH_SYNTAX_ERRORS}"
    else
        record_result "shell_syntax" "PASS"
        log_ok "bash -n: all shell files parse cleanly"
    fi

    # Static analysis with shellcheck
    if require_tool shellcheck; then
        SHELLCHECK_ERRORS=""
        while IFS= read -r f; do
            [[ -f "${f}" ]] || continue
            if ! shellcheck --severity=warning --format=json "${f}" \
                    >>"${REPORT_DIR}/shellcheck.json" 2>&1; then
                SHELLCHECK_ERRORS+="${f} "
            fi
        done <<< "${SH_FILES}"

        if [[ -n "${SHELLCHECK_ERRORS}" ]]; then
            record_result "shell_shellcheck" "FAIL" "shellcheck warnings in: ${SHELLCHECK_ERRORS}"
        else
            record_result "shell_shellcheck" "PASS"
            log_ok "shellcheck: no warnings"
        fi
    else
        record_result "shell_shellcheck" "SKIP" "shellcheck not installed"
    fi
fi

# ---------------------------------------------------------------------------
# JavaScript / TypeScript
# ---------------------------------------------------------------------------
if [[ -n "${JS_FILES}${TS_FILES}" ]]; then
    log "Checking JavaScript/TypeScript files..."
    ALL_JS_TS="${JS_FILES} ${TS_FILES}"

    # node -c: V8 syntax check (JS only)
    if require_tool node && [[ -n "${JS_FILES}" ]]; then
        JS_SYNTAX_ERRORS=""
        while IFS= read -r f; do
            [[ -f "${f}" ]] || continue
            if ! node --check "${f}" 2>>"${REPORT_DIR}/node_check.log"; then
                JS_SYNTAX_ERRORS+="${f} "
            fi
        done <<< "${JS_FILES}"

        if [[ -n "${JS_SYNTAX_ERRORS}" ]]; then
            record_result "js_node_check" "FAIL" "node --check errors: ${JS_SYNTAX_ERRORS}"
        else
            record_result "js_node_check" "PASS"
            log_ok "node --check: all JS files clean"
        fi
    fi

    # eslint: linting
    if require_tool eslint; then
        if ! eslint ${ALL_JS_TS} --format=json \
                --output-file="${REPORT_DIR}/eslint.json" 2>&1; then
            record_result "js_eslint" "FAIL" "eslint errors (see ${REPORT_DIR}/eslint.json)"
        else
            record_result "js_eslint" "PASS"
            log_ok "eslint: no errors"
        fi
    else
        record_result "js_eslint" "SKIP" "eslint not installed"
    fi

    # biome: format + lint (fast alternative)
    if require_tool biome; then
        if ! biome check ${ALL_JS_TS} --reporter=json \
                >"${REPORT_DIR}/biome.json" 2>&1; then
            record_result "js_biome" "FAIL" "biome check failed (see ${REPORT_DIR}/biome.json)"
        else
            record_result "js_biome" "PASS"
            log_ok "biome: no issues"
        fi
    else
        record_result "js_biome" "SKIP" "biome not installed"
    fi
fi

# ---------------------------------------------------------------------------
# Terraform
# ---------------------------------------------------------------------------
if [[ -n "${TF_FILES}" ]]; then
    log "Checking Terraform files..."

    # Collect unique directories containing changed .tf files
    TF_DIRS=$(echo "${TF_FILES}" | xargs -I{} dirname {} | sort -u)

    TF_FMT_ERRORS=""
    while IFS= read -r d; do
        [[ -d "${d}" ]] || continue
        if ! terraform -chdir="${d}" fmt -check -diff \
                >"${REPORT_DIR}/tf_fmt_${d//\//_}.log" 2>&1; then
            TF_FMT_ERRORS+="${d} "
        fi
    done <<< "${TF_DIRS}"

    if [[ -n "${TF_FMT_ERRORS}" ]]; then
        record_result "terraform_fmt" "FAIL" \
            "terraform fmt check failed in: ${TF_FMT_ERRORS}. Run: terraform fmt"
    else
        record_result "terraform_fmt" "PASS"
        log_ok "terraform fmt: all files properly formatted"
    fi

    # terraform validate (requires initialized workspace)
    TF_VALIDATE_ERRORS=""
    while IFS= read -r d; do
        [[ -d "${d}" ]] || continue
        if [[ -d "${d}/.terraform" ]]; then
            if ! terraform -chdir="${d}" validate \
                    >"${REPORT_DIR}/tf_validate_${d//\//_}.log" 2>&1; then
                TF_VALIDATE_ERRORS+="${d} "
            fi
        else
            log_skip "terraform validate for ${d} (not initialized -- run terraform init)"
        fi
    done <<< "${TF_DIRS}"

    if [[ -n "${TF_VALIDATE_ERRORS}" ]]; then
        record_result "terraform_validate" "FAIL" \
            "terraform validate errors in: ${TF_VALIDATE_ERRORS}"
    else
        record_result "terraform_validate" "PASS"
        log_ok "terraform validate: passed"
    fi

    # tflint: extended linting
    if require_tool tflint; then
        while IFS= read -r d; do
            [[ -d "${d}" ]] || continue
            tflint --chdir="${d}" --format=json \
                >"${REPORT_DIR}/tflint_${d//\//_}.json" 2>&1 || true
        done <<< "${TF_DIRS}"
        # Aggregate FAIL if any error-level finding exists
        if grep -ql '"severity":"error"' "${REPORT_DIR}"/tflint_*.json 2>/dev/null; then
            record_result "terraform_tflint" "FAIL" \
                "tflint errors found (see ${REPORT_DIR}/tflint_*.json)"
        else
            record_result "terraform_tflint" "PASS"
            log_ok "tflint: no errors"
        fi
    else
        record_result "terraform_tflint" "SKIP" "tflint not installed"
    fi
fi

# ---------------------------------------------------------------------------
# Go
# ---------------------------------------------------------------------------
if [[ -n "${GO_FILES}" ]]; then
    log "Checking Go files..."

    GO_DIRS=$(echo "${GO_FILES}" | xargs -I{} dirname {} | sort -u)

    if require_tool go; then
        GO_VET_ERRORS=""
        while IFS= read -r d; do
            [[ -d "${d}" ]] || continue
            if ! go vet "./${d}/..." >"${REPORT_DIR}/go_vet_${d//\//_}.log" 2>&1; then
                GO_VET_ERRORS+="${d} "
            fi
        done <<< "${GO_DIRS}"

        if [[ -n "${GO_VET_ERRORS}" ]]; then
            record_result "go_vet" "FAIL" "go vet errors in: ${GO_VET_ERRORS}"
        else
            record_result "go_vet" "PASS"
            log_ok "go vet: no issues"
        fi
    fi

    if require_tool staticcheck; then
        if ! staticcheck ./... >"${REPORT_DIR}/staticcheck.log" 2>&1; then
            record_result "go_staticcheck" "FAIL" \
                "staticcheck issues (see ${REPORT_DIR}/staticcheck.log)"
        else
            record_result "go_staticcheck" "PASS"
            log_ok "staticcheck: no issues"
        fi
    else
        record_result "go_staticcheck" "SKIP" "staticcheck not installed"
    fi
fi

# ---------------------------------------------------------------------------
# YAML
# ---------------------------------------------------------------------------
if [[ -n "${YAML_FILES}" ]]; then
    log "Checking YAML files..."
    if require_tool yamllint; then
        if ! yamllint ${YAML_FILES} -f parsable \
                >"${REPORT_DIR}/yamllint.log" 2>&1; then
            record_result "yaml_lint" "FAIL" \
                "yamllint errors (see ${REPORT_DIR}/yamllint.log)"
        else
            record_result "yaml_lint" "PASS"
            log_ok "yamllint: all files clean"
        fi
    else
        record_result "yaml_lint" "SKIP" "yamllint not installed"
    fi
fi

# ---------------------------------------------------------------------------
# Dockerfiles
# ---------------------------------------------------------------------------
if [[ -n "${DOCKER_FILES}" ]]; then
    log "Checking Dockerfiles..."
    if require_tool hadolint; then
        HADOLINT_ERRORS=""
        while IFS= read -r f; do
            [[ -f "${f}" ]] || continue
            if ! hadolint --format json "${f}" \
                    >"${REPORT_DIR}/hadolint_$(basename "${f}").json" 2>&1; then
                HADOLINT_ERRORS+="${f} "
            fi
        done <<< "${DOCKER_FILES}"

        if [[ -n "${HADOLINT_ERRORS}" ]]; then
            record_result "docker_hadolint" "FAIL" \
                "hadolint errors in: ${HADOLINT_ERRORS}"
        else
            record_result "docker_hadolint" "PASS"
            log_ok "hadolint: all Dockerfiles clean"
        fi
    else
        record_result "docker_hadolint" "SKIP" "hadolint not installed"
    fi
fi

# ---------------------------------------------------------------------------
# SQL
# ---------------------------------------------------------------------------
if [[ -n "${SQL_FILES}" ]]; then
    log "Checking SQL files..."
    if require_tool sqlfluff; then
        if ! sqlfluff lint ${SQL_FILES} --format json \
                >"${REPORT_DIR}/sqlfluff.json" 2>&1; then
            record_result "sql_sqlfluff" "FAIL" \
                "sqlfluff errors (see ${REPORT_DIR}/sqlfluff.json)"
        else
            record_result "sql_sqlfluff" "PASS"
            log_ok "sqlfluff: no issues"
        fi
    else
        record_result "sql_sqlfluff" "SKIP" "sqlfluff not installed"
    fi
fi

# ===========================================================================
# SECTION 2: SECRET DETECTION
# ===========================================================================
log "=== SECTION 2: Secret Detection ==="

if require_tool gitleaks; then
    log "Running gitleaks on git history..."
    if ! gitleaks detect \
            --source . \
            --report-format json \
            --report-path "${REPORT_DIR}/gitleaks.json" \
            --exit-code 1 \
            >"${REPORT_DIR}/gitleaks.log" 2>&1; then
        record_result "secrets_gitleaks" "FAIL" \
            "gitleaks detected secrets (see ${REPORT_DIR}/gitleaks.json)"
    else
        record_result "secrets_gitleaks" "PASS"
        log_ok "gitleaks: no secrets detected"
    fi
else
    record_result "secrets_gitleaks" "SKIP" "gitleaks not installed"
fi

# truffleHog: entropy-based and regex secret scanning
if require_tool trufflehog; then
    log "Running truffleHog on git history..."
    if ! trufflehog git "file://$(pwd)" \
            --json \
            --no-update \
            >"${REPORT_DIR}/trufflehog.json" 2>&1; then
        record_result "secrets_trufflehog" "FAIL" \
            "truffleHog found potential secrets (see ${REPORT_DIR}/trufflehog.json)"
    else
        record_result "secrets_trufflehog" "PASS"
        log_ok "truffleHog: no secrets detected"
    fi
else
    record_result "secrets_trufflehog" "SKIP" "trufflehog not installed"
fi

# ===========================================================================
# SECTION 3: SAST SECURITY SCANNING
# ===========================================================================
log "=== SECTION 3: Static Security Analysis ==="

# Semgrep: cross-language SAST with iGaming-specific rules
if require_tool semgrep; then
    log "Running semgrep SAST scan..."
    SEMGREP_RULES="p/owasp-top-ten p/ci p/secrets"

    # Add iGaming-specific rules if they exist
    IGAMING_RULES_PATH="./semgrep-rules/gambling-rules.yml"
    [[ -f "${IGAMING_RULES_PATH}" ]] && SEMGREP_RULES+=" ${IGAMING_RULES_PATH}"

    SEMGREP_SEVERITY_FLAG=""
    case "${SEVERITY_THRESHOLD}" in
        critical) SEMGREP_SEVERITY_FLAG="--severity ERROR" ;;
        high)     SEMGREP_SEVERITY_FLAG="--severity WARNING" ;;
        *)        SEMGREP_SEVERITY_FLAG="" ;;
    esac

    if ! semgrep scan \
            --config ${SEMGREP_RULES} \
            ${SEMGREP_SEVERITY_FLAG} \
            --json \
            --output "${REPORT_DIR}/semgrep.json" \
            . 2>"${REPORT_DIR}/semgrep.log"; then
        record_result "sast_semgrep" "FAIL" \
            "semgrep found security issues (see ${REPORT_DIR}/semgrep.json)"
    else
        record_result "sast_semgrep" "PASS"
        log_ok "semgrep: no issues at ${SEVERITY_THRESHOLD}+ severity"
    fi
else
    record_result "sast_semgrep" "SKIP" "semgrep not installed"
fi

# Bandit: Python-specific security linting
if [[ -n "${PY_FILES}" ]] && require_tool bandit; then
    log "Running bandit on Python files..."
    if ! bandit ${PY_FILES} \
            --severity-level medium \
            --format json \
            --output "${REPORT_DIR}/bandit.json" \
            2>"${REPORT_DIR}/bandit.log"; then
        record_result "sast_bandit" "FAIL" \
            "bandit found Python security issues (see ${REPORT_DIR}/bandit.json)"
    else
        record_result "sast_bandit" "PASS"
        log_ok "bandit: no Python security issues"
    fi
fi

# ===========================================================================
# SECTION 4: DEPENDENCY VULNERABILITY SCANNING
# ===========================================================================
log "=== SECTION 4: Dependency Scanning ==="

# npm audit
if [[ -f "package.json" ]] && require_tool npm; then
    log "Running npm audit..."
    NPM_AUDIT_LEVEL="high"
    [[ "${SEVERITY_THRESHOLD}" == "critical" ]] && NPM_AUDIT_LEVEL="critical"

    if ! npm audit --audit-level="${NPM_AUDIT_LEVEL}" \
            --json >"${REPORT_DIR}/npm_audit.json" 2>&1; then
        record_result "deps_npm_audit" "FAIL" \
            "npm audit found ${NPM_AUDIT_LEVEL}+ vulnerabilities (see ${REPORT_DIR}/npm_audit.json)"
    else
        record_result "deps_npm_audit" "PASS"
        log_ok "npm audit: no vulnerabilities at ${NPM_AUDIT_LEVEL}+ severity"
    fi
fi

# pip-audit
if [[ -f "requirements.txt" ]] || [[ -f "pyproject.toml" ]]; then
    if require_tool pip-audit; then
        log "Running pip-audit..."
        PIP_AUDIT_ARGS=""
        [[ -f "requirements.txt" ]] && PIP_AUDIT_ARGS="-r requirements.txt"
        [[ -f "pyproject.toml" ]]   && PIP_AUDIT_ARGS="--project ."

        if ! pip-audit ${PIP_AUDIT_ARGS} \
                --format json \
                --output "${REPORT_DIR}/pip_audit.json" 2>&1; then
            record_result "deps_pip_audit" "FAIL" \
                "pip-audit found vulnerable packages (see ${REPORT_DIR}/pip_audit.json)"
        else
            record_result "deps_pip_audit" "PASS"
            log_ok "pip-audit: no vulnerable packages"
        fi
    else
        record_result "deps_pip_audit" "SKIP" "pip-audit not installed"
    fi
fi

# trivy filesystem scan
if require_tool trivy; then
    log "Running trivy filesystem scan..."
    TRIVY_SEVERITY="CRITICAL,HIGH"
    [[ "${SEVERITY_THRESHOLD}" == "medium" ]] && TRIVY_SEVERITY="CRITICAL,HIGH,MEDIUM"

    if ! trivy fs . \
            --severity "${TRIVY_SEVERITY}" \
            --exit-code 1 \
            --ignore-unfixed \
            --format json \
            --output "${REPORT_DIR}/trivy_fs.json" \
            2>"${REPORT_DIR}/trivy_fs.log"; then
        record_result "deps_trivy_fs" "FAIL" \
            "trivy found ${TRIVY_SEVERITY} vulnerabilities (see ${REPORT_DIR}/trivy_fs.json)"
    else
        record_result "deps_trivy_fs" "PASS"
        log_ok "trivy filesystem: no ${TRIVY_SEVERITY} vulnerabilities"
    fi
else
    record_result "deps_trivy_fs" "SKIP" "trivy not installed"
fi

# ===========================================================================
# SECTION 5: CONTAINER IMAGE VALIDATION
# ===========================================================================
log "=== SECTION 5: Container Image Validation ==="

# Scan built Docker images if IMAGE_TAG is provided
if [[ -n "${IMAGE_TAG:-}" ]]; then
    log "Scanning container image: ${IMAGE_TAG}"

    if require_tool trivy; then
        if ! trivy image \
                "${IMAGE_TAG}" \
                --severity "${TRIVY_SEVERITY:-CRITICAL,HIGH}" \
                --exit-code 1 \
                --ignore-unfixed \
                --format json \
                --output "${REPORT_DIR}/trivy_image.json" \
                2>"${REPORT_DIR}/trivy_image.log"; then
            record_result "container_trivy" "FAIL" \
                "trivy image scan: ${TRIVY_SEVERITY:-CRITICAL,HIGH} vulnerabilities found"
        else
            record_result "container_trivy" "PASS"
            log_ok "trivy image: no vulnerabilities in ${IMAGE_TAG}"
        fi
    fi

    # grype: alternative container scanner
    if require_tool grype; then
        if ! grype "${IMAGE_TAG}" \
                --fail-on "${SEVERITY_THRESHOLD}" \
                --output json \
                >"${REPORT_DIR}/grype.json" 2>&1; then
            record_result "container_grype" "FAIL" \
                "grype found vulnerabilities in ${IMAGE_TAG} (see ${REPORT_DIR}/grype.json)"
        else
            record_result "container_grype" "PASS"
            log_ok "grype: image ${IMAGE_TAG} clean"
        fi
    else
        record_result "container_grype" "SKIP" "grype not installed"
    fi

    # Generate SBOM
    if require_tool syft; then
        log "Generating SBOM for ${IMAGE_TAG}..."
        syft "${IMAGE_TAG}" \
            --output cyclonedx-json \
            >"${REPORT_DIR}/sbom.cyclonedx.json" 2>&1
        log_ok "SBOM generated at ${REPORT_DIR}/sbom.cyclonedx.json"
        record_result "container_sbom" "PASS"
    else
        record_result "container_sbom" "SKIP" "syft not installed"
    fi
else
    log_skip "Container image scanning (set IMAGE_TAG env var to enable)"
    record_result "container_trivy" "SKIP" "IMAGE_TAG not set"
    record_result "container_grype" "SKIP" "IMAGE_TAG not set"
    record_result "container_sbom"  "SKIP" "IMAGE_TAG not set"
fi

# ===========================================================================
# SECTION 6: INFRASTRUCTURE AS CODE SECURITY
# ===========================================================================
log "=== SECTION 6: Infrastructure Security ==="

if [[ -n "${TF_FILES}" ]]; then
    # checkov: compliance scanning
    if require_tool checkov; then
        log "Running checkov on Terraform..."
        if ! checkov -d . \
                --framework terraform \
                --check CKV_AWS_*,CKV_GCP_*,CKV_AZURE_* \
                --compact \
                --output json \
                >"${REPORT_DIR}/checkov.json" 2>&1; then
            record_result "iac_checkov" "FAIL" \
                "checkov found IaC security issues (see ${REPORT_DIR}/checkov.json)"
        else
            record_result "iac_checkov" "PASS"
            log_ok "checkov: no IaC security issues"
        fi
    else
        record_result "iac_checkov" "SKIP" "checkov not installed"
    fi

    # tfsec
    if require_tool tfsec; then
        log "Running tfsec..."
        if ! tfsec . \
                --format json \
                >"${REPORT_DIR}/tfsec.json" 2>&1; then
            record_result "iac_tfsec" "FAIL" \
                "tfsec found security issues (see ${REPORT_DIR}/tfsec.json)"
        else
            record_result "iac_tfsec" "PASS"
            log_ok "tfsec: no issues"
        fi
    else
        record_result "iac_tfsec" "SKIP" "tfsec not installed"
    fi
fi

# ===========================================================================
# REPORT GENERATION
# ===========================================================================
generate_report() {
    local report_file="${REPORT_DIR}/pre-deploy-summary.txt"
    local json_report="${REPORT_DIR}/pre-deploy-summary.json"

    echo "========================================" > "${report_file}"
    echo "  Pre-Deployment Verification Report"    >> "${report_file}"
    echo "  $(date -u)"                            >> "${report_file}"
    echo "  Branch: $(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo 'unknown')" >> "${report_file}"
    echo "  Commit: $(git rev-parse --short HEAD 2>/dev/null || echo 'unknown')" >> "${report_file}"
    echo "========================================" >> "${report_file}"
    echo ""                                        >> "${report_file}"

    PASS_COUNT=0
    FAIL_COUNT=0
    SKIP_COUNT=0

    for check in "${!CHECK_RESULTS[@]}"; do
        status="${CHECK_RESULTS[$check]}"
        msg="${CHECK_MESSAGES[$check]:-}"
        case "${status}" in
            PASS) ((PASS_COUNT++)); printf "  ✓ PASS  %-35s\n" "${check}" >> "${report_file}" ;;
            FAIL) ((FAIL_COUNT++)); printf "  ✗ FAIL  %-35s  %s\n" "${check}" "${msg}" >> "${report_file}" ;;
            SKIP) ((SKIP_COUNT++)); printf "  –  SKIP  %-35s  %s\n" "${check}" "${msg}" >> "${report_file}" ;;
        esac
    done

    echo "" >> "${report_file}"
    echo "Summary: ${PASS_COUNT} passed, ${FAIL_COUNT} failed, ${SKIP_COUNT} skipped" >> "${report_file}"

    if [[ ${FAIL_COUNT} -gt 0 ]]; then
        echo "STATUS: BLOCKED -- resolve failures before deploying" >> "${report_file}"
    else
        echo "STATUS: APPROVED -- all checks passed" >> "${report_file}"
    fi

    # JSON report for CI/CD integration
    python3 - <<PYEOF
import json, datetime

results = {}
$(for check in "${!CHECK_RESULTS[@]}"; do
    echo "results['${check}'] = {'status': '${CHECK_RESULTS[$check]}', 'message': '${CHECK_MESSAGES[$check]:-}'}"
done)

report = {
    "timestamp": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
    "branch": "$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo 'unknown')",
    "commit": "$(git rev-parse --short HEAD 2>/dev/null || echo 'unknown')",
    "overall_status": "BLOCKED" if ${FAIL_COUNT} > 0 else "APPROVED",
    "summary": {
        "passed": ${PASS_COUNT},
        "failed": ${FAIL_COUNT},
        "skipped": ${SKIP_COUNT}
    },
    "checks": results
}

with open("${json_report}", "w") as f:
    json.dump(report, f, indent=2)

print(json.dumps(report["summary"]))
PYEOF

    cat "${report_file}"
    log "Full report: ${report_file}"
    log "JSON report: ${json_report}"
}

generate_report

# ---------------------------------------------------------------------------
# Final exit
# ---------------------------------------------------------------------------
if [[ ${OVERALL_STATUS} -ne 0 ]]; then
    log_err "Pre-deployment checks FAILED. Deployment is blocked."
    log_err "Resolve all FAIL items above before deploying to production."
    exit 1
else
    log_ok "All pre-deployment checks PASSED. Deployment approved."
    exit 0
fi
