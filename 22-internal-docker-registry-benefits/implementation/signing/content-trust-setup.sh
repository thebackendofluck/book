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

# shellcheck disable=SC2012,SC2086,SC2155
# =============================================================================
# Docker Content Trust & Cosign Setup — iGaming Image Signing Pipeline
# =============================================================================
# Implements image signing using Cosign (Sigstore) for cryptographic
# verification of container images before deployment. Ensures only
# authorized, scanned images reach production (GLI-33 requirement).
#
# Prerequisites:
#   - cosign >= 2.2.0 (https://github.com/sigstore/cosign)
#   - crane (optional, for image inspection)
#   - jq, openssl
#
# Usage:
#   ./content-trust-setup.sh init                    # Initialize signing keys
#   ./content-trust-setup.sh sign <image>             # Sign an image
#   ./content-trust-setup.sh verify <image>           # Verify image signature
#   ./content-trust-setup.sh sign-and-attest <image>  # Sign + attach attestations
#   ./content-trust-setup.sh setup-harbor             # Configure Harbor RBAC & projects
#   ./content-trust-setup.sh rotate-keys              # Rotate signing keys
# =============================================================================
set -euo pipefail

HARBOR_URL="${HARBOR_URL:-https://registry.casino-platform.internal}"
HARBOR_USER="${HARBOR_USER:-admin}"
HARBOR_PASS="${HARBOR_PASS:?HARBOR_PASS must be set}"
KEY_DIR="${KEY_DIR:-/etc/casino/signing-keys}"
COSIGN_KEY="${KEY_DIR}/cosign.key"
COSIGN_PUB="${KEY_DIR}/cosign.pub"
COSIGN_PASSWORD="${COSIGN_PASSWORD:?COSIGN_PASSWORD must be set}"

# Signing identity annotations
SIGNER_IDENTITY="${SIGNER_IDENTITY:-casino-ci-pipeline}"
SIGNER_EMAIL="${SIGNER_EMAIL:-security@casino-platform.internal}"

ALERT_WEBHOOK="${ALERT_WEBHOOK:-}"

mkdir -p "${KEY_DIR}"

log() { echo "[$(date +'%Y-%m-%d %H:%M:%S')] [signing] $*"; }
error() { echo "[$(date +'%Y-%m-%d %H:%M:%S')] [signing] ERROR: $*" >&2; }

# --- Initialize Signing Keys -------------------------------------------------
cmd_init() {
    log "Initializing Cosign signing keys..."

    if [[ -f "${COSIGN_KEY}" ]]; then
        error "Keys already exist at ${KEY_DIR}. Use 'rotate-keys' to replace."
        exit 1
    fi

    # Generate key pair
    COSIGN_PASSWORD="${COSIGN_PASSWORD}" cosign generate-key-pair \
        --output-key-prefix "${KEY_DIR}/cosign"

    # Set restrictive permissions
    chmod 600 "${COSIGN_KEY}"
    chmod 644 "${COSIGN_PUB}"

    log "Signing keys generated:"
    log "  Private key: ${COSIGN_KEY} (mode 600)"
    log "  Public key:  ${COSIGN_PUB} (mode 644)"

    # Store public key as K8s secret for admission controller verification
    kubectl create secret generic cosign-public-key \
        --from-file=cosign.pub="${COSIGN_PUB}" \
        --namespace=kube-system \
        --dry-run=client -o yaml | kubectl apply -f -

    log "Public key stored as K8s secret 'cosign-public-key' in kube-system namespace"

    # Backup private key to Vault (if available)
    if command -v vault &>/dev/null; then
        log "Backing up private key to HashiCorp Vault..."
        vault kv put secret/casino/signing/cosign \
            private_key=@"${COSIGN_KEY}" \
            public_key=@"${COSIGN_PUB}" \
            created_at="$(date -u +'%Y-%m-%dT%H:%M:%SZ')"
        log "Key backup stored in Vault at secret/casino/signing/cosign"
    fi

    echo ""
    log "IMPORTANT: Back up ${COSIGN_KEY} securely. Loss means you cannot verify existing signatures."
}

# --- Sign Image ---------------------------------------------------------------
cmd_sign() {
    local image="$1"

    log "Signing image: ${image}"

    # Verify image exists and get digest
    local digest
    digest=$(crane digest "${image}" 2>/dev/null || \
        cosign triangulate "${image}" 2>/dev/null | head -1)

    if [[ -z "${digest}" ]]; then
        error "Cannot resolve image: ${image}"
        exit 1
    fi

    log "  Image digest: ${digest}"

    # Sign with annotations (metadata for audit trail)
    COSIGN_PASSWORD="${COSIGN_PASSWORD}" cosign sign \
        --key "${COSIGN_KEY}" \
        --annotations "signer=${SIGNER_IDENTITY}" \
        --annotations "signer-email=${SIGNER_EMAIL}" \
        --annotations "signed-at=$(date -u +'%Y-%m-%dT%H:%M:%SZ')" \
        --annotations "pipeline-run=${CI_PIPELINE_ID:-manual}" \
        --annotations "git-commit=${CI_COMMIT_SHA:-unknown}" \
        --annotations "compliance=GLI-33" \
        --tlog-upload=false \
        --yes \
        "${image}"

    log "Image signed successfully: ${image}"
}

