#!/usr/bin/env bash
# Companion code for "The Backend of Luck" - Chapter 47, Platform Onboarding.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../../.." && pwd)"

CASINO_ID="${CASINO_ID:-secondary-host-br-staging}" \
BUNDLE_PARENT="${BUNDLE_PARENT:-/tmp/casino-bootstrap-cloudflare-bundles}" \
STATE_DIR="${STATE_DIR:-/tmp/casino-bootstrap-cloudflare-state}" \
CF_ZONE="${CF_ZONE:-cloud-acmetocasino.com}" \
CLOUDFLARE_SSL_METHOD="${CLOUDFLARE_SSL_METHOD:-txt}" \
YES_REAL_APPLY="${YES_REAL_APPLY:-}" \
REQUIRE_CLOUDFLARE_ACTIVE="${REQUIRE_CLOUDFLARE_ACTIVE:-}" \
  "${ROOT_DIR}/new-platform/bootstrap-casino/scripts/apply-cloudflare-deployment.sh"
