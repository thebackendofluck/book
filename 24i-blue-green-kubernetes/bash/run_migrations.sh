#!/usr/bin/env bash
# Companion code for "The Backend of Luck" - Chapter 24i, Blue-Green Cluster Switching for iGaming Kubernetes Environm.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# run_migrations.sh — apply pending schema migrations to the shared player database
#
# THIS IS A MANUAL, GATED STEP. NOTHING CALLS IT AUTOMATICALLY.
#
# The header used to claim it was "called from provisioning script if
# MIGRATIONS_PENDING=1". No caller existed and that variable appears nowhere in
# the repository, so the script had never run. It is deliberately still not wired
# into rotation-driver.sh, because an unattended 02:00 migration is incompatible
# with the thing that makes this whole rotation safe: the ability to abandon the
# new colour and stay on the old one. switchover.sh can roll traffic and the
# primary lease back to the previous colour in seconds. A schema change cannot be
# rolled back on the same timescale, and the previous colour is running the
# previous release's code.
#
# ── The expand/contract rule ─────────────────────────────────────────────────
#
# Both colours share ONE PostgreSQL. During a rotation, code from two releases
# can be pointed at the schema at the same time: the outgoing colour keeps
# serving reads for the five minutes of drain, and for the hour before teardown
# it is the rollback target. So every migration applied here must be readable and
# writable by BOTH the old and the new release. In practice:
#
#   EXPAND (safe to run before the switchover)
#     * add a nullable column, or a column with a default
#     * add a new table
#     * add an index (CONCURRENTLY, so it does not lock writes)
#     * add a permissive CHECK / a new enum value
#     * start writing to both the old and the new column in the new release
#
#   CONTRACT (only after the old colour is destroyed and will never come back,
#             i.e. a later rotation, never the same one)
#     * drop a column, table, or index
#     * make a column NOT NULL
#     * rename anything (a rename is a drop plus an add to the old code)
#     * tighten a constraint, narrow a type
#
# A migration that is not purely EXPAND makes rollback to the previous colour
# unsafe, and the operator has to decide that consciously. That is why this
# script refuses to run without an explicit confirmation.
#
# ── What the operator must verify before running this ────────────────────────
#
#   1. Every migration in this batch is EXPAND-only by the list above.
#      `flyway info` is printed below; read it, do not skim it.
#   2. The CURRENTLY ACTIVE release can run against the new schema. Not the new
#      release: the old one, because it keeps serving during the drain and it is
#      the rollback target for the next hour.
#   3. Any CREATE INDEX is CONCURRENTLY, and no migration takes an ACCESS
#      EXCLUSIVE lock on wallet, bet, or player tables. A migration that blocks
#      writes on the wallet tables is a betting outage, whichever colour is live.
#   4. You have a restore point. Note the timestamp; a shared database means a
#      bad migration is not fixable by rotating clusters.
#   5. Migrations are applied BEFORE the switchover (during the provisioning
#      window), never between haproxy_switch and verify_traffic.
#
# Usage:
#   POSTGRES_HOST=... POSTGRES_MIGRATION_PASSWORD=... \
#   MIGRATION_EXPAND_ONLY_CONFIRMED=yes ./run_migrations.sh [--dry-run]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MIGRATIONS_DIR="${MIGRATIONS_DIR:-${SCRIPT_DIR}/../migrations}"
POSTGRES_DB="${POSTGRES_DB:-casino_prod}"
POSTGRES_MIGRATION_USER="${POSTGRES_MIGRATION_USER:-casino_migrator}"
FLYWAY_IMAGE="${FLYWAY_IMAGE:-flyway/flyway:10}"
DRY_RUN=0
[[ "${1:-}" == "--dry-run" ]] && DRY_RUN=1

log() { echo "$(date -u '+%Y-%m-%dT%H:%M:%SZ') [MIGRATION] $*"; }
die() { log "ERROR: $*"; exit 1; }

: "${POSTGRES_HOST:?POSTGRES_HOST must be set}"
: "${POSTGRES_MIGRATION_PASSWORD:?POSTGRES_MIGRATION_PASSWORD must be set (fetch it from Vault)}"
[[ -d "$MIGRATIONS_DIR" ]] || die "Migrations directory not found: $MIGRATIONS_DIR"

if [[ "${MIGRATION_EXPAND_ONLY_CONFIRMED:-no}" != "yes" ]]; then
    die "Refusing to migrate the shared player database. Read the expand/contract rule at the top of this script, verify the five points listed there, then re-run with MIGRATION_EXPAND_ONLY_CONFIRMED=yes. Use --dry-run to see the pending batch first."
fi

JDBC_URL="jdbc:postgresql://${POSTGRES_HOST}:5432/${POSTGRES_DB}"

# The password goes in a mode-0600 config file, never on the docker run command
# line and never in -e. "docker inspect" prints both Config.Cmd and Config.Env
# verbatim, to anyone in the docker group, for the life of the container; a
# mounted file's contents are not in that output. The file is created with
# restrictive permissions before anything is written to it and removed on exit.
CONF_DIR="$(mktemp -d)"
cleanup() {
    # Overwrite before unlinking: the file held a production credential.
    if [[ -f "${CONF_DIR}/flyway.conf" ]]; then
        : > "${CONF_DIR}/flyway.conf"
    fi
    rm -rf "$CONF_DIR"
}
trap cleanup EXIT
chmod 700 "$CONF_DIR"

CONF_FILE="${CONF_DIR}/flyway.conf"
(umask 077 && : > "$CONF_FILE")
cat > "$CONF_FILE" <<EOF
flyway.url=${JDBC_URL}
flyway.user=${POSTGRES_MIGRATION_USER}
flyway.password=${POSTGRES_MIGRATION_PASSWORD}
flyway.locations=filesystem:/flyway/sql
flyway.outOfOrder=false
flyway.cleanDisabled=true
EOF

flyway() {
    docker run --rm \
        -v "${MIGRATIONS_DIR}:/flyway/sql:ro" \
        -v "${CONF_DIR}:/flyway/conf:ro" \
        "$FLYWAY_IMAGE" "$@"
}

log "Target database: ${JDBC_URL} as ${POSTGRES_MIGRATION_USER}"
log "Migration directory: ${MIGRATIONS_DIR}"

log "Pending migrations:"
flyway info || die "flyway info failed; not attempting to migrate"

# Checksums of already-applied migrations must still match, or somebody edited a
# migration that both colours have already run.
log "Validating applied migrations..."
flyway validate || die "flyway validate failed; an applied migration has been modified"

if [[ "$DRY_RUN" -eq 1 ]]; then
    log "--dry-run: stopping before migrate. Nothing was applied."
    exit 0
fi

log "Applying migrations..."
flyway migrate || die "flyway migrate failed; database may be partially migrated — check flyway info"

log "Migrations complete. Re-run switchover only after confirming the ACTIVE colour still serves traffic."
flyway info || true
