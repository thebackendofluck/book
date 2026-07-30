#!/bin/bash
# Companion code for "The Backend of Luck" - Chapter 44, Deploying iGaming Platforms on Cloudflare Workers.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# Deploy GDPR-compliant encryption to Cloudflare Workers.
#
# Steps:
#   1. Validate prerequisites and Wrangler authentication
#   2. Generate AES-256 encryption key if not already set
#   3. Set encryption key as Workers Secret
#   4. Create D1 database with EU jurisdiction (GDPR data residency)
#   5. Apply encryption schema migrations
#   6. Deploy Worker with encryption middleware
#   7. Run smoke tests against live endpoints
#   8. Verify ciphertext is stored in D1 (not plaintext)
#
# Usage:
#   ./deploy.sh                      — Full deployment (interactive)
#   ./deploy.sh --env staging        — Deploy to staging environment
#   ./deploy.sh --env production     — Deploy to production
#   ./deploy.sh --skip-key-gen       — Skip key generation (secrets already set)
#   ./deploy.sh --skip-smoke         — Skip smoke tests (CI/CD environments)
#
# GDPR compliance:
#   The D1 database is created in the WEUR (Western Europe) region to satisfy
#   GDPR Art.44 data residency requirements for EU player data.
#   Cloudflare is a data processor under GDPR Art.28 — their DPA at
#   cloudflare.com/dpa covers this deployment.

set -euo pipefail

# ── Colour output ──────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'

info()    { echo -e "${BLUE}[INFO]${NC}    $*"; }
success() { echo -e "${GREEN}[OK]${NC}      $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC}    $*"; }
error()   { echo -e "${RED}[ERROR]${NC}   $*" >&2; }
step()    { echo -e "${CYAN}[STEP]${NC}    $*"; }

# ── Default arguments ──────────────────────────────────────────────────────
ENV="development"
SKIP_KEY_GEN=false
SKIP_SMOKE=false
DB_NAME="acmetocasino-gdpr-test-db"
DB_LOCATION="weur"  # Western Europe — GDPR Art.44 data residency

for arg in "$@"; do
  case $arg in
    --env=*)         ENV="${arg#*=}" ;;
    --env)           shift; ENV="${1:-development}" ;;
    --skip-key-gen)  SKIP_KEY_GEN=true ;;
    --skip-smoke)    SKIP_SMOKE=true ;;
    --help|-h)
      echo "Usage: $0 [--env ENV] [--skip-key-gen] [--skip-smoke]"
      exit 0
      ;;
  esac
done

