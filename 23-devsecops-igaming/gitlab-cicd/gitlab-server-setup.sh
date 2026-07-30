#!/usr/bin/env bash
# Companion code for "The Backend of Luck" - Chapter 23, DevSecOps for iGaming.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

set -euo pipefail

# =============================================================================
# GitLab CE On-Premises Server Setup
# =============================================================================
# Deploys GitLab Community Edition via Docker Compose for iGaming platform
# development. Includes PostgreSQL, container registry, LDAP/SAML auth,
# automated backups, and monitoring integration.
#
# Usage:
#   ./gitlab-server-setup.sh --domain <gitlab.example.com> \
#       [--ssl letsencrypt|custom] [--ssl-cert /path/cert.pem] \
#       [--ssl-key /path/key.pem] [--smtp-host smtp.example.com] \
#       [--ldap-host ldap.example.com] [--backup-s3-endpoint http://minio:9000]
#
# Requirements:
#   - Docker and Docker Compose v2
#   - At least 8 GB RAM, 4 CPU cores, 50 GB disk
#   - DNS record pointing to this server
#
# iGaming context:
#   Self-hosted GitLab is common in regulated gambling environments where
#   source code and CI/CD secrets must remain within the operator's
#   infrastructure perimeter. This script sets up a production-grade instance
#   with compliance-oriented defaults.
# =============================================================================

# -- Defaults --
DOMAIN=""
SSL_MODE="letsencrypt"
SSL_CERT=""
SSL_KEY=""
GITLAB_HOME="/opt/gitlab"
SMTP_HOST=""
SMTP_PORT="587"
SMTP_USER=""
SMTP_PASSWORD=""
SMTP_FROM=""
LDAP_ENABLED=false
LDAP_HOST=""
LDAP_PORT="636"
LDAP_BIND_DN=""
LDAP_PASSWORD=""
LDAP_BASE=""
SAML_ENABLED=false
SAML_IDP_SSO_URL=""
SAML_IDP_CERT=""
BACKUP_S3_ENDPOINT=""
BACKUP_S3_BUCKET="gitlab-backups"
BACKUP_S3_ACCESS_KEY=""
BACKUP_S3_SECRET_KEY=""
PG_EXTERNAL=false
PG_HOST=""
PG_PORT="5432"
PG_DATABASE="gitlabhq_production"
PG_USER="gitlab"
PG_PASSWORD=""

# =============================================================================
# Functions
# =============================================================================

usage() {
    cat <<EOF
Usage: $(basename "$0") [OPTIONS]

Deploy GitLab CE on-premises for iGaming platform development.

Required:
  --domain <fqdn>              GitLab domain (e.g., gitlab.igaming.internal)

SSL:
  --ssl <mode>                  SSL mode: letsencrypt, custom, none (default: letsencrypt)
  --ssl-cert <path>             Path to SSL certificate (for custom mode)
  --ssl-key <path>              Path to SSL private key (for custom mode)

Email:
  --smtp-host <host>            SMTP server hostname
  --smtp-port <port>            SMTP port (default: 587)
  --smtp-user <user>            SMTP username
  --smtp-password <pass>        SMTP password
  --smtp-from <email>           From address for GitLab emails

Authentication:
  --ldap-host <host>            Enable LDAP and set host
  --ldap-port <port>            LDAP port (default: 636)
  --ldap-bind-dn <dn>           LDAP bind DN
  --ldap-password <pass>        LDAP bind password
  --ldap-base <base>            LDAP search base
  --saml-sso-url <url>          Enable SAML and set IdP SSO URL
  --saml-cert <path>            SAML IdP certificate path

Database:
  --pg-host <host>              Use external PostgreSQL (skip embedded)
  --pg-port <port>              PostgreSQL port (default: 5432)
  --pg-database <db>            Database name (default: gitlabhq_production)
  --pg-user <user>              Database user (default: gitlab)
  --pg-password <pass>          Database password

Backup:
  --backup-s3-endpoint <url>    S3-compatible backup endpoint
  --backup-s3-bucket <name>     Backup bucket (default: gitlab-backups)
  --backup-s3-access <key>      S3 access key
  --backup-s3-secret <key>      S3 secret key

  --help                        Show this help

Examples:
  # Basic with Let's Encrypt
  $(basename "$0") --domain gitlab.igaming.internal

  # Custom CA + external PostgreSQL + LDAP
  $(basename "$0") --domain gitlab.igaming.internal \\
      --ssl custom --ssl-cert /etc/ssl/gitlab.pem --ssl-key /etc/ssl/gitlab.key \\
      --pg-host db.internal --pg-password secret \\
      --ldap-host ldap.internal --ldap-base "dc=igaming,dc=internal"
EOF
    exit 0
}

