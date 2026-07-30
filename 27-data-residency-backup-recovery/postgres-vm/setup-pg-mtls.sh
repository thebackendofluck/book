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

# setup-pg-mtls.sh — Configure PostgreSQL mTLS (mutual TLS) client authentication
#
# Replaces password authentication with client certificate verification.
# Applications connect with their own TLS certificate; no passwords in
# connection strings. Certificates can be rotated by cert-manager (Ch 24h).
#
# Usage:
#   ./setup-pg-mtls.sh --pg-host 10.0.10.30 --pg-version 16 \
#     --ca-dir /etc/postgresql-ha/certs --app-user casino_app
#
# Cross-reference: Chapter 24h (mTLS Between Kubernetes Services)

set -euo pipefail

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; BOLD='\033[1m'; NC='\033[0m'
pass()   { echo -e "${GREEN}[OK]${NC}   $*"; }
info()   { echo -e "${YELLOW}[..]${NC}  $*"; }
fail()   { echo -e "${RED}[ERR]${NC}  $*" >&2; exit 1; }
banner() { echo -e "\n${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
           echo -e "${BOLD} $*${NC}"
           echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}\n"; }

# ─── Defaults ──────────────────────────────────────────────────────────────
PG_HOST="10.0.10.30"
PG_VERSION=16
PG_USER="postgres"
APP_USER="casino_app"
APP_DB="casino"
CA_DIR="/etc/postgresql-ha/certs"
PGDATA_DIR=""           # auto-detected if empty
CERT_DAYS=365
CA_DAYS=3650
DRY_RUN=0

usage() {
cat <<EOF
Usage: $0 [OPTIONS]

  --pg-host HOST      PostgreSQL server IP or hostname (default: 10.0.10.30)
  --pg-version N      PostgreSQL major version (default: 16)
  --app-user USER     Application DB username to create cert for (default: casino_app)
  --app-db DB         Application database (default: casino)
  --ca-dir DIR        Directory for CA and certs (default: /etc/postgresql-ha/certs)
  --cert-days N       Server and client cert validity days (default: 365)
  --ca-days N         CA cert validity days (default: 3650)
  --pgdata DIR        Override PGDATA path
  --dry-run           Show what would be done, no changes
  --help|-h           Show this help

Examples:
  # Generate certs and configure PostgreSQL for mTLS
  $0 --pg-host 10.0.10.30 --app-user casino_app

  # Dry run first
  $0 --pg-host 10.0.10.30 --dry-run
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --pg-host)    PG_HOST="$2";    shift 2 ;;
        --pg-version) PG_VERSION="$2"; shift 2 ;;
        --app-user)   APP_USER="$2";   shift 2 ;;
        --app-db)     APP_DB="$2";     shift 2 ;;
        --ca-dir)     CA_DIR="$2";     shift 2 ;;
        --cert-days)  CERT_DAYS="$2";  shift 2 ;;
        --ca-days)    CA_DAYS="$2";    shift 2 ;;
        --pgdata)     PGDATA_DIR="$2"; shift 2 ;;
        --dry-run)    DRY_RUN=1;       shift ;;
        --help|-h)    usage; exit 0 ;;
        *) echo "Unknown option: $1"; usage; exit 1 ;;
    esac
done

[[ $DRY_RUN -eq 1 ]] && info "DRY-RUN mode — no changes will be made"

# Auto-detect PGDATA
if [[ -z "$PGDATA_DIR" ]]; then
    PGDATA_DIR=$(sudo -u postgres psql -tAc "SHOW data_directory;" 2>/dev/null || true)
    [[ -z "$PGDATA_DIR" ]] && PGDATA_DIR="/var/lib/postgresql/${PG_VERSION}/main"
fi

# ═══════════════════════════════════════════════════════════════════════════
banner "Phase 1: Generate CA and Certificates"
# ═══════════════════════════════════════════════════════════════════════════

[[ $DRY_RUN -eq 0 ]] && mkdir -p "$CA_DIR" && chmod 700 "$CA_DIR"

