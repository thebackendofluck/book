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

# dns-records-audit.sh — Compare current DNS records against a baseline to detect tampering
#
# Purpose:
#   Operates in two modes:
#   1. Generate a baseline snapshot of DNS records for a domain
#   2. Check current DNS records against the baseline and alert on drift
#
#   Useful after a credential leak to verify that an attacker has not
#   modified DNS records (A, AAAA, CNAME, MX, NS, TXT, SOA) to redirect
#   traffic to malicious infrastructure.
#
# Prerequisites:
#   - dig installed (usually part of bind-tools or dnsutils)
#   - SLACK_WEBHOOK_URL environment variable (optional, for alerts)
#
# Usage:
#   ./dns-records-audit.sh --generate-baseline --domain yourbrand.com --output /etc/dns-baseline.txt
#   ./dns-records-audit.sh --check --domain yourbrand.com --baseline /etc/dns-baseline.txt
#   ./dns-records-audit.sh --check --domain yourbrand.com --baseline /etc/dns-baseline.txt --nameserver 8.8.8.8
#
# Examples:
#   # Generate baseline for your primary domain
#   ./dns-records-audit.sh --generate-baseline --domain casino.example.com --output ./dns-baseline-casino.txt
#
#   # Nightly cron check against baseline
#   ./dns-records-audit.sh --check --domain casino.example.com --baseline ./dns-baseline-casino.txt

set -euo pipefail

MODE=""
DOMAIN=""
BASELINE_FILE=""
OUTPUT_FILE=""
NAMESERVER=""
SLACK_WEBHOOK_URL="${SLACK_WEBHOOK_URL:-}"
RECORD_TYPES="A AAAA CNAME MX NS TXT SOA"

usage() {
  cat <<EOF
Usage:
  $0 --generate-baseline --domain <domain> --output <file> [--nameserver <ns>]
  $0 --check --domain <domain> --baseline <file> [--nameserver <ns>]

Options:
  --generate-baseline   Create a new baseline snapshot
  --check               Compare current records against baseline
  --domain <domain>     Domain to query
  --output <file>       Output file for baseline (generate mode)
  --baseline <file>     Baseline file to compare against (check mode)
  --nameserver <ns>     Specific nameserver to query (optional)
EOF
  exit 1
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --generate-baseline) MODE="generate"; shift ;;
    --check) MODE="check"; shift ;;
    --domain) DOMAIN="$2"; shift 2 ;;
    --baseline) BASELINE_FILE="$2"; shift 2 ;;
    --output) OUTPUT_FILE="$2"; shift 2 ;;
    --nameserver) NAMESERVER="@$2"; shift 2 ;;
    -h|--help) usage ;;
    *) echo "Unknown option: $1" >&2; usage ;;
  esac
done

# Validate required arguments
[[ -z "$MODE" ]] && { echo "ERROR: Specify --generate-baseline or --check" >&2; usage; }
[[ -z "$DOMAIN" ]] && { echo "ERROR: --domain is required" >&2; usage; }
command -v dig >/dev/null 2>&1 || { echo "ERROR: dig is required (install bind-tools or dnsutils)" >&2; exit 1; }

fetch_records() {
  local domain="$1"
  for rtype in $RECORD_TYPES; do
    # +noall +answer: only show answer section
    # +nocmd: suppress dig version header
    dig +noall +answer +nocmd ${NAMESERVER:+"$NAMESERVER"} "$domain" "$rtype" 2>/dev/null | \
      awk '{print $1, $4, $5}' | sort
  done
}

if [[ "$MODE" == "generate" ]]; then
  [[ -z "$OUTPUT_FILE" ]] && { echo "ERROR: --output required for --generate-baseline" >&2; exit 1; }

  echo "# DNS baseline for $DOMAIN -- generated $(date -u +%FT%TZ)" > "$OUTPUT_FILE"
  echo "# Regenerate with: $0 --generate-baseline --domain $DOMAIN --output $OUTPUT_FILE" >> "$OUTPUT_FILE"
  fetch_records "$DOMAIN" >> "$OUTPUT_FILE"

  LINES=$(wc -l < "$OUTPUT_FILE" | tr -d ' ')
  echo "[$(date -u +%FT%TZ)] Baseline written to $OUTPUT_FILE ($LINES lines)"
  exit 0
fi

if [[ "$MODE" == "check" ]]; then
  [[ -z "$BASELINE_FILE" ]] && { echo "ERROR: --baseline required for --check" >&2; exit 1; }
  [[ ! -f "$BASELINE_FILE" ]] && { echo "ERROR: Baseline file not found: $BASELINE_FILE" >&2; exit 1; }

  CURRENT=$(mktemp)
  BASELINE_CLEAN=$(mktemp)
  CURRENT_SORTED=$(mktemp)
  trap 'rm -f "$CURRENT" "$BASELINE_CLEAN" "$CURRENT_SORTED"' EXIT

  fetch_records "$DOMAIN" > "$CURRENT"

  # Strip comments from baseline for comparison
  grep -v '^#' "$BASELINE_FILE" | sort > "$BASELINE_CLEAN"
  sort "$CURRENT" > "$CURRENT_SORTED"

  DIFF_OUTPUT=$(diff "$BASELINE_CLEAN" "$CURRENT_SORTED" 2>/dev/null) || true

  if [[ -z "$DIFF_OUTPUT" ]]; then
    echo "[$(date -u +%FT%TZ)] DNS records for $DOMAIN match baseline. No tampering detected."
    exit 0
  else
    echo "[$(date -u +%FT%TZ)] DNS DRIFT DETECTED for $DOMAIN:"
    echo "$DIFF_OUTPUT"
    echo ""
    echo "Lines prefixed with '<' are in the baseline but missing now."
    echo "Lines prefixed with '>' are new records not in the baseline."

    if [[ -n "$SLACK_WEBHOOK_URL" ]]; then
      # Truncate diff for Slack (max ~500 chars to avoid payload limits)
      SHORT_DIFF=$(echo "$DIFF_OUTPUT" | head -20)
      # shellcheck disable=SC2016
      PAYLOAD=$(printf '{"text":"DNS DRIFT DETECTED for %s:\n```\n%s\n```"}' "$DOMAIN" "$SHORT_DIFF")
      curl -s -X POST -H 'Content-Type: application/json' -d "$PAYLOAD" "$SLACK_WEBHOOK_URL" >/dev/null
    fi

    exit 1
  fi
fi

usage
