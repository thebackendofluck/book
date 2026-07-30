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
# HashiCorp Vault Initialization for iGaming Platform
# =============================================================================
# Initializes Vault with secret engines, policies, and authentication methods
# tailored for online gambling platform operations.
#
# Usage:
#   ./vault-init.sh                          # Dev mode with default token
#   ./vault-init.sh --vault-addr https://vault.acme-casino.io
#   ./vault-init.sh --init-prod              # Initialize production Vault
#
# Prerequisites:
#   - vault CLI installed
#   - Vault server running and accessible
#   - VAULT_ADDR and VAULT_TOKEN environment variables set
# =============================================================================
set -euo pipefail
IFS=$'\n\t'

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
VAULT_ADDR="${VAULT_ADDR:-http://127.0.0.1:8200}"
VAULT_TOKEN="${VAULT_TOKEN:-igaming-dev-token-change-me}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INIT_PROD="${INIT_PROD:-false}"

export VAULT_ADDR VAULT_TOKEN

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
log()   { echo "[$(date -u +%H:%M:%S)] $*"; }
ok()    { echo "[OK] $*"; }
err()   { echo "[ERROR] $*" >&2; }
fatal() { err "$*"; exit 1; }

# ---------------------------------------------------------------------------
# Pre-flight
# ---------------------------------------------------------------------------
preflight() {
    if ! command -v vault &>/dev/null; then
        fatal "vault CLI not found. Install from https://www.vaultproject.io/downloads"
    fi

    log "Vault address: $VAULT_ADDR"

    # Wait for Vault to be ready
    local retries=30
    while ! vault status &>/dev/null 2>&1; do
        retries=$((retries - 1))
        if [[ "$retries" -le 0 ]]; then
            fatal "Vault not reachable at $VAULT_ADDR after 30 attempts"
        fi
        log "Waiting for Vault... ($retries attempts remaining)"
        sleep 2
    done

    ok "Vault is ready"
}

# ---------------------------------------------------------------------------
# Production Initialization (unseal keys, root token)
# ---------------------------------------------------------------------------
init_production() {
    log "Initializing production Vault..."

    if vault status | grep -q "Initialized.*true"; then
        log "Vault already initialized"
        return 0
    fi

    # Initialize with Shamir's Secret Sharing (5 key shares, 3 threshold)
    local init_output
    init_output=$(vault operator init -key-shares=5 -key-threshold=3 -format=json)

    # CRITICAL: Save unseal keys securely
    echo "$init_output" > "${SCRIPT_DIR}/vault-init-keys.json"
    chmod 600 "${SCRIPT_DIR}/vault-init-keys.json"

    log "CRITICAL: Unseal keys saved to vault-init-keys.json"
    log "CRITICAL: Distribute keys to 5 different key holders immediately"
    log "CRITICAL: Delete this file after distributing keys"

    # Unseal (in production, this would be done by key holders)
    local key1 key2 key3
    key1=$(echo "$init_output" | jq -r '.unseal_keys_b64[0]')
    key2=$(echo "$init_output" | jq -r '.unseal_keys_b64[1]')
    key3=$(echo "$init_output" | jq -r '.unseal_keys_b64[2]')

    vault operator unseal "$key1"
    vault operator unseal "$key2"
    vault operator unseal "$key3"

    # Set root token for further configuration
    VAULT_TOKEN=$(echo "$init_output" | jq -r '.root_token')
    export VAULT_TOKEN

    ok "Vault initialized and unsealed"
}

# ---------------------------------------------------------------------------
# Enable Secret Engines
# ---------------------------------------------------------------------------
enable_secret_engines() {
    log "Enabling secret engines..."

    # KV v2 for static secrets (API keys, credentials)
    vault secrets enable -path=igaming/static -version=2 kv 2>/dev/null || true
    ok "KV v2 engine: igaming/static"

    # Transit engine for encryption-as-a-service (PCI DSS card data)
    vault secrets enable -path=igaming/transit transit 2>/dev/null || true
    ok "Transit engine: igaming/transit"

    # Database engine for dynamic database credentials
    vault secrets enable -path=igaming/database database 2>/dev/null || true
    ok "Database engine: igaming/database"

    # PKI engine for internal TLS certificates
    vault secrets enable -path=igaming/pki pki 2>/dev/null || true
    vault secrets tune -max-lease-ttl=87600h igaming/pki
    ok "PKI engine: igaming/pki"

    # TOTP engine for MFA
    vault secrets enable -path=igaming/totp totp 2>/dev/null || true
    ok "TOTP engine: igaming/totp"
}