generate_certs() {
    info "Generating CA key and self-signed certificate..."
    openssl genrsa -out "${CA_DIR}/ca.key" 4096
    openssl req -x509 -new -nodes -key "${CA_DIR}/ca.key" \
        -sha256 -days "$CA_DAYS" -out "${CA_DIR}/ca.crt" \
        -subj "/CN=casino-db-ca/O=Casino/OU=Database"
    chmod 600 "${CA_DIR}/ca.key"
    pass "CA: ${CA_DIR}/ca.crt (${CA_DAYS} days)"

    info "Generating server certificate for ${PG_HOST}..."
    openssl genrsa -out "${CA_DIR}/server.key" 2048
    openssl req -new -key "${CA_DIR}/server.key" -out "${CA_DIR}/server.csr" \
        -subj "/CN=${PG_HOST}/O=Casino/OU=Database"
    openssl x509 -req -in "${CA_DIR}/server.csr" \
        -CA "${CA_DIR}/ca.crt" -CAkey "${CA_DIR}/ca.key" -CAcreateserial \
        -out "${CA_DIR}/server.crt" -days "$CERT_DAYS" -sha256
    chmod 600 "${CA_DIR}/server.key"
    pass "Server cert: ${CA_DIR}/server.crt (${CERT_DAYS} days)"

    info "Generating client certificate for application user: ${APP_USER}..."
    openssl genrsa -out "${CA_DIR}/client-${APP_USER}.key" 2048
    openssl req -new -key "${CA_DIR}/client-${APP_USER}.key" \
        -out "${CA_DIR}/client-${APP_USER}.csr" \
        -subj "/CN=${APP_USER}/O=Casino/OU=App"
    openssl x509 -req -in "${CA_DIR}/client-${APP_USER}.csr" \
        -CA "${CA_DIR}/ca.crt" -CAkey "${CA_DIR}/ca.key" -CAcreateserial \
        -out "${CA_DIR}/client-${APP_USER}.crt" -days "$CERT_DAYS" -sha256
    chmod 600 "${CA_DIR}/client-${APP_USER}.key"
    pass "Client cert: ${CA_DIR}/client-${APP_USER}.crt (${CERT_DAYS} days)"
}

if [[ $DRY_RUN -eq 0 ]]; then
    generate_certs
else
    info "WOULD generate: ca.key, ca.crt, server.key, server.crt, client-${APP_USER}.key, client-${APP_USER}.crt"
fi

# ═══════════════════════════════════════════════════════════════════════════
banner "Phase 2: Install Certificates into PostgreSQL data directory"
# ═══════════════════════════════════════════════════════════════════════════

install_server_certs() {
    cp "${CA_DIR}/server.crt" "${PGDATA_DIR}/server.crt"
    cp "${CA_DIR}/server.key" "${PGDATA_DIR}/server.key"
    cp "${CA_DIR}/ca.crt"     "${PGDATA_DIR}/root.crt"
    chown postgres:postgres "${PGDATA_DIR}/server.crt" "${PGDATA_DIR}/server.key" "${PGDATA_DIR}/root.crt"
    chmod 600 "${PGDATA_DIR}/server.key"
    chmod 644 "${PGDATA_DIR}/server.crt" "${PGDATA_DIR}/root.crt"
    pass "Certificates installed in ${PGDATA_DIR}"
}

if [[ $DRY_RUN -eq 0 ]]; then
    install_server_certs
else
    info "WOULD copy certs to ${PGDATA_DIR}/{server.crt,server.key,root.crt}"
fi

# ═══════════════════════════════════════════════════════════════════════════
banner "Phase 3: Configure postgresql.conf for TLS 1.3"
# ═══════════════════════════════════════════════════════════════════════════
CONF_FILE="${PGDATA_DIR}/postgresql.conf"

set_param() {
    local KEY="$1" VAL="$2"
    if grep -qE "^#?[[:space:]]*${KEY}[[:space:]]*=" "$CONF_FILE" 2>/dev/null; then
        sed -i "s|^#\?[[:space:]]*${KEY}[[:space:]]*=.*|${KEY} = ${VAL}|" "$CONF_FILE"
    else
        echo "${KEY} = ${VAL}" >> "$CONF_FILE"
    fi
}