# --- Verify Image Signature ---------------------------------------------------
cmd_verify() {
    local image="$1"

    log "Verifying signature for: ${image}"

    local result
    if result=$(cosign verify \
        --key "${COSIGN_PUB}" \
        --annotations "signer=${SIGNER_IDENTITY}" \
        "${image}" 2>&1); then

        log "VERIFIED: Signature valid for ${image}"

        # Display signing metadata
        echo "${result}" | jq -r '.[0].optional // empty' 2>/dev/null | while IFS= read -r line; do
            log "  ${line}"
        done

        return 0
    else
        error "VERIFICATION FAILED for ${image}"
        error "  ${result}"

        if [[ -n "${ALERT_WEBHOOK}" ]]; then
            curl -sf -X POST "${ALERT_WEBHOOK}" \
                -H "Content-Type: application/json" \
                -d "{\"text\": \":rotating_light: *SIGNATURE VERIFICATION FAILED*\nImage: \`${image}\`\nThis may indicate supply chain tampering.\"}" \
                || true
        fi

        return 1
    fi
}

# --- Sign and Attach Attestations ---------------------------------------------
cmd_sign_and_attest() {
    local image="$1"

    log "Signing image with full attestation chain: ${image}"

    # Step 1: Sign the image
    cmd_sign "${image}"

    # Step 2: Attach vulnerability scan attestation (if Trivy report exists)
    local safe_name
    safe_name=$(echo "${image}" | tr '/:' '__')
    local trivy_report
    trivy_report=$(ls -t /var/reports/trivy/${safe_name}_*.json 2>/dev/null | head -1)

    if [[ -n "${trivy_report}" && -f "${trivy_report}" ]]; then
        log "  Attaching vulnerability scan attestation..."

        # Create in-toto attestation from Trivy report
        local attestation_file="/tmp/${safe_name}_vuln_attestation.json"
        jq -n \
            --arg image "${image}" \
            --slurpfile scan "${trivy_report}" \
            '{
                "_type": "https://in-toto.io/Statement/v0.1",
                "predicateType": "https://cosign.sigstore.dev/attestation/vuln/v1",
                "subject": [{"name": $image}],
                "predicate": {
                    "scanner": {"uri": "https://github.com/aquasecurity/trivy", "version": "0.50+"},
                    "metadata": {
                        "scanStartedOn": (now | todate),
                        "scanFinishedOn": (now | todate)
                    },
                    "scanner_result": $scan[0]
                }
            }' > "${attestation_file}"

        COSIGN_PASSWORD="${COSIGN_PASSWORD}" cosign attest \
            --key "${COSIGN_KEY}" \
            --predicate "${attestation_file}" \
            --type vuln \
            --tlog-upload=false \
            --yes \
            "${image}"

        rm -f "${attestation_file}"
        log "  Vulnerability attestation attached"
    fi

    # Step 3: Attach SBOM attestation (if exists)
    local sbom_report
    sbom_report=$(ls -t /var/reports/sbom/${safe_name}_*.json 2>/dev/null | head -1)

    if [[ -n "${sbom_report}" && -f "${sbom_report}" ]]; then
        log "  Attaching SBOM attestation..."
        COSIGN_PASSWORD="${COSIGN_PASSWORD}" cosign attest \
            --key "${COSIGN_KEY}" \
            --predicate "${sbom_report}" \
            --type spdxjson \
            --tlog-upload=false \
            --yes \
            "${image}"
        log "  SBOM attestation attached"
    fi

    log "Full attestation chain complete for ${image}"
}

