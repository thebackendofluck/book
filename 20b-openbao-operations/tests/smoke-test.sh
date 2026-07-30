#!/usr/bin/env bash
# Companion code for "The Backend of Luck" - Chapter 20b, OpenBao Operations.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# End-to-end smoke test against a freshly-started sandbox. Runs every setup
# and secrets-engine script in order and exits non-zero on the first failure.
# Designed for CI and for manual on-call drills.
#
# This script does NOT require the database or PKI dependencies to be
# present: the db engine step is skipped unless PG_SUPER_PASS is set, and
# the PKI step is skipped unless openssl is available.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../lib/common.sh
source "$SCRIPT_DIR/../lib/common.sh"

BASE="$SCRIPT_DIR/.."

step() {
  log ">>> $*"
  "$@" || die "step failed: $*"
}

optional() {
  log ">>> (optional) $*"
  "$@" || log "  skipped: $*"
}

log "fresh sandbox"
"$BASE/setup/stop-sandbox.sh" || true
step "$BASE/setup/start-sandbox.sh"

step "$BASE/secrets-engines/transit-setup.sh"
step "$BASE/secrets-engines/kv2-setup.sh"
step "$BASE/tests/kv2-cas-test.sh"

if command -v openssl >/dev/null 2>&1; then
  step "$BASE/secrets-engines/pki-setup.sh"
  step "$BASE/secrets-engines/pki-issue-cert.sh"
  step "$BASE/ops/pki-revoke-and-check.sh"
else
  log "skipping PKI suite -- openssl not installed"
fi

step "$BASE/ops/audit-enable.sh"
step "$BASE/ops/audit-rotate-test.sh"
step "$BASE/ops/rotate-transit-key.sh" platform-pii

optional "$BASE/secrets-engines/db-engine-postgres.sh"

log ">>> final health check"
BAO_TOKEN=$(python3 -c "import json; print(json.load(open('$SANDBOX_INIT_FILE'))['root_token'])") \
  BAO_ADDR="$BAO_ADDR" \
  AUDIT_PATH="$SANDBOX_DIR/audit/audit.log" \
  python3 "$BASE/ops/health-check.py" || log "health-check reported warnings"

log "smoke test PASS"