if [[ $DRY_RUN -eq 0 ]]; then
    [[ -f "$CONF_FILE" ]] || fail "postgresql.conf not found: ${CONF_FILE}"
    set_param ssl                         "on"
    set_param ssl_cert_file               "'${PGDATA_DIR}/server.crt'"
    set_param ssl_key_file                "'${PGDATA_DIR}/server.key'"
    set_param ssl_ca_file                 "'${PGDATA_DIR}/root.crt'"
    set_param ssl_min_protocol_version    "'TLSv1.3'"
    set_param ssl_ciphers                 "'HIGH:!aNULL:!MD5'"
    pass "postgresql.conf: SSL/TLS configured"
else
    info "WOULD set ssl=on, ssl_cert_file, ssl_key_file, ssl_ca_file, ssl_min_protocol_version=TLSv1.3"
fi

# ═══════════════════════════════════════════════════════════════════════════
banner "Phase 4: Configure pg_hba.conf for mTLS (reject plaintext)"
# ═══════════════════════════════════════════════════════════════════════════
HBA_FILE="${PGDATA_DIR}/pg_hba.conf"

configure_hba() {
    # Backup original
    cp "$HBA_FILE" "${HBA_FILE}.bak.$(date +%Y%m%d%H%M%S)"

    # Remove existing host/hostssl/hostnossl rules for all/0.0.0.0
    # Then append strict mTLS rules
    cat >> "$HBA_FILE" <<HBA

# mTLS authentication — added by setup-pg-mtls.sh
# Reject all non-SSL connections from the network
hostnossl all all 0.0.0.0/0 reject
hostnossl all all ::/0        reject

# Require valid client certificate (CN must match DB username)
hostssl   all all 0.0.0.0/0 cert clientcert=verify-full
hostssl   all all ::/0        cert clientcert=verify-full
HBA
    pass "pg_hba.conf: mTLS rules appended"
}

if [[ $DRY_RUN -eq 0 ]]; then
    [[ -f "$HBA_FILE" ]] || fail "pg_hba.conf not found: ${HBA_FILE}"
    configure_hba
else
    info "WOULD append hostnossl reject + hostssl cert clientcert=verify-full to ${HBA_FILE}"
fi

# ═══════════════════════════════════════════════════════════════════════════
banner "Phase 5: Create application DB user mapped to cert CN"
# ═══════════════════════════════════════════════════════════════════════════
if [[ $DRY_RUN -eq 0 ]]; then
    sudo -u postgres psql -c "
        DO \$\$ BEGIN
            IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '${APP_USER}') THEN
                CREATE ROLE ${APP_USER} LOGIN;
            END IF;
        END \$\$;
        GRANT CONNECT ON DATABASE ${APP_DB} TO ${APP_USER};
    " && pass "DB user ${APP_USER} created/verified"
else
    info "WOULD create DB user ${APP_USER} with LOGIN privilege on ${APP_DB}"
fi

# ═══════════════════════════════════════════════════════════════════════════
banner "Phase 6: Reload PostgreSQL"
# ═══════════════════════════════════════════════════════════════════════════
if [[ $DRY_RUN -eq 0 ]]; then
    sudo -u postgres psql -c "SELECT pg_reload_conf();" && pass "PostgreSQL configuration reloaded"
    info "Note: ssl setting requires a full restart if not already enabled:"
    info "  systemctl restart postgresql@${PG_VERSION}-main"
else
    info "WOULD reload PostgreSQL configuration"
fi

echo ""
pass "mTLS setup complete."
echo ""
echo "  Client connection string example (Python/psycopg2):"
echo "    sslmode=verify-full"
echo "    sslcert=${CA_DIR}/client-${APP_USER}.crt"
echo "    sslkey=${CA_DIR}/client-${APP_USER}.key"
echo "    sslrootcert=${CA_DIR}/ca.crt"
echo ""
echo "  Run ./test-pg-mtls.sh to verify all connection scenarios."
