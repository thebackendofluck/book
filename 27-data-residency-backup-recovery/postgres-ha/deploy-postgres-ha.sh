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

# One-shot deployment of PostgreSQL HA with Patroni, etcd, and HAProxy.
# Run this script once from the postgres-ha/ directory.
#
# Usage:
#   ./deploy-postgres-ha.sh [--primary-port 5432] [--replica-port 5433] [--haproxy-port 5000]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# ─── Defaults (override via CLI flags) ────────────────────────────────────
PRIMARY_PORT=15432
REPLICA_PORT=15433
HAPROXY_PORT=15000
HAPROXY_READONLY_PORT=15001
HAPROXY_STATS_PORT=17000

while [[ $# -gt 0 ]]; do
    case $1 in
        --primary-port)   PRIMARY_PORT="$2";         shift 2 ;;
        --replica-port)   REPLICA_PORT="$2";         shift 2 ;;
        --haproxy-port)   HAPROXY_PORT="$2";         shift 2 ;;
        --help|-h)
            echo "Usage: $0 [--primary-port N] [--replica-port N] [--haproxy-port N]"
            exit 0 ;;
        *) echo "Unknown flag: $1"; exit 1 ;;
    esac
done

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BOLD='\033[1m'; NC='\033[0m'
banner() { echo -e "\n${BOLD}══════════════════════════════════════════════════${NC}"; echo -e "${BOLD} $*${NC}"; echo -e "${BOLD}══════════════════════════════════════════════════${NC}"; }
pass()   { echo -e "${GREEN}[OK]${NC} $*"; }
fail()   { echo -e "${RED}[ERR]${NC} $*" >&2; exit 1; }
info()   { echo -e "${YELLOW}[..]${NC} $*"; }

COMPOSE_CMD="docker compose"

# ─── Step 1: Prerequisites ─────────────────────────────────────────────────
banner "Step 1: Checking prerequisites"

for cmd in docker curl psql pgbench pg_isready jq; do
    if command -v "$cmd" &>/dev/null; then
        pass "$cmd found"
    else
        info "$cmd not found — some tests will be skipped (non-fatal for deploy)"
    fi
done

$COMPOSE_CMD version &>/dev/null || fail "docker compose not available"
pass "docker compose ready"

# Check ports are free
for PORT in $PRIMARY_PORT $REPLICA_PORT $HAPROXY_PORT $HAPROXY_READONLY_PORT $HAPROXY_STATS_PORT 2379 8008 8009; do
    if ss -tlnp 2>/dev/null | grep -q ":${PORT} " || \
       netstat -tlnp 2>/dev/null | grep -q ":${PORT} "; then
        info "Port $PORT already in use — stopping existing cluster first"
        $COMPOSE_CMD down --remove-orphans 2>/dev/null || true
        break
    fi
done

# ─── Step 2: Environment ──────────────────────────────────────────────────
banner "Step 2: Setting up environment"

if [ ! -f ".env" ]; then
    cp .env.example .env
    info "Created .env from template — using default passwords (change for production)"
fi
pass ".env ready"

# Load env
set -a; source .env; set +a
PG_PASS="${POSTGRES_PASSWORD:?set POSTGRES_PASSWORD}"
export PGPASSWORD="$PG_PASS"

# Update docker-compose ports if overridden from defaults
if [ "$PRIMARY_PORT" != "5432" ] || [ "$REPLICA_PORT" != "5433" ] || [ "$HAPROXY_PORT" != "5000" ]; then
    info "Port overrides requested — updating docker-compose.yml..."
    sed -i \
        -e "s/\"5432:5432\"/\"${PRIMARY_PORT}:5432\"/" \
        -e "s/\"5433:5432\"/\"${REPLICA_PORT}:5432\"/" \
        -e "s/\"5000:5000\"/\"${HAPROXY_PORT}:5000\"/" \
        docker-compose.yml
    pass "Ports updated in docker-compose.yml"
fi

# ─── Step 3: Build images ─────────────────────────────────────────────────
banner "Step 3: Building Patroni image"
info "This may take 2–5 minutes on first run..."
$COMPOSE_CMD build --progress=plain 2>&1 | grep -E '^#|Step|Successfully|error|ERROR' || true
pass "Images built"

# ─── Step 4: Tear down any previous run ───────────────────────────────────
banner "Step 4: Clean state"
$COMPOSE_CMD down -v --remove-orphans 2>/dev/null || true
pass "Previous containers and volumes removed"

# ─── Step 5: Start etcd first ─────────────────────────────────────────────
banner "Step 5: Starting etcd"
$COMPOSE_CMD up -d etcd

info "Waiting for etcd health..."
ETCD_READY=false
for _ in $(seq 1 30); do
    if curl -sf http://localhost:2379/health 2>/dev/null | grep -q '"health":"true"'; then
        ETCD_READY=true
        break
    fi
    sleep 2; echo -n "."
