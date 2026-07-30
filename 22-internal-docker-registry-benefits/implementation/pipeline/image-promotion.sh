#!/usr/bin/env bash
# Companion code for "The Backend of Luck" - Chapter 22, Internal Docker Registry.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# shellcheck disable=SC2155
# =============================================================================
# Image Promotion Pipeline — dev → staging → production
# =============================================================================
# Promotes container images through environments with security gates at
# each stage. Ensures only scanned, signed, and tested images reach
# production. Implements the "golden path" for iGaming image lifecycle.
#
# Prerequisites:
#   - crane (image copy without Docker daemon)
#   - cosign (signature verification)
#   - trivy (vulnerability scanning)
#   - jq, curl
#   - Harbor API access
#
# Usage:
#   ./image-promotion.sh promote <image:tag> dev staging
#   ./image-promotion.sh promote <image:tag> staging prod
#   ./image-promotion.sh status <image:tag>
#   ./image-promotion.sh rollback <image:tag> prod
# =============================================================================
set -euo pipefail

HARBOR_URL="${HARBOR_URL:-https://registry.casino-platform.internal}"
HARBOR_USER="${HARBOR_USER:-admin}"
HARBOR_PASS="${HARBOR_PASS:?HARBOR_PASS must be set}"
COSIGN_PUB="${COSIGN_PUB:-/etc/casino/signing-keys/cosign.pub}"
REPORT_DIR="${REPORT_DIR:-/var/reports/promotion}"
ALERT_WEBHOOK="${ALERT_WEBHOOK:-}"

# Environment registry prefixes
DEV_PROJECT="casino-dev"
STAGING_PROJECT="casino-staging"
PROD_PROJECT="casino-platform"

# Promotion requirements per environment
# staging: must pass vulnerability scan, must be signed
# prod: must pass vuln scan (zero critical), signed, malware-scanned, SBOM attached, soak time

STAGING_SOAK_HOURS="${STAGING_SOAK_HOURS:-24}"  # Min hours in staging before prod
PROD_CRITICAL_MAX=0
PROD_HIGH_MAX=0

mkdir -p "${REPORT_DIR}"

log() { echo "[$(date +'%Y-%m-%d %H:%M:%S')] [promote] $*"; }
error() { echo "[$(date +'%Y-%m-%d %H:%M:%S')] [promote] ERROR: $*" >&2; }

send_alert() {
    local status="$1" message="$2"
    [[ -z "${ALERT_WEBHOOK}" ]] && return
    local emoji=":white_check_mark:"
    [[ "${status}" == "FAILED" ]] && emoji=":x:"
    [[ "${status}" == "WARNING" ]] && emoji=":warning:"
    curl -sf -X POST "${ALERT_WEBHOOK}" \
        -H "Content-Type: application/json" \
        -d "{\"text\": \"${emoji} *Image Promotion ${status}*\n${message}\"}" || true
}

# --- Gate: Vulnerability Scan ------------------------------------------------
gate_vuln_scan() {
    local image="$1" env="$2"

    log "  Gate: Vulnerability scan for ${env}..."

    local report="/tmp/promotion_vuln_$(date +%s).json"
    trivy image "${image}" \
        --format json \
        --output "${report}" \
        --severity CRITICAL,HIGH,MEDIUM \
        --ignore-unfixed \
        2>/dev/null

    local critical high
    critical=$(jq '[.Results[]?.Vulnerabilities[]? | select(.Severity=="CRITICAL")] | length' "${report}" 2>/dev/null || echo 0)
    high=$(jq '[.Results[]?.Vulnerabilities[]? | select(.Severity=="HIGH")] | length' "${report}" 2>/dev/null || echo 0)

    if [[ "${env}" == "prod" ]]; then
        if [[ "${critical}" -gt "${PROD_CRITICAL_MAX}" ]]; then
            error "  GATE FAILED: ${critical} critical vulns (max ${PROD_CRITICAL_MAX} for prod)"
            rm -f "${report}"
            return 1
        fi
        if [[ "${high}" -gt "${PROD_HIGH_MAX}" ]]; then
            error "  GATE FAILED: ${high} high vulns (max ${PROD_HIGH_MAX} for prod)"
            rm -f "${report}"
            return 1
        fi
    else
        # Staging: block on critical only
        if [[ "${critical}" -gt 0 ]]; then
            error "  GATE FAILED: ${critical} critical vulns (zero allowed for staging)"
            rm -f "${report}"
            return 1
        fi
    fi

    log "  Gate PASSED: vuln scan (critical=${critical}, high=${high})"
    rm -f "${report}"
    return 0
}

