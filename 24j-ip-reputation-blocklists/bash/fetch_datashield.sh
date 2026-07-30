#!/bin/bash
# Companion code for "The Backend of Luck" - Chapter 24j, IP Reputation and Blocklist Integration for iGaming Platform.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# Fetch Data-Shield blocklists directly from GitHub raw content CDN.
# Used for manual downloads or bootstrapping the pipeline.
#
# Records a sha256 for each file so a later run can tell whether upstream
# actually changed. There is no publisher-signed checksum to verify against:
# Data-Shield ships the list only, so this proves the download is intact and
# unchanged, not that it is authentic. Authenticity rests on HTTPS to the
# GitHub CDN.

set -euo pipefail

DOWNLOAD_DIR="${IPREP_DOWNLOAD_DIR:-/var/lib/iprep/downloads}"

# Primary recommended list
BLOCKLIST_URL="https://raw.githubusercontent.com/duggytuxy/Data-Shield_IPv4_Blocklist/main/prod_data-shield_ipv4_blocklist.txt"

# Critical list (for payment flow protection zones)
AGGRESSIVE_URL="https://raw.githubusercontent.com/duggytuxy/Data-Shield_IPv4_Blocklist/main/prod_critical_data-shield_ipv4_blocklist.txt"

mkdir -p "$DOWNLOAD_DIR"

fetch() {
    local url="$1" dest="$2"
    # Fetch with proper user-agent and timeout
    curl -fsS \
         -A "Mozilla/5.0 (iGaming-Security-Feed/1.0)" \
         --connect-timeout 15 \
         --max-time 120 \
         --retry 3 \
         --retry-delay 5 \
         -o "$dest" \
         "$url"

    if command -v sha256sum >/dev/null 2>&1; then
        sha256sum "$dest" > "${dest}.sha256"
    else
        # BSD/macOS
        shasum -a 256 "$dest" > "${dest}.sha256"
    fi

    printf '%s: %s lines, %s\n' \
        "$(basename "$dest")" \
        "$(grep -cvE '^\s*($|#)' "$dest" || true)" \
        "$(cut -d' ' -f1 < "${dest}.sha256")"
}

fetch "$BLOCKLIST_URL"  "$DOWNLOAD_DIR/datashield-recommended.txt"
fetch "$AGGRESSIVE_URL" "$DOWNLOAD_DIR/datashield-critical.txt"
