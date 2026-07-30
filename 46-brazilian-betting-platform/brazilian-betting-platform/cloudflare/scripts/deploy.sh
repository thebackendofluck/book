#!/usr/bin/env bash
# Companion code for "The Backend of Luck" - Chapter 46, Building a Brazilian Betting Platform.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

#
# Deploy the bet-brazil edge workers in a dependency-safe order.
#
# Secrets must already be provisioned (see DEPLOY.md and .dev.vars.example).
# This script refuses to deploy a worker whose required secrets are missing,
# so a half-configured worker never reaches production.
#
# Usage: bash scripts/deploy.sh
#
set -euo pipefail

cd "$(dirname "$0")/.."

run() {
  echo "+ $*"
  "$@"
}

# check_secrets <config.toml> [required secret names...]
# Aborts if any named secret is absent from the worker's remote secret store.
check_secrets() {
  local config="$1"
  shift
  local present
  present="$(npx wrangler secret list --config "$config" 2>/dev/null | tr -d '[]", ' || true)"
  local missing=()
  local name
  for name in "$@"; do
    case "$present" in
      *"$name"*) ;;
      *) missing+=("$name") ;;
    esac
  done
  if [ "${#missing[@]}" -gt 0 ]; then
    echo "ERROR: $config is missing secrets: ${missing[*]}" >&2
    echo "Provision them first, e.g.: wrangler secret put ${missing[0]} --config $config" >&2
    exit 1
  fi
}

# Order matters. The gateway must start SIGNING internal calls before
# pix-webhook starts REQUIRING them, so the deposit path never sees a broken
# window where one side is new and the other still old.
check_secrets wrangler.toml JWT_SECRET GATEWAY_INTERNAL_HMAC_SECRET
run npx wrangler deploy --config wrangler.toml

check_secrets wrangler.pix-webhook.toml PIX_HMAC_SECRET PIX_PSP_API_KEY GATEWAY_INTERNAL_HMAC_SECRET
run npx wrangler deploy --config wrangler.pix-webhook.toml

check_secrets wrangler.odds-feed.toml ODDS_PUBLISHER_HMAC_SECRET
run npx wrangler deploy --config wrangler.odds-feed.toml

check_secrets wrangler.sigap-reporter.toml SIGAP_BEARER_TOKEN SIGAP_COMPLIANCE_HMAC_SECRET
run npx wrangler deploy --config wrangler.sigap-reporter.toml

check_secrets wrangler.wallet.toml PIX_HMAC_SECRET PIX_PSP_API_KEY WALLET_INTERNAL_HMAC_SECRET
run npx wrangler deploy --config wrangler.wallet.toml

check_secrets wrangler.session-manager.toml JWT_SECRET
run npx wrangler deploy --config wrangler.session-manager.toml

echo "All edge workers deployed. Validate with: bash scripts/smoke-test.sh"