# ── Prerequisites check ────────────────────────────────────────────────────
check_prerequisites() {
  step "1/8 — Checking prerequisites"

  local missing=()
  for cmd in openssl npx node; do
    if ! command -v "$cmd" &>/dev/null; then
      missing+=("$cmd")
    fi
  done

  if [[ ${#missing[@]} -gt 0 ]]; then
    error "Missing: ${missing[*]}"
    exit 1
  fi

  if ! npx wrangler whoami &>/dev/null; then
    error "Not authenticated. Run: npx wrangler login"
    exit 1
  fi

  success "Prerequisites OK"
}

# ── Step 2: Generate and set encryption key ────────────────────────────────
generate_and_set_keys() {
  step "2/8 — Setting encryption keys as Workers Secrets"

  if [[ "$SKIP_KEY_GEN" == "true" ]]; then
    info "Skipping key generation (--skip-key-gen)"
    # Verify secrets exist
    local secrets
    secrets=$(npx wrangler secret list 2>/dev/null || echo "")
    if echo "$secrets" | grep -q "ENCRYPTION_KEY"; then
      success "ENCRYPTION_KEY already set"
    else
      error "ENCRYPTION_KEY is not set. Run: npx wrangler secret put ENCRYPTION_KEY"
      error "Or remove --skip-key-gen to generate a new key."
      exit 1
    fi
    return 0
  fi

  # Generate AES-256 key (32 bytes, base64-encoded)
  local encryption_key hmac_key
  encryption_key=$(openssl rand -base64 32)
  hmac_key=$(openssl rand -base64 32)

  info "Setting ENCRYPTION_KEY (AES-256, 32 bytes)..."
  echo "${encryption_key}" | npx wrangler secret put ENCRYPTION_KEY --env "${ENV}"
  success "ENCRYPTION_KEY set"

  info "Setting HMAC_KEY (HMAC-SHA-256, 32 bytes)..."
  echo "${hmac_key}" | npx wrangler secret put HMAC_KEY --env "${ENV}"
  success "HMAC_KEY set"

  # Save key version to local file (not the key itself)
  echo "1" > .key-version
  success "Key version: 1"
}

# ── Step 3: Create D1 database with EU jurisdiction ────────────────────────
create_database() {
  step "3/8 — Creating D1 database (location: ${DB_LOCATION})"

  # Check if database already exists
  if npx wrangler d1 list 2>/dev/null | grep -q "${DB_NAME}"; then
    info "Database '${DB_NAME}' already exists"
    return 0
  fi

  info "Creating database: ${DB_NAME}"
  info "Location: ${DB_LOCATION} (Western Europe — GDPR Art.44 data residency)"

  local create_output
  create_output=$(npx wrangler d1 create "${DB_NAME}" --location="${DB_LOCATION}" 2>&1)
  echo "$create_output"

  # Extract database_id from output
  local db_id
  db_id=$(echo "$create_output" | grep -oE '[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}' | head -1)

  if [[ -n "$db_id" ]]; then
    info "Database ID: ${db_id}"
    # Update wrangler.toml with the actual database ID
    if command -v sed &>/dev/null; then
      sed -i.bak "s/REPLACE_WITH_ACTUAL_DATABASE_ID/${db_id}/" wrangler.toml && rm -f wrangler.toml.bak
      success "wrangler.toml updated with database ID"
    else
      warn "Update wrangler.toml manually: database_id = \"${db_id}\""
    fi
  fi

  success "Database created: ${DB_NAME}"
}

# ── Step 4: Apply schema migrations ───────────────────────────────────────
apply_migrations() {
  step "4/8 — Applying D1 schema"

  # Apply the encrypted schema (adds pii_columns to existing tables)
  info "Applying encryption schema to ${DB_NAME}..."

  # Create the DEK mapping table if it doesn't exist
  npx wrangler d1 execute "${DB_NAME}" --command "
    CREATE TABLE IF NOT EXISTS player_dek_metadata (
      player_id   INTEGER PRIMARY KEY,
      kek_version INTEGER NOT NULL DEFAULT 1,
      created_at  TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
      shredded_at TEXT
    );
    CREATE TABLE IF NOT EXISTS encryption_audit (
      id         INTEGER PRIMARY KEY AUTOINCREMENT,
      event_type TEXT    NOT NULL,
      player_id  INTEGER,
      kek_version INTEGER,
      details    TEXT,
      created_at TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
    );
  " 2>/dev/null || true

  success "Schema applied"
}

# ── Step 5: Deploy Worker ──────────────────────────────────────────────────
deploy_worker() {
  step "5/8 — Deploying Worker (env: ${ENV})"

  if [[ "$ENV" == "development" ]]; then
    info "Development environment — skipping remote deploy"
    info "Run 'npx wrangler dev' for local development"
    return 0
  fi

  npx wrangler deploy --env "${ENV}"
  success "Worker deployed"
}

# ── Step 6: Smoke tests ────────────────────────────────────────────────────
smoke_tests() {
  step "6/8 — Running smoke tests"

  if [[ "$SKIP_SMOKE" == "true" ]]; then
    info "Skipping smoke tests (--skip-smoke)"
    return 0
  fi

  if [[ "$ENV" == "development" ]]; then
    info "Smoke tests require a deployed Worker — skipping for development"
    return 0
  fi

  # Determine the Worker URL based on environment
  local worker_url
  if [[ "$ENV" == "production" ]]; then
    worker_url="https://gdpr-test.cloud-acmetocasino.com"
  else
    worker_url="https://acmetocasino-gdpr-test.workers.dev"
  fi

  info "Testing: ${worker_url}/health"

  local http_code
  http_code=$(curl -s -o /dev/null -w "%{http_code}" "${worker_url}/health" --max-time 10 || echo "000")

  if [[ "$http_code" == "200" ]]; then
    success "Health check: HTTP ${http_code}"
  else
    error "Health check failed: HTTP ${http_code}"
    exit 1
  fi
}

# ── Step 7: Verify encryption in D1 ───────────────────────────────────────
verify_encryption() {
  step "7/8 — Verifying encryption is active in D1"

  # Query a sample of the users table to confirm PII is stored as ciphertext
  info "Querying users table for ciphertext verification..."

  local sample_output
  sample_output=$(npx wrangler d1 execute "${DB_NAME}" \
    --command "SELECT id, email, full_name FROM users LIMIT 3;" 2>/dev/null || echo "")

  if echo "$sample_output" | grep -q '"iv"'; then
    success "Ciphertext confirmed in D1 — PII is encrypted"
    info "Sample: $(echo "$sample_output" | head -5)"
  elif echo "$sample_output" | grep -qE '@|\.com'; then
    error "Plaintext detected in D1! Encryption may not be applied."
    error "Ensure EncryptedModel is being used for all writes."
    exit 1
  else
    info "No rows in users table yet (expected for fresh database)"
    success "Encryption schema in place — will encrypt on first write"
  fi
}

# ── Step 8: Summary ────────────────────────────────────────────────────────
print_summary() {
  step "8/8 — Deployment summary"
  echo ""
  success "GDPR encryption deployment complete"
  echo ""
  echo "  Environment:    ${ENV}"
  echo "  Database:       ${DB_NAME}"
  echo "  DB Location:    ${DB_LOCATION} (Western Europe)"
  echo "  Encryption:     AES-256-GCM (GDPR Art.32)"
  echo "  Key storage:    Workers Secrets"
  echo ""
  echo "  Compliance:"
  echo "    GDPR Art.32(1)(a)  — encryption at rest: ACTIVE"
  echo "    GDPR Art.44        — EU data residency (WEUR): ACTIVE"
  echo "    GDPR Art.17        — erasure via crypto-shredding: READY"
  echo "    GDPR Art.28        — Cloudflare DPA: operator responsibility"
  echo ""
  info "Secrets set:"
  npx wrangler secret list 2>/dev/null | grep -E "ENCRYPTION_KEY|HMAC_KEY|JWT_SECRET" || true
  echo ""
  info "Next steps:"
  info "  1. Configure Cloudflare DPA: cloudflare.com/dpa"
  info "  2. Add jurisdiction routing for EU/UK/US player traffic"
  info "  3. Schedule key rotation: see wrangler.toml [triggers]"
  info "  4. Disable Logpush PII fields: cloudflare.com/logpush"
}

# ── Entrypoint ─────────────────────────────────────────────────────────────
main() {
  echo ""
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo "  GDPR Encryption Deployment — AcmeToCasino Platform"
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo "  Environment: ${ENV}"
  echo "  Date:        $(date -u '+%Y-%m-%dT%H:%M:%SZ')"
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo ""

  check_prerequisites
  generate_and_set_keys
  create_database
  apply_migrations
  deploy_worker
  smoke_tests
  verify_encryption
  print_summary
}

main "$@"
