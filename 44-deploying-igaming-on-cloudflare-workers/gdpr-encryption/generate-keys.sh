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

# Generate and rotate AES-256 encryption keys for Cloudflare Workers.
#
# Usage:
#   ./generate-keys.sh              — Generate new keys and set as Workers Secrets
#   ./generate-keys.sh --rotate     — Rotate existing keys (generates new, sets new version)
#   ./generate-keys.sh --print-only — Print generated keys to stdout (do NOT set as secrets)
#
# Prerequisites:
#   - wrangler CLI authenticated (npx wrangler whoami)
#   - openssl installed (available on macOS and Linux by default)
#   - jq installed (brew install jq / apt install jq)
#
# Security model:
#   Keys are generated locally using openssl rand (CSPRNG).
#   They are piped directly to wrangler secret put without being written to disk.
#   The shell history is NOT cleared automatically — consider running in a
#   subshell or using `unset HISTFILE` before running this script.
#
# GDPR Art.32(1)(a) compliance:
#   AES-256-GCM is an "appropriate technical measure" for encryption of
#   personal data under GDPR Art.32. The 256-bit key length is recommended
#   by ENISA (EU Agency for Cybersecurity) and NIST SP 800-57.

set -euo pipefail

# ── Colour output helpers ──────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'  # No Colour

info()    { echo -e "${BLUE}[INFO]${NC}  $*"; }
success() { echo -e "${GREEN}[OK]${NC}    $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error()   { echo -e "${RED}[ERROR]${NC} $*" >&2; }

# ── Argument parsing ───────────────────────────────────────────────────────
ROTATE=false
PRINT_ONLY=false

for arg in "$@"; do
  case $arg in
    --rotate)     ROTATE=true ;;
    --print-only) PRINT_ONLY=true ;;
    --help|-h)
      echo "Usage: $0 [--rotate] [--print-only]"
      echo ""
      echo "  (no flags)   Generate new keys and set as Workers Secrets"
      echo "  --rotate     Rotate existing keys (bump version, set new secrets)"
      echo "  --print-only Print generated keys to stdout only (do NOT set secrets)"
      exit 0
      ;;
    *)
      error "Unknown argument: $arg"
      exit 1
      ;;
  esac
done

# ── Prerequisite checks ────────────────────────────────────────────────────
check_prerequisites() {
  local missing=()

  if ! command -v openssl &>/dev/null; then
    missing+=("openssl")
  fi

  if ! command -v npx &>/dev/null; then
    missing+=("npx (Node.js)")
  fi

  if [[ ${#missing[@]} -gt 0 ]]; then
    error "Missing prerequisites: ${missing[*]}"
    error "Install missing tools and retry."
    exit 1
  fi

  if [[ "$PRINT_ONLY" == "false" ]]; then
    if ! npx wrangler whoami &>/dev/null; then
      error "Not authenticated with Wrangler. Run: npx wrangler login"
      exit 1
    fi
  fi
}

# ── Key generation ─────────────────────────────────────────────────────────
generate_aes256_key() {
  # 32 bytes = 256 bits, base64-encoded
  openssl rand -base64 32
}

generate_hmac_key() {
  # 32 bytes = 256 bits, base64-encoded
  openssl rand -base64 32
}

# ── Key version management ─────────────────────────────────────────────────
get_current_version() {
  # Read current key version from a local version file (not a secret)
  local version_file=".key-version"
  if [[ -f "$version_file" ]]; then
    cat "$version_file"
  else
    echo "1"
  fi
}

bump_version() {
  local version_file=".key-version"
  local current
  current=$(get_current_version)
  local new_version=$((current + 1))
  echo "$new_version" > "$version_file"
  echo "$new_version"
}

# ── Main ───────────────────────────────────────────────────────────────────
main() {
  check_prerequisites

  info "Generating GDPR-compliant encryption keys"
  info "Algorithm: AES-256-GCM (ENISA/NIST SP 800-57 recommended)"
  info "Key length: 256-bit (32 bytes)"
  echo ""

  # Generate keys
  local encryption_key hmac_key
  encryption_key=$(generate_aes256_key)
  hmac_key=$(generate_hmac_key)

  if [[ "$ROTATE" == "true" ]]; then
    local new_version
    new_version=$(bump_version)
    info "Key rotation — new version: ${new_version}"
    info "Previous version: $((new_version - 1))"
    echo ""
    warn "IMPORTANT: After setting new secrets, run the DEK rotation job:"
    warn "  npx wrangler dev --trigger cron"
    warn "This re-wraps all player DEKs under the new KEK."
    echo ""
  fi

  if [[ "$PRINT_ONLY" == "true" ]]; then
    echo "Generated keys (PRINT ONLY — not set as Wrangler Secrets):"
    echo ""
    echo "ENCRYPTION_KEY: ${encryption_key}"
    echo "HMAC_KEY:       ${hmac_key}"
    echo ""
    warn "These keys are shown in plaintext. Store them securely."
    warn "Do NOT commit them to source control."
    warn "Use wrangler secret put to set them in production."
    return 0
  fi

  # Set ENCRYPTION_KEY as Workers Secret
  info "Setting ENCRYPTION_KEY as Workers Secret..."
  if echo "${encryption_key}" | npx wrangler secret put ENCRYPTION_KEY; then
    success "ENCRYPTION_KEY set successfully"
  else
    error "Failed to set ENCRYPTION_KEY"
    exit 1
  fi

  # Set HMAC_KEY as Workers Secret
  info "Setting HMAC_KEY as Workers Secret..."
  if echo "${hmac_key}" | npx wrangler secret put HMAC_KEY; then
    success "HMAC_KEY set successfully"
  else
    error "Failed to set HMAC_KEY"
    exit 1
  fi

  echo ""
  success "All secrets set successfully."
  echo ""
  info "Next steps:"
  info "  1. Verify secrets are set:  npx wrangler secret list"
  info "  2. Deploy the Worker:       npx wrangler deploy"

  if [[ "$ROTATE" == "true" ]]; then
    info "  3. Run DEK rotation job to re-wrap player keys under the new KEK"
    echo ""
    warn "ROTATION CHECKLIST:"
    warn "  [ ] Secrets set (ENCRYPTION_KEY, HMAC_KEY)"
    warn "  [ ] Worker deployed with new secrets"
    warn "  [ ] DEK rotation job triggered and completed"
    warn "  [ ] Old key version removed from rotation config"
    warn "  [ ] Rotation event logged in compliance_events"
  fi

  echo ""
  info "GDPR compliance note:"
  info "  - Keys are 256-bit AES (GDPR Art.32 appropriate technical measure)"
  info "  - Keys are stored as Workers Secrets (encrypted at rest by Cloudflare)"
  info "  - Keys are never written to disk or source control"
  info "  - Key rotation: wrangler secret put ENCRYPTION_KEY --env production"
}

main "$@"