# --- Setup Harbor Projects and RBAC -------------------------------------------
cmd_setup_harbor() {
    log "Configuring Harbor projects and RBAC..."

    # Create projects
    local projects=("casino-base" "casino-games" "casino-platform" "casino-backoffice" "casino-monitoring" "casino-third-party")

    for project in "${projects[@]}"; do
        log "  Creating project: ${project}"
        curl -sk -u "${HARBOR_USER}:${HARBOR_PASS}" \
            -X POST "${HARBOR_URL}/api/v2.0/projects" \
            -H "Content-Type: application/json" \
            -d "{
                \"project_name\": \"${project}\",
                \"metadata\": {
                    \"public\": \"false\",
                    \"enable_content_trust\": \"true\",
                    \"enable_content_trust_cosign\": \"true\",
                    \"auto_scan\": \"true\",
                    \"prevent_vul\": \"true\",
                    \"severity\": \"critical\"
                },
                \"storage_limit\": 107374182400
            }" 2>/dev/null || log "    Project ${project} may already exist"
    done

    # Create robot accounts for CI/CD
    log "  Creating CI/CD robot account..."
    local robot_response
    robot_response=$(curl -sk -u "${HARBOR_USER}:${HARBOR_PASS}" \
        -X POST "${HARBOR_URL}/api/v2.0/robots" \
        -H "Content-Type: application/json" \
        -d '{
            "name": "ci-pipeline",
            "description": "CI/CD pipeline robot account",
            "duration": 365,
            "level": "system",
            "permissions": [
                {
                    "kind": "project",
                    "namespace": "*",
                    "access": [
                        {"resource": "repository", "action": "push"},
                        {"resource": "repository", "action": "pull"},
                        {"resource": "artifact", "action": "read"},
                        {"resource": "artifact-label", "action": "create"},
                        {"resource": "tag", "action": "create"},
                        {"resource": "scan", "action": "create"}
                    ]
                }
            ]
        }' 2>/dev/null)

    local robot_secret
    robot_secret=$(echo "${robot_response}" | jq -r '.secret // empty')
    if [[ -n "${robot_secret}" ]]; then
        log "  Robot account created. Store the secret securely:"
        log "    Username: robot\$ci-pipeline"
        log "    Secret:   ${robot_secret}"

        # Store in K8s secret
        kubectl create secret docker-registry harbor-ci-credentials \
            --docker-server="${HARBOR_URL}" \
            --docker-username="robot\$ci-pipeline" \
            --docker-password="${robot_secret}" \
            --namespace=cicd \
            --dry-run=client -o yaml | kubectl apply -f -
    fi

    # Enable immutable tags for production images
    log "  Configuring immutable tag rules..."
    for project in "casino-platform" "casino-games"; do
        curl -sk -u "${HARBOR_USER}:${HARBOR_PASS}" \
            -X POST "${HARBOR_URL}/api/v2.0/projects/${project}/immutabletagrules" \
            -H "Content-Type: application/json" \
            -d '{
                "tag_selectors": [{"kind": "doublestar", "decoration": "matches", "pattern": "v*"}],
                "scope_selectors": {"repository": [{"kind": "doublestar", "decoration": "repoMatches", "pattern": "**"}]}
            }' 2>/dev/null || true
    done

    log "Harbor setup complete"
}

# --- Rotate Signing Keys -----------------------------------------------------
cmd_rotate_keys() {
    log "Rotating signing keys..."
    log "WARNING: This will generate new keys. Old signatures will remain valid"
    log "         but new images will be signed with the new key."

    # Archive old keys
    local archive_dir="${KEY_DIR}/archived/$(date +'%Y%m%d_%H%M%S')"
    mkdir -p "${archive_dir}"

    if [[ -f "${COSIGN_KEY}" ]]; then
        cp "${COSIGN_KEY}" "${archive_dir}/cosign.key"
        cp "${COSIGN_PUB}" "${archive_dir}/cosign.pub"
        log "  Old keys archived to ${archive_dir}"
    fi

    # Remove old keys
    rm -f "${COSIGN_KEY}" "${COSIGN_PUB}"

    # Generate new keys
    cmd_init

    log "Key rotation complete. Update all verification policies with new public key."
    log "Archived keys location: ${archive_dir}"
}

# --- Main ---------------------------------------------------------------------
main() {
    local cmd="${1:-help}"
    shift || true

    case "${cmd}" in
        init)
            cmd_init
            ;;
        sign)
            [[ $# -lt 1 ]] && { error "Usage: $0 sign <image>"; exit 1; }
            cmd_sign "$1"
            ;;
        verify)
            [[ $# -lt 1 ]] && { error "Usage: $0 verify <image>"; exit 1; }
            cmd_verify "$1"
            ;;
        sign-and-attest)
            [[ $# -lt 1 ]] && { error "Usage: $0 sign-and-attest <image>"; exit 1; }
            cmd_sign_and_attest "$1"
            ;;
        setup-harbor)
            cmd_setup_harbor
            ;;
        rotate-keys)
            cmd_rotate_keys
            ;;
        *)
            echo "Usage: $0 {init|sign|verify|sign-and-attest|setup-harbor|rotate-keys} [args]"
            echo ""
            echo "Commands:"
            echo "  init                    Generate Cosign signing key pair"
            echo "  sign <image>            Sign a container image"
            echo "  verify <image>          Verify image signature"
            echo "  sign-and-attest <image> Sign + attach vuln/SBOM attestations"
            echo "  setup-harbor            Configure Harbor projects, RBAC, and policies"
            echo "  rotate-keys             Rotate signing keys (archives old keys)"
            exit 1
            ;;
    esac
}

main "$@"