log_info() {
    echo "[INFO]  $(date -u +%Y-%m-%dT%H:%M:%SZ) $*"
}

log_warn() {
    echo "[WARN]  $(date -u +%Y-%m-%dT%H:%M:%SZ) $*" >&2
}

log_error() {
    echo "[ERROR] $(date -u +%Y-%m-%dT%H:%M:%SZ) $*" >&2
}

check_prerequisites() {
    local missing=()

    if ! command -v docker &>/dev/null; then
        missing+=("docker")
    fi

    if ! docker compose version &>/dev/null 2>&1; then
        missing+=("docker-compose-v2")
    fi

    if [[ ${#missing[@]} -gt 0 ]]; then
        log_error "Missing prerequisites: ${missing[*]}"
        exit 1
    fi

    # Check system resources
    local mem_gb
    mem_gb=$(awk '/MemTotal/ {printf "%.0f", $2/1024/1024}' /proc/meminfo 2>/dev/null || echo "0")
    if [[ "${mem_gb}" -lt 7 ]]; then
        log_warn "System has ${mem_gb} GB RAM. GitLab recommends at least 8 GB."
    fi
}

# =============================================================================
# Parse arguments
# =============================================================================
while [[ $# -gt 0 ]]; do
    case $1 in
        --domain)            DOMAIN="$2";              shift 2 ;;
        --ssl)               SSL_MODE="$2";            shift 2 ;;
        --ssl-cert)          SSL_CERT="$2";            shift 2 ;;
        --ssl-key)           SSL_KEY="$2";             shift 2 ;;
        --smtp-host)         SMTP_HOST="$2";           shift 2 ;;
        --smtp-port)         SMTP_PORT="$2";           shift 2 ;;
        --smtp-user)         SMTP_USER="$2";           shift 2 ;;
        --smtp-password)     SMTP_PASSWORD="$2";       shift 2 ;;
        --smtp-from)         SMTP_FROM="$2";           shift 2 ;;
        --ldap-host)         LDAP_ENABLED=true; LDAP_HOST="$2"; shift 2 ;;
        --ldap-port)         LDAP_PORT="$2";           shift 2 ;;
        --ldap-bind-dn)      LDAP_BIND_DN="$2";       shift 2 ;;
        --ldap-password)     LDAP_PASSWORD="$2";       shift 2 ;;
        --ldap-base)         LDAP_BASE="$2";           shift 2 ;;
        --saml-sso-url)      SAML_ENABLED=true; SAML_IDP_SSO_URL="$2"; shift 2 ;;
        --saml-cert)         SAML_IDP_CERT="$2";       shift 2 ;;
        --pg-host)           PG_EXTERNAL=true; PG_HOST="$2"; shift 2 ;;
        --pg-port)           PG_PORT="$2";             shift 2 ;;
        --pg-database)       PG_DATABASE="$2";         shift 2 ;;
        --pg-user)           PG_USER="$2";             shift 2 ;;
        --pg-password)       PG_PASSWORD="$2";         shift 2 ;;
        --backup-s3-endpoint) BACKUP_S3_ENDPOINT="$2"; shift 2 ;;
        --backup-s3-bucket)  BACKUP_S3_BUCKET="$2";    shift 2 ;;
        --backup-s3-access)  BACKUP_S3_ACCESS_KEY="$2"; shift 2 ;;
        --backup-s3-secret)  BACKUP_S3_SECRET_KEY="$2"; shift 2 ;;
        --help)              usage ;;
        *)
            log_error "Unknown option: $1"
            usage
            ;;
    esac