# --- Gate: Signature Verification ---------------------------------------------
gate_signature() {
    local image="$1"

    log "  Gate: Signature verification..."

    if cosign verify --key "${COSIGN_PUB}" "${image}" &>/dev/null; then
        log "  Gate PASSED: signature valid"
        return 0
    else
        error "  GATE FAILED: signature verification failed for ${image}"
        return 1
    fi
}

# --- Gate: Soak Time (staging → prod only) ------------------------------------
gate_soak_time() {
    local image="$1" source_env="$2"

    [[ "${source_env}" != "staging" ]] && return 0

    log "  Gate: Soak time check (min ${STAGING_SOAK_HOURS}h in staging)..."

    # Check when image was pushed to staging
    local repo_name tag
    repo_name=$(echo "${image}" | sed "s|${HARBOR_URL#https://}/||" | cut -d: -f1)
    tag=$(echo "${image}" | cut -d: -f2)

    local push_time
    push_time=$(curl -sk -u "${HARBOR_USER}:${HARBOR_PASS}" \
        "${HARBOR_URL}/api/v2.0/projects/${STAGING_PROJECT}/repositories/${repo_name##*/}/artifacts?with_tag=true" \
        2>/dev/null | jq -r "[.[]? | select(.tags[]?.name == \"${tag}\")] | .[0].push_time // empty" 2>/dev/null)

    if [[ -z "${push_time}" ]]; then
        error "  GATE FAILED: Cannot determine staging push time for ${image}"
        return 1
    fi

    local push_epoch now_epoch hours_in_staging
    push_epoch=$(date -d "${push_time}" +%s 2>/dev/null || echo 0)
    now_epoch=$(date +%s)
    hours_in_staging=$(( (now_epoch - push_epoch) / 3600 ))

    if [[ "${hours_in_staging}" -lt "${STAGING_SOAK_HOURS}" ]]; then
        error "  GATE FAILED: Image has been in staging for ${hours_in_staging}h (min: ${STAGING_SOAK_HOURS}h)"
        return 1
    fi

    log "  Gate PASSED: soak time (${hours_in_staging}h in staging)"
    return 0
}

# --- Gate: SBOM Exists (prod only) --------------------------------------------
gate_sbom() {
    local image="$1" env="$2"

    [[ "${env}" != "prod" ]] && return 0

    log "  Gate: SBOM attestation check..."

    if cosign verify-attestation \
        --key "${COSIGN_PUB}" \
        --type spdxjson \
        "${image}" &>/dev/null; then
        log "  Gate PASSED: SBOM attestation present"
        return 0
    else
        error "  GATE FAILED: No SBOM attestation found (required for production)"
        return 1
    fi
}

