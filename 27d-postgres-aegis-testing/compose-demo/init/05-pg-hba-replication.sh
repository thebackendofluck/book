#!/bin/sh
# Companion code for "The Backend of Luck" - Chapter 27d, PostgreSQL Aegis.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# 05-pg-hba-replication.sh
#
# Appends a replication trust line to pg_hba.conf so the demo replica can
# bootstrap via pg_basebackup. Demo-only — production uses SCRAM-SHA-256
# with a dedicated replicator role whose password lives in OpenBao.
#
# The suffix "05-" runs this before the SQL files in the same directory.

set -eu

HBA="${PGDATA}/pg_hba.conf"

if grep -q '^# aegis-demo: replication' "$HBA" 2>/dev/null; then
  echo "[pg-hba] replication line already present"
  exit 0
fi

cat >>"$HBA" <<'EOF'
# aegis-demo: replication (demo-only trust, NEVER use in production)
host    replication     demo            0.0.0.0/0               trust
host    replication     demo            ::/0                    trust
EOF

echo "[pg-hba] appended replication line"
