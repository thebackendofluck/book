# =============================================================================
# Vault Policy: Game Server Namespace
# =============================================================================
# Scoped access for game server pods. Game servers need:
# - RNG configuration secrets (certified RNG provider keys)
# - Session management keys
# - Game configuration (house edge, bet limits)
# - TLS certificates for mTLS between game servers
#
# Game servers MUST NOT have access to:
# - Payment/wallet secrets
# - KYC/PII data
# - Backoffice admin credentials
# - Vault system endpoints
# =============================================================================

# --- Game Server Application Secrets ---
# Read-only access to game server configuration
path "secret/data/game-server/*" {
  capabilities = ["read", "list"]
}

path "secret/metadata/game-server/*" {
  capabilities = ["read", "list"]
}

# --- RNG Configuration ---
# Certified RNG provider credentials and configuration
# Critical: These control game fairness and must be tightly controlled
path "secret/data/shared/rng-config" {
  capabilities = ["read"]
}

# --- Session Management ---
# JWT signing keys for player game sessions
path "secret/data/game-server/session-keys" {
  capabilities = ["read"]
}

# --- TLS Certificates ---
# Issue short-lived certificates for mTLS between game servers
# Max TTL 24h forces regular rotation
path "pki/issue/game-server" {
  capabilities = ["create", "update"]
}

path "pki/cert/ca" {
  capabilities = ["read"]
}

# --- Transit Encryption ---
# Encrypt/decrypt game state data at rest
# Game servers can encrypt but NOT export the key material
path "transit/encrypt/game-state" {
  capabilities = ["update"]
}

path "transit/decrypt/game-state" {
  capabilities = ["update"]
}

# Deny key export - game state encryption key must stay in Vault
path "transit/export/*" {
  capabilities = ["deny"]
}

# --- Database Credentials ---
# Dynamic database credentials for game history storage
# Read-only access to game history database
path "database/creds/game-server-readonly" {
  capabilities = ["read"]
}

# --- Token Self-Management ---
# Required for automatic token renewal by Vault Agent
path "auth/token/lookup-self" {
  capabilities = ["read"]
}

path "auth/token/renew-self" {
  capabilities = ["update"]
}

# --- Explicit Denials ---
# Defense in depth: explicitly deny access to sensitive paths
# even if a broader policy accidentally grants access

# Never allow game servers to access payment secrets
path "secret/data/payment/*" {
  capabilities = ["deny"]
}

# Never allow access to KYC/PII data
path "secret/data/kyc/*" {
  capabilities = ["deny"]
}

# Never allow access to player account secrets
path "secret/data/player-account/*" {
  capabilities = ["deny"]
}

# Never allow access to backoffice admin credentials
path "secret/data/backoffice/*" {
  capabilities = ["deny"]
}

# Never allow system administration
path "sys/*" {
  capabilities = ["deny"]
}

# Never allow auth method management
path "auth/*/config" {
  capabilities = ["deny"]
}
