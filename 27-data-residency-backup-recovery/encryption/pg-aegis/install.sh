#!/usr/bin/env bash
# Companion code for "The Backend of Luck" - Chapter 27, Data Residency and Backup/Recovery.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# install.sh — build and install pg_aegis extension via pgrx.
#
# Usage:
#   ./install.sh                 # build + install for pg16
#   PG_VERSION=pg17 ./install.sh
#
# Prereqs:
#   * rustc >= 1.78 (pgrx 0.12 requirement)
#   * postgresql-server-dev-16 installed
#   * pgrx-cli installed: cargo install --locked cargo-pgrx --version 0.12.9
#   * cargo pgrx init (first time only)

set -euo pipefail

PG_VERSION="${PG_VERSION:-pg16}"
PG_CONFIG="${PG_CONFIG:-$(command -v pg_config)}"

if [[ -z "$PG_CONFIG" ]]; then
    echo "ERROR: pg_config not found. apt install postgresql-server-dev-16" >&2
    exit 1
fi

echo "==> Using PG_VERSION=$PG_VERSION, pg_config=$PG_CONFIG"
echo "==> cargo pgrx install (release)"
cargo pgrx install --release --features "$PG_VERSION" --pg-config "$PG_CONFIG" --sudo

echo "==> Done. Next steps:"
cat <<EOF
  1. In postgresql.conf (or via ALTER SYSTEM):
       shared_preload_libraries = 'pg_aegis'
       pg_aegis.master_key_b64  = '<base64 of 32 random bytes>'
     Restart PostgreSQL.
  2. In your database:
       CREATE EXTENSION pg_aegis;
       SELECT aegis_generate_key('player_pii_key');
  3. Verify:
       SELECT aegis_version();
       SELECT aegis_decrypt(aegis_encrypt('hello', 'player_pii_key'), 'player_pii_key');
EOF
