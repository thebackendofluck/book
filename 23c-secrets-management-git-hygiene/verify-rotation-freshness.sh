#!/usr/bin/env bash
# Companion code for "The Backend of Luck" - Chapter 23c, Secrets Management and Git Hygiene for iGaming Engineering.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# verify-rotation-freshness.sh — Compare secret last-rotated date against policy
#
# Purpose:
#   Queries OpenBao (or Vault) KV v2 metadata for all secrets under a given
#   path, checks when each was last updated, and flags any that exceed the
#   configured maximum age (default: 90 days). Optionally sends a Slack alert.
#
# Prerequisites:
#   - VAULT_ADDR and VAULT_TOKEN environment variables set
#   - jq installed (brew install jq)
#   - vault or bao CLI installed
#   - SLACK_WEBHOOK_URL environment variable (optional, for alerts)
#
# Usage:
#   ./verify-rotation-freshness.sh [--max-age-days 90] [--path secret/]
#
# Examples:
#   ./verify-rotation-freshness.sh --max-age-days 30 --path secret/casino/
#   SLACK_WEBHOOK_URL=https://hooks.slack.com/... ./verify-rotation-freshness.sh

set -euo pipefail

MAX_AGE_DAYS=90
SECRET_PATH="secret/"
SLACK_WEBHOOK_URL="${SLACK_WEBHOOK_URL:-}"
EXIT_CODE=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --max-age-days) MAX_AGE_DAYS="$2"; shift 2 ;;
    --path) SECRET_PATH="$2"; shift 2 ;;
    *) echo "Usage: $0 [--max-age-days N] [--path secret/path/]" >&2; exit 1 ;;
  esac
done

# Validate dependencies
command -v jq >/dev/null 2>&1 || { echo "ERROR: jq is required (brew install jq)" >&2; exit 1; }

CLI="vault"
if command -v bao >/dev/null 2>&1; then
  CLI="bao"
elif ! command -v vault >/dev/null 2>&1; then
  echo "ERROR: vault or bao CLI is required" >&2
  exit 1
fi

if [[ -z "${VAULT_ADDR:-}" || -z "${VAULT_TOKEN:-}" ]]; then
  echo "ERROR: VAULT_ADDR and VAULT_TOKEN must be set" >&2
  exit 1
fi

echo "[$(date -u +%FT%TZ)] Checking rotation freshness (max age: ${MAX_AGE_DAYS} days, path: ${SECRET_PATH})"

KEYS=$($CLI kv list -format=json "${SECRET_PATH}" 2>/dev/null | jq -r '.[]' 2>/dev/null) || {
  echo "ERROR: Failed to list secrets at ${SECRET_PATH}" >&2
  exit 1
}

NOW_EPOCH=$(date +%s)
STALE_SECRETS=""
TOTAL=0
STALE=0

while IFS= read -r KEY; do
  [[ -z "$KEY" ]] && continue
  TOTAL=$((TOTAL + 1))

  METADATA=$($CLI kv metadata get -format=json "${SECRET_PATH}${KEY}" 2>/dev/null) || {
    echo "  WARN: Cannot read metadata for ${KEY}, skipping"
    continue
  }

  UPDATED=$(echo "$METADATA" | jq -r '.data.updated_time // .data.created_time' 2>/dev/null)

  if [[ -z "$UPDATED" || "$UPDATED" == "null" ]]; then
    echo "  WARN: No timestamp for ${KEY}, treating as stale"
    STALE=$((STALE + 1))
    STALE_SECRETS="${STALE_SECRETS}\n  - ${KEY}: no timestamp"
    continue
  fi

  # Parse ISO 8601 timestamp to epoch (macOS and Linux compatible)
  UPDATED_EPOCH=$(date -j -f "%Y-%m-%dT%H:%M:%S" "${UPDATED%%.*}" +%s 2>/dev/null) || \
    UPDATED_EPOCH=$(date -d "${UPDATED}" +%s 2>/dev/null) || {
      echo "  WARN: Cannot parse timestamp for ${KEY}: ${UPDATED}"
      continue
    }

  AGE_DAYS=$(( (NOW_EPOCH - UPDATED_EPOCH) / 86400 ))

  if [[ $AGE_DAYS -gt $MAX_AGE_DAYS ]]; then
    STALE=$((STALE + 1))
    STALE_SECRETS="${STALE_SECRETS}\n  - ${KEY}: ${AGE_DAYS} days old"
    EXIT_CODE=1
  fi
done <<< "$KEYS"

echo "[$(date -u +%FT%TZ)] Checked $TOTAL secrets, $STALE exceed ${MAX_AGE_DAYS}-day policy"

if [[ $STALE -gt 0 ]]; then
  printf "Stale secrets:%b\n" "$STALE_SECRETS"
  if [[ -n "$SLACK_WEBHOOK_URL" ]]; then
    PAYLOAD=$(printf '{"text":"Rotation freshness check: %d/%d secrets exceed %d-day policy:%b"}' \
      "$STALE" "$TOTAL" "$MAX_AGE_DAYS" "$STALE_SECRETS")
    curl -s -X POST -H 'Content-Type: application/json' -d "$PAYLOAD" "$SLACK_WEBHOOK_URL" >/dev/null
  fi
fi

exit $EXIT_CODE