done

if [[ -z "${DOMAIN}" ]]; then
    log_error "--domain is required."
    usage
fi

# =============================================================================
# Main setup
# =============================================================================

check_prerequisites

log_info "Setting up GitLab CE for iGaming platform"
log_info "Domain: ${DOMAIN}"
log_info "SSL mode: ${SSL_MODE}"
log_info "GitLab home: ${GITLAB_HOME}"

# -- Step 1: Create directory structure --
log_info "Step 1: Creating directory structure..."

mkdir -p "${GITLAB_HOME}"/{config,logs,data}
mkdir -p "${GITLAB_HOME}/config/ssl"
mkdir -p "${GITLAB_HOME}/backups"

# -- Step 2: Build external URL and SSL config --
EXTERNAL_URL="https://${DOMAIN}"
NGINX_SSL_CONFIG=""

case "${SSL_MODE}" in
    letsencrypt)
        NGINX_SSL_CONFIG="
    letsencrypt['enable'] = true
    letsencrypt['contact_emails'] = ['admin@${DOMAIN}']
    letsencrypt['auto_renew'] = true
    letsencrypt['auto_renew_hour'] = 3
    letsencrypt['auto_renew_day_of_month'] = '*/7'"
        ;;
    custom)
        if [[ -z "${SSL_CERT}" || -z "${SSL_KEY}" ]]; then
            log_error "Custom SSL requires --ssl-cert and --ssl-key"
            exit 1
        fi
        cp "${SSL_CERT}" "${GITLAB_HOME}/config/ssl/${DOMAIN}.crt"
        cp "${SSL_KEY}" "${GITLAB_HOME}/config/ssl/${DOMAIN}.key"
        chmod 600 "${GITLAB_HOME}/config/ssl/${DOMAIN}.key"
        NGINX_SSL_CONFIG="
    nginx['ssl_certificate'] = '/etc/gitlab/ssl/${DOMAIN}.crt'
    nginx['ssl_certificate_key'] = '/etc/gitlab/ssl/${DOMAIN}.key'
    letsencrypt['enable'] = false"
        ;;
    none)
        EXTERNAL_URL="http://${DOMAIN}"
        NGINX_SSL_CONFIG="
    letsencrypt['enable'] = false
    nginx['redirect_http_to_https'] = false"
        log_warn "SSL disabled. Not recommended for production iGaming environments."
        ;;
esac

# -- Step 3: Build SMTP config --
SMTP_CONFIG=""
if [[ -n "${SMTP_HOST}" ]]; then
    SMTP_FROM="${SMTP_FROM:-gitlab@${DOMAIN}}"
    SMTP_CONFIG="
    gitlab_rails['smtp_enable'] = true
    gitlab_rails['smtp_address'] = '${SMTP_HOST}'
    gitlab_rails['smtp_port'] = ${SMTP_PORT}
    gitlab_rails['smtp_user_name'] = '${SMTP_USER}'
    gitlab_rails['smtp_password'] = '${SMTP_PASSWORD}'
    gitlab_rails['smtp_authentication'] = 'login'
    gitlab_rails['smtp_enable_starttls_auto'] = true
    gitlab_rails['gitlab_email_from'] = '${SMTP_FROM}'"
fi

