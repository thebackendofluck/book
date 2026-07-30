# =============================================================================
# Vault Policy: Backoffice Namespace
# =============================================================================
# The backoffice serves internal operators (customer support, compliance,
# risk management, marketing). It has broader read access than player-facing
# services but still follows least-privilege principles.
#
# Access requirements:
# - LDAP/SSO configuration for operator authentication
# - Read-only access to reporting database credentials
# - Backoffice application secrets
# - TLS certificates for internal services
#
# Security rationale:
# - Backoffice can READ player data but cannot ACCESS payment credentials
# - No access to game RNG or session keys (prevents insider manipulation)
# - Longer TTLs (1h) appropriate for internal tooling sessions
# - Audit logging captures all backoffice secret access for compliance
# =============================================================================

# --- Backoffice Application Secrets ---
path "secret/data/backoffice/*" {
  capabilities = ["read", "list"]
}

path "secret/metadata/backoffice/*" {
  capabilities = ["read", "list"]
}

# --- LDAP / SSO Configuration ---
# Backoffice authenticates operators via LDAP/Active Directory
path "secret/data/shared/ldap-config" {
  capabilities = ["read"]
}

path "secret/data/backoffice/sso-config" {
  capabilities = ["read"]
}

# --- Reporting Database ---
# Read-only database credentials for generating compliance reports,
# player activity reports, and financial summaries
path "database/creds/reporting-readonly" {
  capabilities = ["read"]
}

path "database/creds/backoffice-readonly" {
  capabilities = ["read"]
}

# --- Player Data Access (Read-Only) ---
# Customer support needs to look up player information
# This is read-only and all access is audit-logged
path "secret/data/player-account/config" {
  capabilities = ["read"]
}

# --- Encryption for Export ---
# Backoffice may need to encrypt data for regulatory reports
path "transit/encrypt/reporting" {
  capabilities = ["update"]
}

# --- TLS Certificates ---
path "pki/issue/backoffice" {
  capabilities = ["create", "update"]
}

path "pki/cert/ca" {
  capabilities = ["read"]
}

# --- Monitoring Credentials ---
# Backoffice dashboards need read access to monitoring systems
path "secret/data/monitoring/grafana" {
  capabilities = ["read"]
}

# --- Token Self-Management ---
path "auth/token/lookup-self" {
  capabilities = ["read"]
}

path "auth/token/renew-self" {
  capabilities = ["update"]
}

# --- Explicit Denials ---

# Payment gateway credentials - backoffice must never access these directly
# Financial operations go through the payment service API
path "secret/data/payment/stripe" {
  capabilities = ["deny"]
}

path "secret/data/payment/adyen" {
  capabilities = ["deny"]
}

path "secret/data/payment/worldpay" {
  capabilities = ["deny"]
}

# Card tokenization keys - PCI DSS scope isolation
path "transit/*/payment" {
  capabilities = ["deny"]
}

# Game RNG configuration - prevents insider game manipulation
path "secret/data/shared/rng-config" {
  capabilities = ["deny"]
}

path "secret/data/game-server/*" {
  capabilities = ["deny"]
}

# KYC vendor API keys - KYC service handles vendor integration
path "secret/data/kyc/vendor-api-keys" {
  capabilities = ["deny"]
}

# System administration
path "sys/*" {
  capabilities = ["deny"]
}

# Auth method management
path "auth/*/config" {
  capabilities = ["deny"]
}
