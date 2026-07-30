#!/bin/bash
# Companion code for "The Backend of Luck" - Chapter 27, Data Residency and Backup/Recovery.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# Patroni container entrypoint
# Waits for etcd, then starts Patroni which manages PostgreSQL lifecycle.

set -e

echo "=== PostgreSQL HA — Patroni entrypoint ==="

# Fix data directory permissions (Docker volumes can mount as root-owned)
if [ -d "/var/lib/postgresql/data" ]; then
    chmod 0700 /var/lib/postgresql/data 2>/dev/null || true
fi
echo "Node name: ${PATRONI_NAME:-unknown}"
echo "Scope:     ${PATRONI_SCOPE:-unknown}"

# Wait for etcd to be reachable
ETCD_HOST="${PATRONI_ETCD3_HOSTS:-etcd:2379}"
echo "Waiting for etcd at ${ETCD_HOST}..."
until curl -sf "http://${ETCD_HOST}/health" > /dev/null 2>&1; do
    echo "  etcd not ready — retrying in 2s..."
    sleep 2
done
echo "etcd is ready."

# Substitute passwords from env into patroni config
# Patroni supports env var expansion in its YAML natively,
# but we also make the pgpass file for pg_rewind
cat > /tmp/pgpass <<EOF
*:*:*:postgres:${POSTGRES_PASSWORD:?set POSTGRES_PASSWORD}
*:*:*:replicator:${PATRONI_REPLICATION_PASSWORD:?set PATRONI_REPLICATION_PASSWORD}
*:*:*:rewind_user:rewind_2024
EOF
chmod 600 /tmp/pgpass

echo "Starting Patroni..."
exec patroni /etc/patroni.yml
