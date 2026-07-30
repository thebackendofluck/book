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

# scan-all-repos.sh — Nightly Gitleaks scan of all repos in a GitHub org
#
# Purpose:
#   Clones every repo in a GitHub organization (shallow, depth=50),
#   runs Gitleaks against each, and sends a Slack alert if any repo
#   contains secret findings. Reports are saved to /var/log/gitleaks-reports/.
#
# Prerequisites:
#   - gitleaks installed (brew install gitleaks)
#   - gh CLI authenticated (gh auth login)
#   - SLACK_WEBHOOK_URL environment variable set (optional, for alerts)
#
# Usage:
#   ./scan-all-repos.sh <github-org> [--clone-dir /tmp/scan]
#
# Examples:
#   ./scan-all-repos.sh myorg
#   SLACK_WEBHOOK_URL=https://hooks.slack.com/... ./scan-all-repos.sh myorg --clone-dir /tmp/scans

set -euo pipefail

ORG="${1:?Usage: $0 <github-org> [--clone-dir /path]}"
CLONE_DIR="/tmp/gitleaks-scan-$$"
REPORT_DIR="/var/log/gitleaks-reports"
SLACK_WEBHOOK_URL="${SLACK_WEBHOOK_URL:-}"
EXIT_CODE=0

shift || true
while [[ $# -gt 0 ]]; do
  case "$1" in
    --clone-dir) CLONE_DIR="$2"; shift 2 ;;
    *) echo "Unknown option: $1" >&2; exit 1 ;;
  esac
done

# Validate dependencies
command -v gitleaks >/dev/null 2>&1 || { echo "ERROR: gitleaks is required (brew install gitleaks)" >&2; exit 1; }
command -v gh >/dev/null 2>&1 || { echo "ERROR: gh CLI is required (brew install gh)" >&2; exit 1; }

mkdir -p "$CLONE_DIR" "$REPORT_DIR"
trap 'rm -rf "$CLONE_DIR"' EXIT

echo "[$(date -u +%FT%TZ)] Starting scan for org: $ORG"

REPOS=$(gh repo list "$ORG" --limit 500 --json nameWithOwner -q '.[].nameWithOwner')

if [[ -z "$REPOS" ]]; then
  echo "ERROR: No repos found for org $ORG (check gh auth)" >&2
  exit 1
fi

TOTAL=0
FAILED=0
FINDINGS=""

while IFS= read -r REPO; do
  TOTAL=$((TOTAL + 1))
  REPO_NAME=$(basename "$REPO")
  REPO_DIR="$CLONE_DIR/$REPO_NAME"
  REPORT="$REPORT_DIR/${REPO_NAME}-$(date +%F).json"

  echo "  Scanning $REPO ..."
  if ! gh repo clone "$REPO" "$REPO_DIR" -- --depth=50 --quiet 2>/dev/null; then
    echo "  WARN: Failed to clone $REPO, skipping" >&2
    continue
  fi

  if ! gitleaks detect --source "$REPO_DIR" --report-path "$REPORT" --redact --no-banner 2>/dev/null; then
    FAILED=$((FAILED + 1))
    FINDINGS="${FINDINGS}\n- ${REPO}: secrets found (see ${REPORT})"
    EXIT_CODE=1
  fi

  rm -rf "$REPO_DIR"
done <<< "$REPOS"

echo "[$(date -u +%FT%TZ)] Scan complete: $TOTAL repos scanned, $FAILED with findings"

if [[ $FAILED -gt 0 && -n "$SLACK_WEBHOOK_URL" ]]; then
  PAYLOAD=$(printf '{"text":"Gitleaks nightly scan: %d/%d repos have secret findings:\\n%s"}' \
    "$FAILED" "$TOTAL" "$FINDINGS")
  curl -s -X POST -H 'Content-Type: application/json' -d "$PAYLOAD" "$SLACK_WEBHOOK_URL" >/dev/null
fi

exit $EXIT_CODE