# ---------------------------------------------------------------------------
# Create Secret Paths for iGaming Services
# ---------------------------------------------------------------------------
create_secret_paths() {
    log "Creating iGaming secret paths..."

    # Payment service secrets
    vault kv put igaming/static/payment-service/database \
        host="payment-db.acme-casino.internal" \
        port="5432" \
        username="payment_svc" \
        password="CHANGE_ME_USE_DYNAMIC_CREDS" \
        database="payment_db" \
        ssl_mode="verify-full"

    vault kv put igaming/static/payment-service/providers \
        adyen_api_key="CHANGE_ME" \
        adyen_merchant_account="AcmetoCasinoECOM" \
        nuvei_merchant_id="CHANGE_ME" \
        nuvei_merchant_site_id="CHANGE_ME" \
        nuvei_secret_key="CHANGE_ME" \
        worldpay_service_key="CHANGE_ME"

    vault kv put igaming/static/payment-service/encryption \
        card_encryption_key="CHANGE_ME_32_BYTE_KEY" \
        pci_tokenization_key="CHANGE_ME"

    ok "Payment service secrets created"

    # Game engine secrets
    vault kv put igaming/static/game-engine/database \
        host="game-db.acme-casino.internal" \
        port="5432" \
        username="game_svc" \
        password="CHANGE_ME" \
        database="game_db"

    vault kv put igaming/static/game-engine/providers \
        evolution_api_key="CHANGE_ME" \
        pragmatic_api_key="CHANGE_ME" \
        netent_api_key="CHANGE_ME" \
        microgaming_api_key="CHANGE_ME"

    vault kv put igaming/static/game-engine/session \
        jwt_secret="CHANGE_ME_64_BYTE_SECRET" \
        session_encryption_key="CHANGE_ME"

    ok "Game engine secrets created"

    # RNG service secrets
    vault kv put igaming/static/rng-service/config \
        seed_encryption_key="CHANGE_ME_32_BYTE_KEY" \
        database_host="rng-db.acme-casino.internal" \
        database_password="CHANGE_ME" \
        entropy_source="hardware"

    ok "RNG service secrets created"

    # KYC service secrets
    vault kv put igaming/static/kyc-service/providers \
        onfido_api_key="CHANGE_ME" \
        jumio_api_token="CHANGE_ME" \
        sumsub_app_token="CHANGE_ME" \
        gbg_api_key="CHANGE_ME"

    vault kv put igaming/static/kyc-service/database \
        host="kyc-db.acme-casino.internal" \
        password="CHANGE_ME" \
        encryption_key="CHANGE_ME_PII_ENCRYPTION_KEY"

    ok "KYC service secrets created"

    # Anti-fraud service secrets
    vault kv put igaming/static/anti-fraud/config \
        database_password="CHANGE_ME" \
        ml_model_api_key="CHANGE_ME" \
        sift_api_key="CHANGE_ME" \
        maxmind_license_key="CHANGE_ME"

    ok "Anti-fraud service secrets created"

    # Bonus engine secrets
    vault kv put igaming/static/bonus-engine/database \
        host="bonus-db.acme-casino.internal" \
        password="CHANGE_ME"

    ok "Bonus engine secrets created"

    # Platform-wide secrets
    vault kv put igaming/static/platform/jwt \
        access_token_secret="CHANGE_ME_64_BYTES" \
        refresh_token_secret="CHANGE_ME_64_BYTES" \
        issuer="https://auth.acme-casino.io" \
        access_token_ttl="900" \
        refresh_token_ttl="604800"

    vault kv put igaming/static/platform/redis \
        host="redis.acme-casino.internal" \
        port="6379" \
        password="CHANGE_ME" \
        tls_enabled="true"

    vault kv put igaming/static/platform/kafka \
        bootstrap_servers="kafka-0.acme-casino.internal:9093,kafka-1.acme-casino.internal:9093" \
        sasl_username="igaming-platform" \
        sasl_password="CHANGE_ME" \
        security_protocol="SASL_SSL"

    vault kv put igaming/static/platform/smtp \
        host="smtp.acme-casino.io" \
        port="587" \
        username="noreply@acme-casino.io" \
        password="CHANGE_ME"

    vault kv put igaming/static/platform/monitoring \
        datadog_api_key="CHANGE_ME" \
        sentry_dsn="CHANGE_ME" \
        pagerduty_service_key="CHANGE_ME"

    ok "Platform-wide secrets created"
}