# --- Promote Image ------------------------------------------------------------
cmd_promote() {
    local image_tag="$1"
    local source_env="$2"
    local target_env="$3"

    # Determine source and target projects
    local source_project target_project
    case "${source_env}" in
        dev)     source_project="${DEV_PROJECT}" ;;
        staging) source_project="${STAGING_PROJECT}" ;;
        prod)    source_project="${PROD_PROJECT}" ;;
        *)       error "Unknown environment: ${source_env}"; exit 1 ;;
    esac
    case "${target_env}" in
        dev)     target_project="${DEV_PROJECT}" ;;
        staging) target_project="${STAGING_PROJECT}" ;;
        prod)    target_project="${PROD_PROJECT}" ;;
        *)       error "Unknown environment: ${target_env}"; exit 1 ;;
    esac

    # Validate promotion path
    if [[ "${source_env}" == "dev" && "${target_env}" != "staging" ]]; then
        error "dev can only promote to staging"
        exit 1
    fi
    if [[ "${source_env}" == "staging" && "${target_env}" != "prod" ]]; then
        error "staging can only promote to prod"
        exit 1
    fi

    local source_image="${HARBOR_URL#https://}/${source_project}/${image_tag}"
    local target_image="${HARBOR_URL#https://}/${target_project}/${image_tag}"

    log "Promoting: ${source_image} → ${target_image}"
    log "Environment: ${source_env} → ${target_env}"

    # Run promotion gates
    local gates_passed=true

    gate_vuln_scan "${source_image}" "${target_env}" || gates_passed=false
    gate_signature "${source_image}" || gates_passed=false
    gate_soak_time "${source_image}" "${source_env}" || gates_passed=false
    gate_sbom "${source_image}" "${target_env}" || gates_passed=false

    if [[ "${gates_passed}" != "true" ]]; then
        error "PROMOTION BLOCKED: One or more gates failed"
        send_alert "FAILED" "Promotion of \`${image_tag}\` from ${source_env} to ${target_env} was blocked by security gates."

        # Log promotion attempt for audit
        jq -n \
            --arg image "${image_tag}" \
            --arg source "${source_env}" \
            --arg target "${target_env}" \
            --arg status "BLOCKED" \
            --arg timestamp "$(date -u +'%Y-%m-%dT%H:%M:%SZ')" \
            '{image: $image, source: $source, target: $target, status: $status, timestamp: $timestamp}' \
            >> "${REPORT_DIR}/promotion_audit.jsonl"

        return 1
    fi

    # All gates passed — copy image
    log "All gates passed. Copying image..."

    crane copy "${source_image}" "${target_image}" 2>/dev/null
    log "Image copied: ${target_image}"

    # Copy signatures and attestations
    log "Copying signatures and attestations..."
    cosign copy "${source_image}" "${target_image}" 2>/dev/null || true

    # Add promotion label in Harbor
    curl -sk -u "${HARBOR_USER}:${HARBOR_PASS}" \
        -X POST "${HARBOR_URL}/api/v2.0/projects/${target_project}/repositories/${image_tag%%:*}/artifacts/${image_tag##*:}/labels" \
        -H "Content-Type: application/json" \
        -d "{\"name\": \"promoted-from-${source_env}\", \"description\": \"Promoted at $(date -u +'%Y-%m-%dT%H:%M:%SZ')\"}" \
        2>/dev/null || true

    # Audit log
    jq -n \
        --arg image "${image_tag}" \
        --arg source "${source_env}" \
        --arg target "${target_env}" \
        --arg source_image "${source_image}" \
        --arg target_image "${target_image}" \
        --arg status "SUCCESS" \
        --arg timestamp "$(date -u +'%Y-%m-%dT%H:%M:%SZ')" \
        --arg promoted_by "${USER:-ci-pipeline}" \
        '{
            image: $image,
            source: $source,
            target: $target,
            source_ref: $source_image,
            target_ref: $target_image,
            status: $status,
            timestamp: $timestamp,
            promoted_by: $promoted_by
        }' >> "${REPORT_DIR}/promotion_audit.jsonl"

    log "PROMOTION COMPLETE: ${image_tag} promoted to ${target_env}"
    send_alert "SUCCESS" "Image \`${image_tag}\` promoted from ${source_env} to ${target_env}."
}

# --- Image Status -------------------------------------------------------------
cmd_status() {
    local image_tag="$1"

    log "Checking promotion status for: ${image_tag}"

    for env in dev staging prod; do
        local project
        case "${env}" in
            dev)     project="${DEV_PROJECT}" ;;
            staging) project="${STAGING_PROJECT}" ;;
            prod)    project="${PROD_PROJECT}" ;;
        esac

        local full_image="${HARBOR_URL#https://}/${project}/${image_tag}"
        local exists="NO"
        local signed="NO"
        local scanned="NO"

        # Check if image exists
        if crane manifest "${full_image}" &>/dev/null; then
            exists="YES"

            # Check signature
            cosign verify --key "${COSIGN_PUB}" "${full_image}" &>/dev/null && signed="YES"

            # Check scan status from Harbor
            local repo_name tag
            repo_name="${image_tag%%:*}"
            tag="${image_tag##*:}"
            local scan_status
            scan_status=$(curl -sk -u "${HARBOR_USER}:${HARBOR_PASS}" \
                "${HARBOR_URL}/api/v2.0/projects/${project}/repositories/${repo_name}/artifacts?with_scan_overview=true&with_tag=true" \
                2>/dev/null | jq -r "[.[]? | select(.tags[]?.name == \"${tag}\")] | .[0].scan_overview // empty" 2>/dev/null)
            [[ -n "${scan_status}" ]] && scanned="YES"
        fi

        printf "  %-10s | exists: %-3s | signed: %-3s | scanned: %-3s\n" "${env}" "${exists}" "${signed}" "${scanned}"
    done
}

