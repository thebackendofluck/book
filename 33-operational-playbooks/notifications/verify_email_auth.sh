#!/usr/bin/env bash
# Companion code for "The Backend of Luck" - Chapter 33, Operational Playbooks.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# verify_email_auth.sh — pre-deployment SPF/DKIM/DMARC validation for every
# configured sending domain. Companion script for Chapter 33c.
#
# Usage: ./verify_email_auth.sh domain1.com [domain2.com ...]
#        DKIM_SELECTORS="ses,sendgrid" ./verify_email_auth.sh example.com
#
# Exits non-zero if any domain is missing SPF, a DKIM selector record, or has
# a DMARC policy weaker than quarantine.
set -euo pipefail

SELECTORS="${DKIM_SELECTORS:-default,ses,sendgrid,s1,s2}"
fail=0

check_domain() {
  local domain="$1"
  echo "== $domain =="

  # SPF: exactly one v=spf1 TXT record must exist.
  local spf
  spf=$(dig +short TXT "$domain" | tr -d '"' | grep -c 'v=spf1' || true)
  if [[ "$spf" -lt 1 ]]; then
    echo "  SPF: MISSING"; fail=1
  elif [[ "$spf" -gt 1 ]]; then
    echo "  SPF: INVALID (multiple v=spf1 records)"; fail=1
  else
    echo "  SPF: ok"
  fi

  # DKIM: at least one configured selector must publish a key.
  local found_dkim=0 sel
  IFS=',' read -ra sels <<<"$SELECTORS"
  for sel in "${sels[@]}"; do
    if dig +short TXT "${sel}._domainkey.${domain}" | grep -q 'v=DKIM1'; then
      echo "  DKIM: ok (selector ${sel})"; found_dkim=1
    fi
  done
  [[ "$found_dkim" -eq 1 ]] || { echo "  DKIM: MISSING (checked: ${SELECTORS})"; fail=1; }

  # DMARC: policy must be quarantine or reject.
  local dmarc policy
  dmarc=$(dig +short TXT "_dmarc.${domain}" | tr -d '"' | grep 'v=DMARC1' || true)
  if [[ -z "$dmarc" ]]; then
    echo "  DMARC: MISSING"; fail=1
  else
    policy=$(echo "$dmarc" | grep -oE 'p=[a-z]+' | head -1 | cut -d= -f2)
    case "$policy" in
      quarantine | reject) echo "  DMARC: ok (p=${policy})" ;;
      *) echo "  DMARC: WEAK (p=${policy:-none}; need quarantine or reject)"; fail=1 ;;
    esac
  fi
}

[[ $# -ge 1 ]] || { echo "usage: $0 <domain> [domain ...]" >&2; exit 2; }
for d in "$@"; do check_domain "$d"; done

[[ "$fail" -eq 0 ]] || { echo "email-auth validation FAILED"; exit 1; }
echo "all domains passed email-auth validation"