# -- Step 4: Build LDAP config --
LDAP_CONFIG=""
if [[ "${LDAP_ENABLED}" == "true" ]]; then
    LDAP_CONFIG="
    gitlab_rails['ldap_enabled'] = true
    gitlab_rails['ldap_servers'] = {
      'main' => {
        'label' => 'iGaming LDAP',
        'host' => '${LDAP_HOST}',
        'port' => ${LDAP_PORT},
        'uid' => 'sAMAccountName',
        'encryption' => 'simple_tls',
        'verify_certificates' => true,
        'bind_dn' => '${LDAP_BIND_DN}',
        'password' => '${LDAP_PASSWORD}',
        'active_directory' => true,
        'base' => '${LDAP_BASE}',
        'user_filter' => '',
        'attributes' => {
          'username' => ['uid', 'userid', 'sAMAccountName'],
          'email' => ['mail', 'email', 'userPrincipalName'],
          'name' => 'cn',
          'first_name' => 'givenName',
          'last_name' => 'sn'
        }
      }
    }"
fi

# -- Step 5: Build SAML config --
SAML_CONFIG=""
if [[ "${SAML_ENABLED}" == "true" ]]; then
    SAML_CERT_CONTENT=""
    if [[ -n "${SAML_IDP_CERT}" && -f "${SAML_IDP_CERT}" ]]; then
        SAML_CERT_CONTENT=$(cat "${SAML_IDP_CERT}")
    fi
    SAML_CONFIG="
    gitlab_rails['omniauth_enabled'] = true
    gitlab_rails['omniauth_allow_single_sign_on'] = ['saml']
    gitlab_rails['omniauth_block_auto_created_users'] = false
    gitlab_rails['omniauth_providers'] = [
      {
        name: 'saml',
        args: {
          assertion_consumer_service_url: '${EXTERNAL_URL}/users/auth/saml/callback',
          idp_cert: '${SAML_CERT_CONTENT}',
          idp_sso_target_url: '${SAML_IDP_SSO_URL}',
          issuer: '${EXTERNAL_URL}',
          name_identifier_format: 'urn:oasis:names:tc:SAML:2.0:nameid-format:persistent'
        },
        label: 'iGaming SSO'
      }
    ]"
fi

# -- Step 6: Build PostgreSQL config --
PG_CONFIG=""
if [[ "${PG_EXTERNAL}" == "true" ]]; then
    PG_CONFIG="
    postgresql['enable'] = false
    gitlab_rails['db_adapter'] = 'postgresql'
    gitlab_rails['db_encoding'] = 'unicode'
    gitlab_rails['db_host'] = '${PG_HOST}'
    gitlab_rails['db_port'] = ${PG_PORT}
    gitlab_rails['db_database'] = '${PG_DATABASE}'
    gitlab_rails['db_username'] = '${PG_USER}'
    gitlab_rails['db_password'] = '${PG_PASSWORD}'"
fi

# -- Step 7: Build backup config --
BACKUP_CONFIG="
    gitlab_rails['backup_keep_time'] = 604800"

if [[ -n "${BACKUP_S3_ENDPOINT}" ]]; then
    BACKUP_CONFIG="
    gitlab_rails['backup_keep_time'] = 604800
    gitlab_rails['backup_upload_connection'] = {
      'provider' => 'AWS',
      'aws_access_key_id' => '${BACKUP_S3_ACCESS_KEY}',
      'aws_secret_access_key' => '${BACKUP_S3_SECRET_KEY}',
      'endpoint' => '${BACKUP_S3_ENDPOINT}',
      'path_style' => true
    }
    gitlab_rails['backup_upload_remote_directory'] = '${BACKUP_S3_BUCKET}'"
fi

# -- Step 8: Generate gitlab.rb --
log_info "Step 8: Generating gitlab.rb configuration..."