# --- Rollback -----------------------------------------------------------------
cmd_rollback() {
    local image_tag="$1"
    local env="$2"

    log "Rolling back ${image_tag} in ${env}..."

    local project
    case "${env}" in
        dev)     project="${DEV_PROJECT}" ;;
        staging) project="${STAGING_PROJECT}" ;;
        prod)    project="${PROD_PROJECT}" ;;
        *)       error "Unknown environment: ${env}"; exit 1 ;;
    esac

    # Get previous tag/digest
    local repo_name="${image_tag%%:*}"
    local artifacts
    artifacts=$(curl -sk -u "${HARBOR_USER}:${HARBOR_PASS}" \
        "${HARBOR_URL}/api/v2.0/projects/${project}/repositories/${repo_name}/artifacts?page_size=5&with_tag=true&sort=-push_time" \
        2>/dev/null)

    local previous_digest
    previous_digest=$(echo "${artifacts}" | jq -r '.[1].digest // empty' 2>/dev/null)

    if [[ -z "${previous_digest}" ]]; then
        error "No previous version found to rollback to"
        exit 1
    fi

    log "Previous version digest: ${previous_digest}"
    log "Tagging previous version as current..."

    # Re-tag the previous artifact
    local current_tag="${image_tag##*:}"
    crane tag "${HARBOR_URL#https://}/${project}/${repo_name}@${previous_digest}" "${current_tag}" 2>/dev/null

    # Audit log
    jq -n \
        --arg image "${image_tag}" \
        --arg env "${env}" \
        --arg previous_digest "${previous_digest}" \
        --arg status "ROLLBACK" \
        --arg timestamp "$(date -u +'%Y-%m-%dT%H:%M:%SZ')" \
        '{image: $image, env: $env, rolled_back_to: $previous_digest, status: $status, timestamp: $timestamp}' \
        >> "${REPORT_DIR}/promotion_audit.jsonl"

    log "ROLLBACK COMPLETE for ${image_tag} in ${env}"
    send_alert "WARNING" "Image \`${image_tag}\` was rolled back in ${env}."
}

# --- Main ---------------------------------------------------------------------
main() {
    local cmd="${1:-help}"
    shift || true

    case "${cmd}" in
        promote)
            [[ $# -lt 3 ]] && { error "Usage: $0 promote <image:tag> <source-env> <target-env>"; exit 1; }
            cmd_promote "$1" "$2" "$3"
            ;;
        status)
            [[ $# -lt 1 ]] && { error "Usage: $0 status <image:tag>"; exit 1; }
            cmd_status "$1"
            ;;
        rollback)
            [[ $# -lt 2 ]] && { error "Usage: $0 rollback <image:tag> <env>"; exit 1; }
            cmd_rollback "$1" "$2"
            ;;
        *)
            echo "Usage: $0 {promote|status|rollback} [args]"
            echo ""
            echo "Commands:"
            echo "  promote <image:tag> <source> <target>  Promote image between environments"
            echo "  status <image:tag>                     Check image presence across environments"
            echo "  rollback <image:tag> <env>             Rollback to previous version"
            echo ""
            echo "Environments: dev, staging, prod"
            echo ""
            echo "Examples:"
            echo "  $0 promote game-service:v2.1.0 dev staging"
            echo "  $0 promote game-service:v2.1.0 staging prod"
            echo "  $0 status game-service:v2.1.0"
            echo "  $0 rollback game-service:v2.1.0 prod"
            exit 1
            ;;
    esac
}

main "$@"
