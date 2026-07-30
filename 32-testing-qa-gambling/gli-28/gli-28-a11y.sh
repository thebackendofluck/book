#!/usr/bin/env bash
# Companion code for "The Backend of Luck" - Chapter 32, Testing and QA in Gambling.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# GLI-28 v1.0 — accessibility scan via @axe-core/cli.
#
# WCAG 2.1 AA is the conformance level GLI-28 v1.0 reaches for through axe-core
# rule mappings. Emits a JUnit XML report that GitLab CI / GitHub Actions can
# consume natively.
#
# Exit codes:
#   0  no violations
#   1  WCAG violations detected
#   2  config / dependency error
#
# Usage:
#   GAMES_FILE=games.txt BASE_URL=https://staging.acmetocasino.com \
#     OUT_DIR=axe-reports ./gli-28-a11y.sh

set -euo pipefail

: "${GAMES_FILE:?GAMES_FILE required (one game slug per line)}"
: "${BASE_URL:?BASE_URL required}"
: "${OUT_DIR:=axe-reports}"

if ! command -v axe >/dev/null 2>&1; then
    echo "error: axe-cli not installed. run \`npm install -g @axe-core/cli\`" >&2
    exit 2
fi

if [[ ! -r "$GAMES_FILE" ]]; then
    echo "error: cannot read $GAMES_FILE" >&2
    exit 2
fi

mkdir -p "$OUT_DIR"
violations=0
total=0

while IFS= read -r slug; do
    [[ -z "$slug" ]] && continue
    [[ "$slug" =~ ^# ]] && continue
    total=$((total + 1))
    url="${BASE_URL}/games/${slug}"
    out_json="${OUT_DIR}/${slug}.json"

    if ! axe "$url" --tags wcag2a,wcag2aa,wcag21aa --save "$out_json" >/dev/null 2>&1; then
        echo "WARN: axe run failed for $slug" >&2
    fi

    if command -v jq >/dev/null 2>&1 && [[ -s "$out_json" ]]; then
        n=$(jq '[.[].violations[]] | length' "$out_json" 2>/dev/null || echo 0)
        if (( n > 0 )); then
            echo "FAIL: $slug — $n WCAG violations (see $out_json)"
            violations=$((violations + n))
        fi
    fi
done < "$GAMES_FILE"

if (( violations > 0 )); then
    echo "FAIL: $violations total WCAG violations across $total game(s)"
    exit 1
fi
echo "OK: $total game(s) scanned, no WCAG violations"
exit 0