cat > "${GITLAB_HOME}/config/gitlab.rb" <<RUBY
# =============================================================================
# GitLab CE Configuration for iGaming Platform
# Generated by gitlab-server-setup.sh on $(date -u +%Y-%m-%dT%H:%M:%SZ)
# =============================================================================

    external_url '${EXTERNAL_URL}'

    # -- SSL / TLS --
    ${NGINX_SSL_CONFIG}

    # -- NGINX hardening --
    nginx['ssl_protocols'] = 'TLSv1.2 TLSv1.3'
    nginx['ssl_ciphers'] = 'ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256:ECDHE-ECDSA-AES256-GCM-SHA384:ECDHE-RSA-AES256-GCM-SHA384'
    nginx['hsts_max_age'] = 63072000
    nginx['hsts_include_subdomains'] = true

    # -- Container Registry --
    registry_external_url 'https://registry.${DOMAIN}'
    gitlab_rails['registry_enabled'] = true

    # -- GitLab Pages --
    pages_external_url 'https://pages.${DOMAIN}'
    gitlab_pages['enable'] = true

    # -- CI/CD --
    gitlab_rails['gitlab_default_projects_features_builds'] = true
    gitlab_ci['builds_directory'] = '/var/opt/gitlab/builds'

    # -- Monitoring (Prometheus + Grafana) --
    prometheus['enable'] = true
    prometheus['listen_address'] = '0.0.0.0:9090'
    grafana['enable'] = true
    grafana['admin_password'] = 'changeme-on-first-login'

    # -- Audit logging (iGaming compliance) --
    gitlab_rails['audit_events_enabled'] = true

    # -- Rate limiting (DDoS protection) --
    gitlab_rails['rate_limiting_response_text'] = 'Rate limit exceeded. Try again later.'

    # -- Database --
    ${PG_CONFIG}

    # -- Email / SMTP --
    ${SMTP_CONFIG}

    # -- LDAP --
    ${LDAP_CONFIG}

    # -- SAML --
    ${SAML_CONFIG}

    # -- Backup --
    ${BACKUP_CONFIG}

    # -- Timezone (important for audit logs in regulated environments) --
    gitlab_rails['time_zone'] = 'UTC'
RUBY

chmod 600 "${GITLAB_HOME}/config/gitlab.rb"

# -- Step 9: Generate Docker Compose file --
log_info "Step 9: Generating Docker Compose file..."

COMPOSE_PG_SERVICE=""
COMPOSE_PG_VOLUME=""
COMPOSE_DEPENDS=""

