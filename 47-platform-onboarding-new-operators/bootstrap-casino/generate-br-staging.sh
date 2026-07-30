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

python3 "${ROOT_DIR}/new-platform/bootstrap-casino/bootstrap_casino.py" \
  --casino-id acme-br-staging \
  --brand-name "Acme Brazil Staging" \
  --jurisdiction br \
  --domain acme-br.staging.internal \
  --environment staging \
  --runtime k3s \
  --output "${ROOT_DIR}/out/bootstrap-jurisdictions" \
  --force
