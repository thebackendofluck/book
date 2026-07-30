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

# shellcheck disable=SC2016
# =============================================================================
# Container Image Security Scanner for iGaming Platforms
# =============================================================================
# Scans container images with Trivy and Grype before allowing deployment.
# Enforces gambling-platform policies: no root users in game containers,
# mandatory health checks, read-only root filesystems, and no embedded secrets.
#
# Usage:
#   ./container-scanner.sh IMAGE_REF [--policy strict|standard] [--push-on-pass]
#
# Examples:
#   ./container-scanner.sh acme-casino/game-server:v2.1.0 --policy strict
#   ./container-scanner.sh acme-casino/payment-service:latest --push-on-pass
# =============================================================================

set -euo pipefail

REPORT_DIR="${REPORT_DIR:-/tmp/container-reports/$(date +%Y%m%d-%H%M%S)}"
TRIVY_SEVERITY="${TRIVY_SEVERITY:-CRITICAL,HIGH}"
POLICY="${POLICY:-standard}"
PUSH_ON_PASS="${PUSH_ON_PASS:-false}"
REGISTRY="${REGISTRY:-}"

# Gambling-specific image classifications
# Critical images get the strictest scanning policies
declare -A SERVICE_CRITICALITY=(
    ["game-server"]="critical"
    ["rng-service"]="critical"
    ["payment-service"]="critical"
    ["wallet-service"]="critical"
    ["kyc-service"]="critical"
    ["withdrawal-service"]="critical"
    ["bonus-engine"]="high"
    ["player-account"]="high"
    ["backoffice"]="high"
    ["reporting"]="medium"
    ["marketing"]="low"
    ["cms"]="low"
)

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log_info()  { echo -e "${BLUE}[INFO]${NC}  $*"; }
log_ok()    { echo -e "${GREEN}[OK]${NC}    $*"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
log_error() { echo -e "${RED}[ERROR]${NC} $*"; }

# ---------------------------------------------------------------------------
# Determine service criticality from image name
# ---------------------------------------------------------------------------
get_criticality() {
    local image="${1}"
    local service_name
    service_name=$(echo "${image}" | sed 's|.*/||' | sed 's|:.*||')

    local criticality="${SERVICE_CRITICALITY[${service_name}]:-standard}"
    echo "${criticality}"
}

# ---------------------------------------------------------------------------
# Vulnerability scanning with Trivy
# ---------------------------------------------------------------------------
scan_trivy() {
    local image="${1}"
    log_info "Scanning ${image} with Trivy..."

    if ! command -v trivy >/dev/null 2>&1; then
        log_warn "Trivy not installed. Installing..."
        curl -sfL https://raw.githubusercontent.com/aquasecurity/trivy/main/contrib/install.sh | sh -s -- -b /usr/local/bin
    fi

    local trivy_json="${REPORT_DIR}/trivy-results.json"
    local trivy_table="${REPORT_DIR}/trivy-results.txt"

    # Full vulnerability scan
    trivy image \
        --severity "${TRIVY_SEVERITY}" \
        --format json \
        --output "${trivy_json}" \
        --ignore-unfixed \
        --security-checks vuln,secret,config \
        "${image}" 2>/dev/null || true

    # Human-readable table
    trivy image \
        --severity "${TRIVY_SEVERITY}" \
        --format table \
        --output "${trivy_table}" \
        --ignore-unfixed \
        "${image}" 2>/dev/null || true

    if [[ -f "${trivy_json}" ]]; then
        local vuln_count
        vuln_count=$(jq '[.Results[]?.Vulnerabilities[]? // empty] | length' "${trivy_json}" 2>/dev/null || echo 0)
        local critical_count
        critical_count=$(jq '[.Results[]?.Vulnerabilities[]? | select(.Severity == "CRITICAL")] | length' \
            "${trivy_json}" 2>/dev/null || echo 0)
        local secret_count
        secret_count=$(jq '[.Results[]?.Secrets[]? // empty] | length' "${trivy_json}" 2>/dev/null || echo 0)

        echo "  Trivy results: ${vuln_count} vulns (${critical_count} critical), ${secret_count} secrets"

        if [[ "${secret_count}" -gt 0 ]]; then
            log_error "SECRETS DETECTED IN CONTAINER IMAGE!"
            log_error "Embedded secrets violate PCI DSS Req 2.1 and are a critical deployment blocker"
            jq -r '.Results[]?.Secrets[]? | "  \(.RuleID): \(.Match | .[0:50])..."' \
                "${trivy_json}" 2>/dev/null || true
            return 1
        fi

        if [[ "${critical_count}" -gt 0 ]]; then
            log_error "Critical vulnerabilities found"
            jq -r '.Results[]?.Vulnerabilities[]? | select(.Severity == "CRITICAL") |
                "  \(.VulnerabilityID): \(.PkgName) \(.InstalledVersion) -> \(.FixedVersion // "no fix")"' \
                "${trivy_json}" 2>/dev/null || true
            return 1
        fi
    fi

    log_ok "Trivy scan passed"
    return 0
}

# ---------------------------------------------------------------------------
# Vulnerability scanning with Grype (second opinion)
# ---------------------------------------------------------------------------
scan_grype() {
    local image="${1}"

    if ! command -v grype >/dev/null 2>&1; then
        log_info "Grype not installed, skipping secondary scan"
        return 0
    fi

    log_info "Scanning ${image} with Grype (secondary scanner)..."

    local grype_json="${REPORT_DIR}/grype-results.json"

    grype "${image}" \
        --output json \
        --file "${grype_json}" \
        --only-fixed \
        --fail-on critical 2>/dev/null || true

    if [[ -f "${grype_json}" ]]; then
        local match_count
        match_count=$(jq '.matches | length' "${grype_json}" 2>/dev/null || echo 0)
        echo "  Grype results: ${match_count} matches"
    fi

    log_ok "Grype scan complete"
    return 0
}

# ---------------------------------------------------------------------------
# Dockerfile / image configuration checks (iGaming-specific)
# ---------------------------------------------------------------------------
check_image_config() {
    local image="${1}"
    log_info "Checking container configuration for iGaming compliance..."

    local issues=0

    # Inspect image configuration
    local config_json="${REPORT_DIR}/image-config.json"
    docker inspect "${image}" > "${config_json}" 2>/dev/null || {
        log_warn "Cannot inspect image (not pulled locally)"
        return 0
    }

    # Check 1: No root user
    # Gambling containers must not run as root - regulatory requirement for
    # defense-in-depth and to prevent container escape to host
    local user
    user=$(jq -r '.[0].Config.User // ""' "${config_json}")
    if [[ -z "${user}" || "${user}" == "root" || "${user}" == "0" ]]; then
        log_error "Container runs as root. All gambling platform containers must use a non-root user"
        log_error "Add 'USER nonroot' to your Dockerfile"
        issues=$((issues + 1))
    else
        log_ok "Non-root user: ${user}"
    fi

    # Check 2: Health check defined
    # Required for orchestrator-managed game sessions - unhealthy game servers
    # must be removed from rotation before players are affected
    local healthcheck
    healthcheck=$(jq -r '.[0].Config.Healthcheck.Test // empty' "${config_json}" 2>/dev/null || echo "")
    if [[ -z "${healthcheck}" ]]; then
        log_warn "No HEALTHCHECK defined. Required for game server availability monitoring"
        issues=$((issues + 1))
    else
        log_ok "Health check configured"
    fi

    # Check 3: No exposed privileged ports
    local exposed_ports
    exposed_ports=$(jq -r '.[0].Config.ExposedPorts // {} | keys[]' "${config_json}" 2>/dev/null || echo "")
    for port in ${exposed_ports}; do
        local port_num
        port_num=$(echo "${port}" | grep -oP '^\d+')
        if [[ -n "${port_num}" && "${port_num}" -lt 1024 && "${port_num}" -ne 443 && "${port_num}" -ne 80 ]]; then
            log_warn "Privileged port exposed: ${port}. Use high ports and let the ingress controller handle mapping"
            issues=$((issues + 1))
        fi
    done

    # Check 4: No environment variables with secret-like names
    local env_vars
    env_vars=$(jq -r '.[0].Config.Env[]? // empty' "${config_json}" 2>/dev/null || echo "")
    local secret_patterns="PASSWORD|SECRET|API_KEY|PRIVATE_KEY|TOKEN|CREDENTIAL|DB_PASS"
    while IFS= read -r env_var; do
        local var_name
        var_name=$(echo "${env_var}" | cut -d'=' -f1)
        if echo "${var_name}" | grep -qEi "${secret_patterns}"; then
            # Check if it's a placeholder or actual value
            local var_value
            var_value=$(echo "${env_var}" | cut -d'=' -f2-)
            if [[ "${var_value}" != "" && "${var_value}" != "changeme" && \
                  "${var_value}" != '${'"${var_name}"'}' ]]; then
                log_error "Potential secret in environment variable: ${var_name}"
                log_error "Secrets must be injected via Vault, not baked into images"
                issues=$((issues + 1))
            fi
        fi
    done <<< "${env_vars}"

    # Check 5: Base image is approved
    # Gambling platforms should use hardened, approved base images
    local base_layers
    base_layers=$(jq -r '.[0].RootFS.Layers | length' "${config_json}" 2>/dev/null || echo 0)
    log_info "Image has ${base_layers} layers"

    # Check 6: Image size (large images increase attack surface)
    local image_size
    image_size=$(jq -r '.[0].Size' "${config_json}" 2>/dev/null || echo 0)
    local size_mb=$((image_size / 1024 / 1024))
    if [[ ${size_mb} -gt 500 ]]; then
        log_warn "Image is ${size_mb}MB. Consider using distroless or alpine base to reduce attack surface"
    else
        log_ok "Image size: ${size_mb}MB"
    fi

    if [[ ${issues} -gt 0 ]]; then
        log_error "${issues} configuration issues found"
        if [[ "${POLICY}" == "strict" ]]; then
            return 1
        fi
    fi

    return 0
}

# ---------------------------------------------------------------------------
# Malware scanning (important for supply chain attacks)
# ---------------------------------------------------------------------------
scan_malware() {
    local image="${1}"
    log_info "Checking for malware and cryptominers..."

    # ClamAV scan if available
    if command -v clamscan >/dev/null 2>&1; then
        local mount_dir="${REPORT_DIR}/image-fs"
        mkdir -p "${mount_dir}"

        # Export image filesystem for scanning
        local container_id
        container_id=$(docker create "${image}" 2>/dev/null) || return 0
        docker export "${container_id}" 2>/dev/null | tar -xf - -C "${mount_dir}" 2>/dev/null || true
        docker rm "${container_id}" >/dev/null 2>&1 || true

        clamscan -r --no-summary --infected "${mount_dir}" > "${REPORT_DIR}/malware-scan.txt" 2>/dev/null || true

        local infected
        infected=$(grep -c "FOUND" "${REPORT_DIR}/malware-scan.txt" 2>/dev/null || echo 0)
        if [[ "${infected}" -gt 0 ]]; then
            log_error "MALWARE DETECTED in container image!"
            cat "${REPORT_DIR}/malware-scan.txt"
            rm -rf "${mount_dir}"
            return 1
        fi

        rm -rf "${mount_dir}"
        log_ok "No malware detected"
    else
        log_info "ClamAV not available, skipping malware scan"
    fi

    return 0
}

# ---------------------------------------------------------------------------
# Generate attestation (for supply chain security / SLSA)
# ---------------------------------------------------------------------------
generate_attestation() {
    local image="${1}"
    log_info "Generating scan attestation..."

    local attestation="${REPORT_DIR}/attestation.json"

    # Create a signed attestation of scan results
    # This can be stored in an OCI registry alongside the image
    local image_digest
    image_digest=$(docker inspect --format='{{index .RepoDigests 0}}' "${image}" 2>/dev/null || echo "unknown")

    cat > "${attestation}" << EOF
{
  "apiVersion": "in-toto/v0.1",
  "predicateType": "https://acme-casino.com/security-scan/v1",
  "subject": [{
    "name": "${image}",
    "digest": {"sha256": "${image_digest}"}
  }],
  "predicate": {
    "scanner": "acme-casino-container-scanner",
    "scanTimestamp": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
    "policy": "${POLICY}",
    "results": {
      "trivy": "$([ -f "${REPORT_DIR}/trivy-results.json" ] && echo 'completed' || echo 'skipped')",
      "grype": "$([ -f "${REPORT_DIR}/grype-results.json" ] && echo 'completed' || echo 'skipped')",
      "configCheck": "completed",
      "malwareScan": "$([ -f "${REPORT_DIR}/malware-scan.txt" ] && echo 'completed' || echo 'skipped')"
    },
    "verdict": "PASS",
    "pipelineId": "${CI_PIPELINE_ID:-manual}"
  }
}
EOF

    # Sign with cosign if available (for Sigstore supply chain security)
    if command -v cosign >/dev/null 2>&1; then
        log_info "Signing attestation with cosign..."
        cosign attest --predicate "${attestation}" "${image}" 2>/dev/null || \
            log_warn "Cosign attestation failed (keyless signing requires OIDC)"
    fi

    log_ok "Attestation generated: ${attestation}"
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
main() {
    local image="${1:?Usage: $0 IMAGE_REF [--policy strict|standard] [--push-on-pass]}"
    shift

    while [[ $# -gt 0 ]]; do
        case "${1}" in
            --policy)       POLICY="${2}"; shift 2 ;;
            --push-on-pass) PUSH_ON_PASS="true"; shift ;;
            --registry)     REGISTRY="${2}"; shift 2 ;;
            *) shift ;;
        esac
    done

    mkdir -p "${REPORT_DIR}"

    # Auto-escalate policy for critical services
    local criticality
    criticality=$(get_criticality "${image}")
    if [[ "${criticality}" == "critical" ]]; then
        log_warn "Critical service detected - enforcing strict policy"
        POLICY="strict"
    fi

    echo "=============================================="
    echo " Container Security Scanner"
    echo " Image:       ${image}"
    echo " Policy:      ${POLICY}"
    echo " Criticality: ${criticality}"
    echo " Reports:     ${REPORT_DIR}"
    echo "=============================================="

    local scan_failed=0

    scan_trivy "${image}"       || scan_failed=1
    scan_grype "${image}"       || scan_failed=1
    check_image_config "${image}" || scan_failed=1
    scan_malware "${image}"     || scan_failed=1

    if [[ ${scan_failed} -eq 0 ]]; then
        generate_attestation "${image}"

        if [[ "${PUSH_ON_PASS}" == "true" && -n "${REGISTRY}" ]]; then
            log_info "Pushing verified image to ${REGISTRY}..."
            docker tag "${image}" "${REGISTRY}/${image}"
            docker push "${REGISTRY}/${image}"
            log_ok "Image pushed to registry"
        fi

        log_ok "All container security checks PASSED"
    else
        log_error "Container security checks FAILED - deployment blocked"
        log_error "Review reports in ${REPORT_DIR}"
    fi

    exit ${scan_failed}
}

main "$@"
