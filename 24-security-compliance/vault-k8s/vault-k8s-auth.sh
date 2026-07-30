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

# =============================================================================
# Vault Kubernetes Auth Backend Setup for iGaming Platform
# =============================================================================
# Configures Vault's Kubernetes authentication backend with per-namespace
# service accounts and short-lived token policies. Each gambling platform
# service gets scoped access to only its required secrets.
#
# Prerequisites:
#   - Vault deployed and initialized (see vault-helm-values.yaml)
#   - kubectl configured with cluster-admin access
#   - VAULT_ADDR and VAULT_TOKEN environment variables set
#
# Usage:
#   ./vault-k8s-auth.sh [--cluster CLUSTER_NAME] [--env prod|staging|dev]
# =============================================================================

set -euo pipefail

VAULT_ADDR="${VAULT_ADDR:?VAULT_ADDR must be set}"
VAULT_TOKEN="${VAULT_TOKEN:?VAULT_TOKEN must be set}"
CLUSTER_NAME="${CLUSTER_NAME:-acme-casino-prod}"
ENVIRONMENT="${ENVIRONMENT:-prod}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Gambling platform namespaces and their Vault access requirements
declare -A NAMESPACE_SECRETS=(
    ["game-server"]="secret/data/game-server,secret/data/shared/rng-config,pki/issue/game-server"
    ["payment"]="secret/data/payment,secret/data/shared/encryption-keys,pki/issue/payment,transit/encrypt/payment"
    ["player-account"]="secret/data/player-account,secret/data/shared/encryption-keys,pki/issue/player-account"
    ["backoffice"]="secret/data/backoffice,secret/data/shared/ldap-config,pki/issue/backoffice"
    ["kyc"]="secret/data/kyc,secret/data/shared/encryption-keys,secret/data/kyc/vendor-api-keys"
    ["bonus-engine"]="secret/data/bonus-engine,secret/data/shared/cache-config"
    ["reporting"]="secret/data/reporting,secret/data/shared/db-readonly"
    ["monitoring"]="secret/data/monitoring"
)

# Token TTLs - shorter for more sensitive services
declare -A TOKEN_TTLS=(
    ["game-server"]="30m"
    ["payment"]="15m"      # Payment secrets rotate most frequently
    ["player-account"]="30m"
    ["backoffice"]="1h"
    ["kyc"]="30m"
    ["bonus-engine"]="1h"
    ["reporting"]="2h"
    ["monitoring"]="4h"
)

log_info()  { echo -e "\033[0;34m[INFO]\033[0m  $*"; }
log_ok()    { echo -e "\033[0;32m[OK]\033[0m    $*"; }
log_error() { echo -e "\033[0;31m[ERROR]\033[0m $*"; }

# ---------------------------------------------------------------------------
# Convert a Go-style duration (e.g. 15m, 30m, 1h, 2h, 4h) to whole seconds.
# ${ttl%m} only strips a trailing "m", so it silently mishandles hour values
# and aborts the role-creation loop under set -e; this handles both units.
# ---------------------------------------------------------------------------
ttl_to_seconds() {
    local ttl="$1"
    local unit="${ttl: -1}"
    local value="${ttl%[a-z]}"

    if ! [[ "${value}" =~ ^[0-9]+$ ]]; then
        log_error "Invalid TTL value: ${ttl}"
        exit 1
    fi

    case "${unit}" in
        s) echo "${value}" ;;
        m) echo $((value * 60)) ;;
        h) echo $((value * 3600)) ;;
        *)
            log_error "Unsupported TTL unit in '${ttl}' (expected s, m, or h)"
            exit 1
            ;;
    esac
}