if [[ "${PG_EXTERNAL}" == "false" ]]; then
    COMPOSE_PG_SERVICE="
  postgresql:
    image: postgres:16-alpine
    restart: unless-stopped
    environment:
      POSTGRES_DB: ${PG_DATABASE}
      POSTGRES_USER: ${PG_USER}
      POSTGRES_PASSWORD: \${GITLAB_PG_PASSWORD:-$(openssl rand -hex 16)}
    volumes:
      - pg_data:/var/lib/postgresql/data
    networks:
      - gitlab-net
    healthcheck:
      test: [\"CMD-SHELL\", \"pg_isready -U ${PG_USER}\"]
      interval: 10s
      timeout: 5s
      retries: 5"
    COMPOSE_PG_VOLUME="
  pg_data:
    driver: local"
    COMPOSE_DEPENDS="
    depends_on:
      postgresql:
        condition: service_healthy"
fi

cat > "${GITLAB_HOME}/docker-compose.yml" <<YAML
# =============================================================================
# GitLab CE Docker Compose - iGaming Platform
# =============================================================================
version: "3.8"

services:
  gitlab:
    image: gitlab/gitlab-ce:latest
    container_name: gitlab-ce
    restart: unless-stopped
    hostname: ${DOMAIN}
    ports:
      - "80:80"
      - "443:443"
      - "22:22"
      - "5050:5050"     # Container registry
      - "9090:9090"     # Prometheus
    volumes:
      - ${GITLAB_HOME}/config:/etc/gitlab
      - ${GITLAB_HOME}/logs:/var/log/gitlab
      - ${GITLAB_HOME}/data:/var/opt/gitlab
      - ${GITLAB_HOME}/backups:/var/opt/gitlab/backups
    shm_size: "256m"
    networks:
      - gitlab-net
    environment:
      GITLAB_OMNIBUS_CONFIG: "from_file('/etc/gitlab/gitlab.rb')"
    healthcheck:
      test: ["CMD", "curl", "-fsSL", "http://localhost/-/health"]
      interval: 30s
      timeout: 10s
      retries: 10
      start_period: 300s
    ${COMPOSE_DEPENDS}

  gitlab-runner:
    image: gitlab/gitlab-runner:latest
    container_name: gitlab-runner
    restart: unless-stopped
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock
      - gitlab_runner_config:/etc/gitlab-runner
    networks:
      - gitlab-net
    depends_on:
      gitlab:
        condition: service_healthy
${COMPOSE_PG_SERVICE}

networks:
  gitlab-net:
    driver: bridge

volumes:
  gitlab_runner_config:
    driver: local${COMPOSE_PG_VOLUME}
YAML

# -- Step 10: Setup automated backup cron --
log_info "Step 10: Setting up automated daily backups..."

cat > /etc/cron.d/gitlab-backup <<CRON
# GitLab automated backup - daily at 02:00 UTC
# Retains 7 days of backups (configured in gitlab.rb)
0 2 * * * root docker exec -t gitlab-ce gitlab-backup create SKIP=artifacts,registry STRATEGY=copy >> /var/log/gitlab-backup.log 2>&1

# Backup configuration files separately (secrets, gitlab.rb)
15 2 * * * root tar czf ${GITLAB_HOME}/backups/gitlab-config-\$(date +\%Y\%m\%d).tar.gz -C ${GITLAB_HOME} config >> /var/log/gitlab-backup.log 2>&1

# Clean old config backups (keep 30 days)
30 2 * * * root find ${GITLAB_HOME}/backups -name "gitlab-config-*.tar.gz" -mtime +30 -delete
CRON

chmod 644 /etc/cron.d/gitlab-backup

# -- Step 11: Start GitLab --
log_info "Step 11: Starting GitLab CE..."

cd "${GITLAB_HOME}"
docker compose up -d

log_info "GitLab is starting. Initial boot takes 3-5 minutes."
log_info "Monitor progress with: docker logs -f gitlab-ce"

cat <<SUMMARY

=============================================================================
  GitLab CE On-Premises Setup Complete
=============================================================================
  URL:            ${EXTERNAL_URL}
  Registry:       https://registry.${DOMAIN}
  Pages:          https://pages.${DOMAIN}
  Prometheus:     http://${DOMAIN}:9090
  Grafana:        ${EXTERNAL_URL}/-/grafana
  SSL:            ${SSL_MODE}
  Database:       $(if [[ "${PG_EXTERNAL}" == "true" ]]; then echo "External (${PG_HOST})"; else echo "Docker (embedded)"; fi)
  LDAP:           ${LDAP_ENABLED}
  SAML:           ${SAML_ENABLED}
  Backups:        Daily at 02:00 UTC → ${GITLAB_HOME}/backups/
  Config:         ${GITLAB_HOME}/config/gitlab.rb
  Compose:        ${GITLAB_HOME}/docker-compose.yml
=============================================================================
  First steps:
  1. Wait for GitLab to finish booting (3-5 min)
  2. Get root password: docker exec -t gitlab-ce grep 'Password:' /etc/gitlab/initial_root_password
  3. Change root password immediately
  4. Configure runner: Settings > CI/CD > Runners
  5. For LDAP/SAML: verify login at ${EXTERNAL_URL}/users/sign_in
  6. Review Grafana dashboards at ${EXTERNAL_URL}/-/grafana
=============================================================================
SUMMARY
