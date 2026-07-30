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
BRAND_NAME="${BRAND_NAME:-secondary-host Brazil Staging}" \
JURISDICTION="${JURISDICTION:-br}" \
DOMAIN="${DOMAIN:-secondary-host-br.staging.internal}" \
ENVIRONMENT="${ENVIRONMENT:-staging}" \
RUNTIME="${RUNTIME:-k3s}" \
BUNDLE_PARENT="${BUNDLE_PARENT:-/tmp/casino-bootstrap-real-bundles}" \
PLAN_DIR="${PLAN_DIR:-/tmp/casino-bootstrap-real-plans}" \
VIP="${VIP:-10.0.10.250}" \
PROVIDERS="${PROVIDERS:-bao,dns,grafana,cloudflare}" \
CF_ZONE="${CF_ZONE:-cloud-acmetocasino.com}" \
CLOUDFLARE_SSL_METHOD="${CLOUDFLARE_SSL_METHOD:-txt}" \
  "${ROOT_DIR}/new-platform/bootstrap-casino/scripts/plan-real-infra.sh"
