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

# Tear down the PostgreSQL HA cluster and remove all volumes.
# Usage: ./destroy-postgres-ha.sh [--keep-volumes]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

KEEP_VOLUMES=false
[ "${1:-}" = "--keep-volumes" ] && KEEP_VOLUMES=true

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
info() { echo -e "${YELLOW}[destroy]${NC} $*"; }
pass() { echo -e "${GREEN}[OK]${NC} $*"; }

info "Stopping PostgreSQL HA cluster..."
docker compose down --remove-orphans 2>/dev/null || true
pass "Containers stopped"

if [ "$KEEP_VOLUMES" = "false" ]; then
    info "Removing volumes (etcd, pg-primary, pg-replica, wal-archive)..."
    docker compose down -v 2>/dev/null || true
    pass "Volumes removed"
else
    info "Volumes kept (--keep-volumes flag set)"
fi

info "Removing built images..."
docker rmi postgres-ha-pg-primary postgres-ha-pg-replica 2>/dev/null || true
pass "Images removed"

pass "PostgreSQL HA cluster destroyed."
echo ""
echo "To redeploy: ./deploy-postgres-ha.sh"