done
echo ""
[ "$ETCD_READY" = "true" ] && pass "etcd healthy" || fail "etcd did not become healthy in 60s"

# ─── Step 6: Start primary ─────────────────────────────────────────────────
banner "Step 6: Starting pg-primary"
$COMPOSE_CMD up -d pg-primary

info "Waiting for pg-primary (PostgreSQL + Patroni bootstrap, up to 90s)..."
for _ in $(seq 1 45); do
    if pg_isready -h localhost -p "$PRIMARY_PORT" -U postgres -q 2>/dev/null; then
        ROLE=$(psql -h localhost -p "$PRIMARY_PORT" -U postgres -d postgres \
            -c "SELECT pg_is_in_recovery()::text;" -t -A 2>/dev/null || echo "error")
        if [ "$ROLE" = "false" ]; then
            pass "pg-primary is up and is the leader (not in recovery)"
            break
        fi
    fi
    sleep 2; echo -n "."
done
echo ""
pg_isready -h localhost -p "$PRIMARY_PORT" -U postgres -q 2>/dev/null \
    || fail "pg-primary not ready after 90s"

# ─── Step 7: Start replica ─────────────────────────────────────────────────
banner "Step 7: Starting pg-replica"
$COMPOSE_CMD up -d pg-replica

info "Waiting for pg-replica (clone + streaming, up to 120s)..."
for _ in $(seq 1 60); do
    if pg_isready -h localhost -p "$REPLICA_PORT" -U postgres -q 2>/dev/null; then
        pass "pg-replica is up"
        break
    fi
    sleep 2; echo -n "."
done
echo ""

# ─── Step 8: Start HAProxy ────────────────────────────────────────────────
banner "Step 8: Starting HAProxy"
$COMPOSE_CMD up -d haproxy
sleep 5

# Verify HAProxy routes correctly
HAPROXY_ROLE=$(psql -h localhost -p "$HAPROXY_PORT" -U postgres -d postgres \
    -c "SELECT pg_is_in_recovery()::text;" -t -A 2>/dev/null || echo "error")
if [ "$HAPROXY_ROLE" = "false" ]; then
    pass "HAProxy port $HAPROXY_PORT → primary (not in recovery)"
else
    info "HAProxy routing check: $HAPROXY_ROLE (replica may still be syncing)"
fi

# ─── Step 9: Patroni cluster status ───────────────────────────────────────
banner "Step 9: Patroni cluster status"
$COMPOSE_CMD exec -T pg-primary patronictl -c /etc/patroni.yml list 2>/dev/null || \
    info "patronictl not yet ready — cluster still bootstrapping"

# ─── Step 10: Replication status ──────────────────────────────────────────
banner "Step 10: Replication status"
psql -h localhost -p "$PRIMARY_PORT" -U postgres -d postgres \
    -c "SELECT client_addr, state, sync_state, sent_lsn, replay_lsn FROM pg_stat_replication;" \
    2>/dev/null || info "No replication slots yet — replica still syncing"

# ─── Step 11: Initial data parity check ───────────────────────────────────
banner "Step 11: Initial data parity check"
if pg_isready -h localhost -p "$REPLICA_PORT" -U postgres -q 2>/dev/null; then
    P_ROWS=$(psql -h localhost -p "$PRIMARY_PORT" -U postgres -d postgres \
        -c "SELECT count(*) FROM pg_stat_user_tables;" -t -A 2>/dev/null || echo "0")
    info "Tables visible on primary: $P_ROWS"
    pass "Cluster is up — run ./data-parity-check.sh after inserting data"
fi

# ─── Done ─────────────────────────────────────────────────────────────────
banner "Deployment complete"
cat <<EOF

  PostgreSQL HA cluster is running.

  Connection strings:
    Primary (read-write):   postgresql://postgres:${PG_PASS}@localhost:${HAPROXY_PORT}/postgres
    Replicas (read-only):   postgresql://postgres:${PG_PASS}@localhost:${HAPROXY_READONLY_PORT}/postgres
    Direct primary (15432): postgresql://postgres:${PG_PASS}@localhost:${PRIMARY_PORT}/postgres
    Direct replica (15433): postgresql://postgres:${PG_PASS}@localhost:${REPLICA_PORT}/postgres
    HAProxy stats UI:       http://localhost:${HAPROXY_STATS_PORT}/stats  (admin / haproxy_stats_2024)
    Patroni primary API:    http://localhost:8008
    Patroni replica API:    http://localhost:8009
    etcd:                   http://localhost:2379

  Next steps:
    ./test-failover.sh           # full failover test suite
    ./benchmark-ha.sh            # pgbench performance benchmark
    ./data-parity-check.sh       # verify primary/replica parity
    ./destroy-postgres-ha.sh     # tear everything down

EOF