# ---------------------------------------------------------------------------
# Configure Transit Encryption Keys (PCI DSS)
# ---------------------------------------------------------------------------
configure_transit() {
    log "Configuring transit encryption keys..."

    # Card data encryption key (AES-256-GCM)
    vault write igaming/transit/keys/card-data \
        type=aes256-gcm96 \
        exportable=false \
        allow_plaintext_backup=false \
        min_decryption_version=1 \
        min_encryption_version=1

    # Player PII encryption key
    vault write igaming/transit/keys/player-pii \
        type=aes256-gcm96 \
        exportable=false

    # Token encryption key (for payment tokenization)
    vault write igaming/transit/keys/payment-token \
        type=aes256-gcm96 \
        exportable=false

    # Audit log encryption key
    vault write igaming/transit/keys/audit-log \
        type=aes256-gcm96 \
        exportable=false

    ok "Transit encryption keys configured"
}

# ---------------------------------------------------------------------------
# Configure PKI (Internal TLS)
# ---------------------------------------------------------------------------
configure_pki() {
    log "Configuring PKI for internal TLS..."

    # Generate root CA
    vault write igaming/pki/root/generate/internal \
        common_name="Acme Casino Internal CA" \
        organization="Acme Casino Ltd" \
        ttl=87600h

    # Configure URLs
    vault write igaming/pki/config/urls \
        issuing_certificates="${VAULT_ADDR}/v1/igaming/pki/ca" \
        crl_distribution_points="${VAULT_ADDR}/v1/igaming/pki/crl"

    # Create role for service certificates
    vault write igaming/pki/roles/igaming-service \
        allowed_domains="acme-casino.internal,acme-casino.svc.cluster.local" \
        allow_subdomains=true \
        max_ttl=720h \
        key_type=ec \
        key_bits=256 \
        require_cn=true \
        enforce_hostnames=true

    ok "PKI configured"
}

# ---------------------------------------------------------------------------
# Apply Policies
# ---------------------------------------------------------------------------
apply_policies() {
    log "Applying Vault policies..."

    # Apply the main policy file
    if [[ -f "${SCRIPT_DIR}/vault-policies.hcl" ]]; then
        vault policy write igaming-policies "${SCRIPT_DIR}/vault-policies.hcl"
        ok "Applied igaming-policies from vault-policies.hcl"
    fi

    # Service-specific policies (inline for completeness)
    vault policy write payment-service - <<'POLICY'
path "igaming/static/data/payment-service/*" {
  capabilities = ["read", "list"]
}
path "igaming/transit/encrypt/card-data" {
  capabilities = ["update"]
}
path "igaming/transit/decrypt/card-data" {
  capabilities = ["update"]
}
path "igaming/transit/encrypt/payment-token" {
  capabilities = ["update"]
}
path "igaming/static/data/platform/redis" {
  capabilities = ["read"]
}
path "igaming/static/data/platform/kafka" {
  capabilities = ["read"]
}
POLICY
    ok "Policy: payment-service"

    vault policy write game-engine - <<'POLICY'
path "igaming/static/data/game-engine/*" {
  capabilities = ["read", "list"]
}
path "igaming/static/data/platform/redis" {
  capabilities = ["read"]
}
path "igaming/static/data/platform/kafka" {
  capabilities = ["read"]
}
POLICY
    ok "Policy: game-engine"

    vault policy write rng-service - <<'POLICY'
path "igaming/static/data/rng-service/*" {
  capabilities = ["read"]
}
# RNG service should have minimal access
# No transit, no cross-service access
POLICY
    ok "Policy: rng-service"

    vault policy write kyc-service - <<'POLICY'
path "igaming/static/data/kyc-service/*" {
  capabilities = ["read", "list"]
}
path "igaming/transit/encrypt/player-pii" {
  capabilities = ["update"]
}
path "igaming/transit/decrypt/player-pii" {
  capabilities = ["update"]
}
POLICY
    ok "Policy: kyc-service"

    vault policy write anti-fraud - <<'POLICY'
path "igaming/static/data/anti-fraud/*" {
  capabilities = ["read"]
}
path "igaming/static/data/platform/kafka" {
  capabilities = ["read"]
}
POLICY
    ok "Policy: anti-fraud"

    vault policy write bonus-engine - <<'POLICY'
path "igaming/static/data/bonus-engine/*" {
  capabilities = ["read"]
}
path "igaming/static/data/platform/redis" {
  capabilities = ["read"]
}
POLICY
    ok "Policy: bonus-engine"

    # Read-only audit policy for compliance team
    vault policy write compliance-auditor - <<'POLICY'
path "igaming/static/metadata/*" {
  capabilities = ["read", "list"]
}
path "sys/audit" {
  capabilities = ["read"]
}
path "sys/policies/acl" {
  capabilities = ["list"]
}
path "sys/policies/acl/*" {
  capabilities = ["read"]
}
# Cannot read actual secret values
path "igaming/static/data/*" {
  capabilities = ["deny"]
}
POLICY
    ok "Policy: compliance-auditor"
}