# ---------------------------------------------------------------------------
# Enable and configure Kubernetes auth backend
# ---------------------------------------------------------------------------
setup_k8s_auth() {
    log_info "Enabling Kubernetes auth backend for cluster: ${CLUSTER_NAME}..."

    # Enable the auth method (idempotent)
    vault auth enable -path="kubernetes/${CLUSTER_NAME}" kubernetes 2>/dev/null || \
        log_info "Kubernetes auth already enabled for ${CLUSTER_NAME}"

    # Get the Kubernetes API server details
    local k8s_host
    k8s_host=$(kubectl config view --minify -o jsonpath='{.clusters[0].cluster.server}')

    local k8s_ca_cert
    k8s_ca_cert=$(kubectl config view --raw --minify -o jsonpath='{.clusters[0].cluster.certificate-authority-data}' | base64 -d)

    # Create a service account for Vault's token reviewer
    kubectl apply -f - << EOF
apiVersion: v1
kind: ServiceAccount
metadata:
  name: vault-auth
  namespace: vault
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  name: vault-auth-tokenreview
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: ClusterRole
  name: system:auth-delegator
subjects:
  - kind: ServiceAccount
    name: vault-auth
    namespace: vault
---
apiVersion: v1
kind: Secret
metadata:
  name: vault-auth-token
  namespace: vault
  annotations:
    kubernetes.io/service-account.name: vault-auth
type: kubernetes.io/service-account-token
EOF

    # Wait for token to be populated
    sleep 5

    local reviewer_token
    reviewer_token=$(kubectl get secret vault-auth-token -n vault \
        -o jsonpath='{.data.token}' | base64 -d)

    # Configure the auth backend
    vault write "auth/kubernetes/${CLUSTER_NAME}/config" \
        kubernetes_host="${k8s_host}" \
        kubernetes_ca_cert="${k8s_ca_cert}" \
        token_reviewer_jwt="${reviewer_token}" \
        issuer="https://kubernetes.default.svc.cluster.local"

    log_ok "Kubernetes auth backend configured for ${CLUSTER_NAME}"
}

# ---------------------------------------------------------------------------
# Enable secret engines
# ---------------------------------------------------------------------------
setup_secret_engines() {
    log_info "Configuring secret engines..."

    # KV v2 for application secrets
    vault secrets enable -path=secret -version=2 kv 2>/dev/null || \
        log_info "KV v2 already enabled"

    # Transit engine for encryption-as-a-service
    # Used by payment service for card tokenization
    vault secrets enable -path=transit transit 2>/dev/null || \
        log_info "Transit engine already enabled"

    # Create transit encryption key for payment data
    vault write transit/keys/payment \
        type="aes256-gcm96" \
        min_decryption_version=1 \
        min_encryption_version=1 \
        deletion_allowed=false \
        exportable=false  # Key material must never leave Vault

    # PKI engine for internal TLS certificates
    vault secrets enable -path=pki pki 2>/dev/null || \
        log_info "PKI engine already enabled"

    vault secrets tune -max-lease-ttl=87600h pki

    # Generate root CA (in production, use an external CA)
    vault write pki/root/generate/internal \
        common_name="Acme Casino Internal CA" \
        ttl=87600h \
        key_bits=4096

    # Create PKI roles for each service
    for namespace in "${!NAMESPACE_SECRETS[@]}"; do
        vault write "pki/roles/${namespace}" \
            allowed_domains="${namespace}.svc.cluster.local,${namespace}.acme-casino.internal" \
            allow_subdomains=true \
            max_ttl="720h" \
            key_bits=2048 \
            key_type="rsa" \
            require_cn=false \
            generate_lease=true
    done

    # Database secrets engine for dynamic database credentials
    vault secrets enable -path=database database 2>/dev/null || \
        log_info "Database engine already enabled"

    log_ok "Secret engines configured"
}

