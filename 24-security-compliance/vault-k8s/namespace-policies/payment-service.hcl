# =============================================================================
# Vault Policy: Payment Service Namespace
# =============================================================================
# The payment service handles deposits, withdrawals, and card tokenization.
# This is the most sensitive namespace in the gambling platform.
#
# Access requirements:
# - Payment gateway API keys (Stripe, Adyen, Worldpay)
# - Card tokenization via Transit engine (PCI DSS compliance)
# - Database credentials (dynamic, short-lived)
# - TLS certificates for PCI DSS network segmentation
# - Encryption keys for PII at rest
#
# Security rationale:
# - Shortest TTLs (15min) to minimize credential exposure window
# - Transit engine for encryption-as-a-service (keys never leave Vault)
# - No access to game logic, RNG, or bonus secrets
# - Explicit deny on all administrative paths
# =============================================================================

# --- Payment Gateway Credentials ---
# Read-only access to payment provider API keys
path "secret/data/payment/stripe" {
  capabilities = ["read"]
}

path "secret/data/payment/adyen" {
  capabilities = ["read"]
}

path "secret/data/payment/worldpay" {
  capabilities = ["read"]
}

# General payment configuration
path "secret/data/payment/config" {
  capabilities = ["read"]
}

path "secret/metadata/payment/*" {
  capabilities = ["read", "list"]
}

# --- Transit Encryption (PCI DSS Card Tokenization) ---
# The payment service uses Vault Transit to tokenize card numbers
# This keeps card data encrypted with keys that never leave Vault
# Satisfies PCI DSS Requirement 3.4 (render PAN unreadable)

# Encrypt card data (tokenize)
path "transit/encrypt/payment" {
  capabilities = ["update"]
}

# Decrypt card data (de-tokenize, for processing only)
path "transit/decrypt/payment" {
  capabilities = ["update"]
}

# Rewrap encrypted data with latest key version (key rotation)
path "transit/rewrap/payment" {
  capabilities = ["update"]
}

# Read key configuration (NOT key material)
path "transit/keys/payment" {
  capabilities = ["read"]
}

# DENY key export - card encryption keys must NEVER leave Vault
path "transit/export/encryption-key/payment" {
  capabilities = ["deny"]
}

# Encrypt/decrypt PII associated with transactions
path "transit/encrypt/pii" {
  capabilities = ["update"]
}

path "transit/decrypt/pii" {
  capabilities = ["update"]
}

# --- Shared Encryption Keys ---
path "secret/data/shared/encryption-keys" {
  capabilities = ["read"]
}

# --- Dynamic Database Credentials ---
# Short-lived database credentials reduce risk of credential theft
# Payment database has read-write access (for transaction records)
path "database/creds/payment-readwrite" {
  capabilities = ["read"]
}

# Reporting queries use read-only credentials
path "database/creds/payment-readonly" {
  capabilities = ["read"]
}

# --- TLS Certificates ---
# mTLS certificates for PCI DSS network segmentation
# Payment service communicates only with approved services
path "pki/issue/payment" {
  capabilities = ["create", "update"]
}

path "pki/cert/ca" {
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

# Game logic secrets - payment service has no business accessing these
path "secret/data/game-server/*" {
  capabilities = ["deny"]
}

path "secret/data/shared/rng-config" {
  capabilities = ["deny"]
}

# Bonus engine - separation of concerns prevents bonus-payment fraud
path "secret/data/bonus-engine/*" {
  capabilities = ["deny"]
}

# Backoffice admin access
path "secret/data/backoffice/*" {
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

# Policy management
path "sys/policies/*" {
  capabilities = ["deny"]
}