# ---------------------------------------------------------------------------
# Configure Authentication Methods
# ---------------------------------------------------------------------------
configure_auth() {
    log "Configuring authentication methods..."

    # Kubernetes auth (for pod identity)
    vault auth enable kubernetes 2>/dev/null || true

    # Create Kubernetes auth roles for each service
    for service in payment-service game-engine rng-service kyc-service anti-fraud bonus-engine; do
        namespace="${service}"
        [[ "$service" == "payment-service" ]] && namespace="payment-system"
        [[ "$service" == "kyc-service" ]] && namespace="kyc-system"

        vault write auth/kubernetes/role/"$service" \
            bound_service_account_names="$service" \
            bound_service_account_namespaces="$namespace" \
            policies="$service" \
            ttl=1h \
            max_ttl=4h 2>/dev/null || true
        ok "Kubernetes auth role: $service (namespace: $namespace)"
    done

    # AppRole auth (for CI/CD pipelines)
    vault auth enable approle 2>/dev/null || true

    vault write auth/approle/role/cicd-pipeline \
        secret_id_ttl=10m \
        token_ttl=20m \
        token_max_ttl=30m \
        policies="default" \
        token_num_uses=10

    ok "AppRole auth configured for CI/CD"

    # Userpass auth (for human operators — dev/staging only)
    vault auth enable userpass 2>/dev/null || true
    ok "Userpass auth enabled (dev/staging only)"
}

# ---------------------------------------------------------------------------
# Enable Audit Logging
# ---------------------------------------------------------------------------
enable_audit() {
    log "Enabling audit logging..."

    vault audit enable file file_path=/vault/logs/audit.log 2>/dev/null || true
    ok "File audit backend enabled at /vault/logs/audit.log"
}

# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------
verify() {
    log "Verifying Vault configuration..."

    echo ""
    echo "Secret Engines:"
    vault secrets list -format=table | grep igaming

    echo ""
    echo "Policies:"
    vault policy list | grep -E "igaming|payment|game|rng|kyc|fraud|bonus|compliance"

    echo ""
    echo "Auth Methods:"
    vault auth list -format=table

    echo ""
    ok "Vault initialization complete"
    log "Access Vault UI at: ${VAULT_ADDR}/ui"
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
main() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --vault-addr)   VAULT_ADDR="$2"; export VAULT_ADDR; shift 2;;
            --vault-token)  VAULT_TOKEN="$2"; export VAULT_TOKEN; shift 2;;
            --init-prod)    INIT_PROD="true"; shift;;
            --help|-h)
                echo "Usage: $0 [--vault-addr URL] [--vault-token TOKEN] [--init-prod]"
                exit 0;;
            *) fatal "Unknown argument: $1";;
        esac
    done

    preflight

    if [[ "$INIT_PROD" == "true" ]]; then
        init_production
    fi

    enable_secret_engines
    create_secret_paths
    configure_transit
    configure_pki
    apply_policies
    configure_auth
    enable_audit
    verify

    log "Vault is ready for iGaming platform operations"
}

main "$@"