# ---------------------------------------------------------------------------
# Create namespace-specific policies
# ---------------------------------------------------------------------------
create_namespace_policies() {
    log_info "Creating namespace-specific Vault policies..."

    for namespace in "${!NAMESPACE_SECRETS[@]}"; do
        local policy_name="${ENVIRONMENT}-${namespace}"
        local ttl="${TOKEN_TTLS[${namespace}]:-1h}"

        log_info "Creating policy: ${policy_name} (TTL: ${ttl})"

        # Check if a custom HCL policy file exists
        local policy_file="${SCRIPT_DIR}/namespace-policies/${namespace}.hcl"
        if [[ -f "${policy_file}" ]]; then
            log_info "Using custom policy file: ${policy_file}"
            vault policy write "${policy_name}" "${policy_file}"
        else
            # Generate policy from the namespace secrets mapping
            local policy_hcl=""
            IFS=',' read -ra secret_paths <<< "${NAMESPACE_SECRETS[${namespace}]}"

            for secret_path in "${secret_paths[@]}"; do
                # Determine capabilities based on path
                local capabilities='["read", "list"]'
                if [[ "${secret_path}" == pki/* ]]; then
                    capabilities='["create", "update"]'
                elif [[ "${secret_path}" == transit/* ]]; then
                    capabilities='["create", "update"]'
                fi

                policy_hcl+="
# Access to ${secret_path}
path \"${secret_path}\" {
  capabilities = ${capabilities}
}
path \"${secret_path}/*\" {
  capabilities = ${capabilities}
}
"
            done

            # Add token self-management (required for renewal)
            policy_hcl+='
# Token self-management
path "auth/token/lookup-self" {
  capabilities = ["read"]
}
path "auth/token/renew-self" {
  capabilities = ["update"]
}

# Deny access to sys endpoints (defense in depth)
path "sys/*" {
  capabilities = ["deny"]
}
'

            echo "${policy_hcl}" | vault policy write "${policy_name}" -
        fi

        log_ok "Policy ${policy_name} created"
    done
}

# ---------------------------------------------------------------------------
# Create Kubernetes auth roles for each namespace
# ---------------------------------------------------------------------------
create_k8s_auth_roles() {
    log_info "Creating Kubernetes auth roles..."

    for namespace in "${!NAMESPACE_SECRETS[@]}"; do
        local role_name="${ENVIRONMENT}-${namespace}"
        local policy_name="${ENVIRONMENT}-${namespace}"
        local ttl="${TOKEN_TTLS[${namespace}]:-1h}"

        log_info "Creating role: ${role_name} (namespace: ${namespace}, TTL: ${ttl})"

        # Create service account in Kubernetes namespace
        kubectl create namespace "${namespace}" 2>/dev/null || true

        kubectl apply -f - << EOF
apiVersion: v1
kind: ServiceAccount
metadata:
  name: vault-auth
  namespace: ${namespace}
  labels:
    app.kubernetes.io/managed-by: vault-k8s-auth
    environment: ${ENVIRONMENT}
EOF

        # Create Vault role binding the K8s service account to the policy.
        # Only the dedicated vault-auth SA may authenticate - not the namespace
        # "default" SA, which every pod in the namespace gets automatically and
        # would otherwise inherit this role's policy without being provisioned
        # for it.
        local ttl_seconds max_ttl
        ttl_seconds=$(ttl_to_seconds "${ttl}")
        max_ttl="$((ttl_seconds * 2))s"

        # No `period`: periodic tokens renew indefinitely and never fall back
        # through TokenReview, bypassing max_ttl. Tokens must re-authenticate
        # once max_ttl is reached.
        vault write "auth/kubernetes/${CLUSTER_NAME}/role/${role_name}" \
            bound_service_account_names="vault-auth" \
            bound_service_account_namespaces="${namespace}" \
            policies="${policy_name}" \
            ttl="${ttl}" \
            max_ttl="${max_ttl}" \
            token_type="service"

        log_ok "Role ${role_name} created"
    done
}

# ---------------------------------------------------------------------------
# Seed initial secrets for gambling platform services
# ---------------------------------------------------------------------------
seed_initial_secrets() {
    log_info "Seeding initial secrets for ${ENVIRONMENT}..."

    # Shared secrets used across services
    vault kv put "secret/shared/encryption-keys" \
        pii_encryption_key="$(openssl rand -hex 32)" \
        token_signing_key="$(openssl rand -hex 64)" \
        note="Auto-generated. Rotate via vault-rotate-keys.sh"

    vault kv put "secret/shared/cache-config" \
        redis_host="redis-cluster.cache.svc.cluster.local" \
        redis_port="6379" \
        redis_password="$(openssl rand -base64 32)"

    # Game server secrets
    vault kv put "secret/game-server/config" \
        rng_provider="certified-rng-service" \
        rng_api_key="$(openssl rand -hex 32)" \
        session_secret="$(openssl rand -hex 64)" \
        max_bet_limit="10000" \
        house_edge_config="standard"

    # Payment service secrets
    vault kv put "secret/payment/stripe" \
        api_key="sk_live_REPLACE_ME" \
        webhook_secret="whsec_REPLACE_ME" \
        note="Replace with actual Stripe credentials"

    vault kv put "secret/payment/config" \
        db_host="payment-db.rds.amazonaws.com" \
        db_name="payment_${ENVIRONMENT}" \
        db_username="payment_svc" \
        db_password="$(openssl rand -base64 32)" \
        encryption_key="$(openssl rand -hex 32)"

    # KYC service vendor API keys
    vault kv put "secret/kyc/vendor-api-keys" \
        onfido_api_key="REPLACE_ME" \
        jumio_api_key="REPLACE_ME" \
        sumsub_api_key="REPLACE_ME" \
        note="Replace with actual vendor API keys"

    log_ok "Initial secrets seeded for ${ENVIRONMENT}"
    log_info "IMPORTANT: Replace placeholder values with actual credentials"
}

# ---------------------------------------------------------------------------
# Setup audit logging (regulatory requirement)
# ---------------------------------------------------------------------------
setup_audit() {
    log_info "Configuring audit logging..."

    # File audit backend (already in Helm values, ensure it's enabled)
    vault audit enable file \
        file_path="/vault/audit/vault-audit.log" \
        log_raw=false \
        hmac_accessor=true 2>/dev/null || \
        log_info "File audit already enabled"

    # Syslog audit for SIEM integration
    vault audit enable -path=syslog syslog \
        tag="vault" \
        facility="AUTH" \
        log_raw=false 2>/dev/null || \
        log_info "Syslog audit already enabled"

    log_ok "Audit logging configured"
    log_info "All secret access will be logged for regulatory compliance"
}

# ---------------------------------------------------------------------------
# Verify setup
# ---------------------------------------------------------------------------
verify_setup() {
    log_info "Verifying Vault Kubernetes auth setup..."

    echo ""
    echo "=== Auth Methods ==="
    vault auth list

    echo ""
    echo "=== Policies ==="
    vault policy list

    echo ""
    echo "=== Secret Engines ==="
    vault secrets list

    echo ""
    echo "=== Audit Devices ==="
    vault audit list

    echo ""
    echo "=== Kubernetes Roles ==="
    for namespace in "${!NAMESPACE_SECRETS[@]}"; do
        local role_name="${ENVIRONMENT}-${namespace}"
        echo "  ${role_name}:"
        vault read "auth/kubernetes/${CLUSTER_NAME}/role/${role_name}" \
            -format=json 2>/dev/null | \
            jq '{ttl: .data.token_ttl, max_ttl: .data.token_max_ttl, policies: .data.token_policies}' || \
            echo "    (not found)"
    done

    log_ok "Verification complete"
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
main() {
    while [[ $# -gt 0 ]]; do
        case "${1}" in
            --cluster) CLUSTER_NAME="${2}"; shift 2 ;;
            --env)     ENVIRONMENT="${2}"; shift 2 ;;
            --verify-only) verify_setup; exit 0 ;;
            --help|-h)
                echo "Usage: $0 [--cluster NAME] [--env prod|staging|dev] [--verify-only]"
                exit 0
                ;;
            *) shift ;;
        esac
    done

    echo "=============================================="
    echo " Vault Kubernetes Auth Setup"
    echo " Cluster:     ${CLUSTER_NAME}"
    echo " Environment: ${ENVIRONMENT}"
    echo "=============================================="

    setup_k8s_auth
    setup_secret_engines
    create_namespace_policies
    create_k8s_auth_roles
    seed_initial_secrets
    setup_audit
    verify_setup

    echo ""
    log_ok "Vault Kubernetes auth setup complete"
    echo ""
    echo "Next steps:"
    echo "  1. Replace placeholder secrets with actual credentials"
    echo "  2. Annotate pods with vault.hashicorp.com/agent-inject annotations"
    echo "  3. Test auth: vault write auth/kubernetes/${CLUSTER_NAME}/login role=<role> jwt=<sa-token>"
    echo "  4. Monitor audit logs: tail -f /vault/audit/vault-audit.log"
}

main "$@"
