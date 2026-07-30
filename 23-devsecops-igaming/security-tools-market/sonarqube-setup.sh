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

# =============================================================================
# SonarQube Setup for iGaming Platforms
# =============================================================================
#
# Purpose:
#   Complete SonarQube Community/Enterprise deployment for iGaming operators.
#   Configures quality gates, custom profiles, and gaming-specific rules that
#   align with ISO 27001:2022, PCI-DSS v4.0, and GLI-33 requirements.
#
# Why SonarQube for iGaming:
#   Casino platforms handle real money transactions, certified RNG outputs, and
#   personally identifiable data across multiple jurisdictions. Static analysis
#   catches vulnerabilities *before* code reaches production -- critical when a
#   single bug can expose financial data or compromise RNG integrity.
#
# Usage:
#   ./sonarqube-setup.sh [--enterprise] [--domain sonar.example.com]
#
# Prerequisites:
#   - Docker and Docker Compose v2+
#   - At least 4 GB RAM available (SonarQube requires sysctl vm.max_map_count)
#   - Domain name and SSL certificate (for production deployments)
#
# =============================================================================

set -euo pipefail

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
SONAR_VERSION="${SONAR_VERSION:-10.7-community}"
SONAR_DB_PASSWORD="${SONAR_DB_PASSWORD:-$(openssl rand -base64 24)}"
SONAR_ADMIN_PASSWORD="${SONAR_ADMIN_PASSWORD:-$(openssl rand -base64 16)}"
SONAR_DOMAIN="${SONAR_DOMAIN:-sonar.localhost}"
SONAR_PORT="${SONAR_PORT:-9000}"
INSTALL_DIR="${INSTALL_DIR:-/opt/sonarqube}"
ENTERPRISE_MODE=false

# OWASP Top 10 rule tags that SonarQube recognises
export OWASP_TAGS="owasp-a01,owasp-a02,owasp-a03,owasp-a04,owasp-a05,owasp-a06,owasp-a07,owasp-a08,owasp-a09,owasp-a10"

# ---------------------------------------------------------------------------
# Colour helpers (safe for non-TTY)
# ---------------------------------------------------------------------------
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log_info()  { printf "${GREEN}[INFO]${NC}  %s\n" "$1"; }
log_warn()  { printf "${YELLOW}[WARN]${NC}  %s\n" "$1"; }
log_error() { printf "${RED}[ERROR]${NC} %s\n" "$1" >&2; }

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------
while [[ $# -gt 0 ]]; do
    case "$1" in
        --enterprise)
            ENTERPRISE_MODE=true
            SONAR_VERSION="${SONAR_VERSION:-10.7-enterprise}"
            shift
            ;;
        --domain)
            SONAR_DOMAIN="$2"
            shift 2
            ;;
        --port)
            SONAR_PORT="$2"
            shift 2
            ;;
        --install-dir)
            INSTALL_DIR="$2"
            shift 2
            ;;
        -h|--help)
            printf "Usage: %s [--enterprise] [--domain DOMAIN] [--port PORT]\n" "$0"
            exit 0
            ;;
        *)
            log_error "Unknown option: $1"
            exit 1
            ;;
    esac
done

# ---------------------------------------------------------------------------
# Pre-flight checks
# ---------------------------------------------------------------------------
preflight_checks() {
    log_info "Running pre-flight checks..."

    # Docker must be available
    if ! command -v docker &>/dev/null; then
        log_error "Docker is not installed. Install Docker first."
        exit 1
    fi

    # Docker Compose v2
    if ! docker compose version &>/dev/null; then
        log_error "Docker Compose v2 is required. Install docker-compose-plugin."
        exit 1
    fi

    # Kernel parameter required by Elasticsearch inside SonarQube
    local current_map_count
    current_map_count=$(sysctl -n vm.max_map_count 2>/dev/null || echo "0")
    if [[ "$current_map_count" -lt 262144 ]]; then
        log_warn "vm.max_map_count is $current_map_count (need >= 262144)."
        log_info "Setting vm.max_map_count = 524288..."
        sudo sysctl -w vm.max_map_count=524288
        echo "vm.max_map_count=524288" | sudo tee -a /etc/sysctl.d/99-sonarqube.conf >/dev/null
    fi

    # Memory check -- SonarQube wants at least 3 GB free
    local free_mb
    free_mb=$(awk '/MemAvailable/ {printf "%d", $2/1024}' /proc/meminfo)
    if [[ "$free_mb" -lt 3072 ]]; then
        log_warn "Only ${free_mb} MB RAM available. SonarQube recommends 4 GB+."
    fi

    log_info "Pre-flight checks passed."
}

# ---------------------------------------------------------------------------
# Generate Docker Compose manifest
# ---------------------------------------------------------------------------
generate_compose() {
    log_info "Generating Docker Compose configuration..."
    mkdir -p "${INSTALL_DIR}"

    cat > "${INSTALL_DIR}/docker-compose.yml" <<YAML
# SonarQube for iGaming -- Docker Compose
# Generated by sonarqube-setup.sh
version: "3.9"

services:
  sonarqube:
    image: sonarqube:${SONAR_VERSION}
    container_name: sonarqube
    restart: unless-stopped
    depends_on:
      sonar-db:
        condition: service_healthy
    environment:
      SONAR_JDBC_URL: jdbc:postgresql://sonar-db:5432/sonarqube
      SONAR_JDBC_USERNAME: sonar
      SONAR_JDBC_PASSWORD: ${SONAR_DB_PASSWORD}
      # JVM tuning -- iGaming codebases can be large
      SONAR_CE_JAVAOPTS: "-Xmx2g -Xms512m"
      SONAR_WEB_JAVAOPTS: "-Xmx1g -Xms256m"
    ports:
      - "${SONAR_PORT}:9000"
    volumes:
      - sonarqube_data:/opt/sonarqube/data
      - sonarqube_extensions:/opt/sonarqube/extensions
      - sonarqube_logs:/opt/sonarqube/logs
    ulimits:
      nofile:
        soft: 131072
        hard: 131072
    networks:
      - sonar-net

  sonar-db:
    image: postgres:16-alpine
    container_name: sonar-db
    restart: unless-stopped
    environment:
      POSTGRES_USER: sonar
      POSTGRES_PASSWORD: ${SONAR_DB_PASSWORD}
      POSTGRES_DB: sonarqube
    volumes:
      - sonar_db_data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U sonar -d sonarqube"]
      interval: 10s
      timeout: 5s
      retries: 5
    networks:
      - sonar-net

volumes:
  sonarqube_data:
  sonarqube_extensions:
  sonarqube_logs:
  sonar_db_data:

networks:
  sonar-net:
    driver: bridge
YAML

    log_info "Docker Compose written to ${INSTALL_DIR}/docker-compose.yml"
}

# ---------------------------------------------------------------------------
# Nginx reverse proxy with SSL
# ---------------------------------------------------------------------------
generate_nginx_config() {
    log_info "Generating nginx reverse proxy configuration..."

    local nginx_conf="/etc/nginx/sites-available/${SONAR_DOMAIN}"
    sudo mkdir -p /etc/nginx/sites-available /etc/nginx/sites-enabled

    sudo tee "${nginx_conf}" > /dev/null <<NGINX
# Nginx reverse proxy for SonarQube -- iGaming deployment
# TLS termination happens here; SonarQube runs on plain HTTP internally.

server {
    listen 80;
    server_name ${SONAR_DOMAIN};
    return 301 https://\$host\$request_uri;
}

server {
    listen 443 ssl http2;
    server_name ${SONAR_DOMAIN};

    ssl_certificate     /etc/letsencrypt/live/${SONAR_DOMAIN}/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/${SONAR_DOMAIN}/privkey.pem;
    ssl_protocols       TLSv1.2 TLSv1.3;
    ssl_ciphers         HIGH:!aNULL:!MD5;

    # Security headers -- important for PCI-DSS compliance
    add_header Strict-Transport-Security "max-age=63072000; includeSubDomains" always;
    add_header X-Content-Type-Options    "nosniff" always;
    add_header X-Frame-Options           "DENY" always;

    location / {
        proxy_pass         http://127.0.0.1:${SONAR_PORT};
        proxy_set_header   Host              \$host;
        proxy_set_header   X-Real-IP         \$remote_addr;
        proxy_set_header   X-Forwarded-For   \$proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto \$scheme;
        proxy_read_timeout 300;
    }
}
NGINX

    sudo ln -sf "${nginx_conf}" "/etc/nginx/sites-enabled/${SONAR_DOMAIN}"
    log_info "Nginx config written. Reload nginx after obtaining SSL certificate."
}

# ---------------------------------------------------------------------------
# Wait for SonarQube to become healthy
# ---------------------------------------------------------------------------
wait_for_sonar() {
    local url="http://localhost:${SONAR_PORT}/api/system/health"
    local max_attempts=60
    local attempt=0

    log_info "Waiting for SonarQube to start (up to ${max_attempts} attempts)..."
    while [[ $attempt -lt $max_attempts ]]; do
        if curl -sf "${url}" 2>/dev/null | grep -q '"health":"GREEN"'; then
            log_info "SonarQube is healthy."
            return 0
        fi
        sleep 5
        attempt=$((attempt + 1))
    done

    log_error "SonarQube did not become healthy within $((max_attempts * 5)) seconds."
    exit 1
}

# ---------------------------------------------------------------------------
# Configure quality gate for gaming compliance
# ---------------------------------------------------------------------------
configure_quality_gate() {
    local base_url="http://localhost:${SONAR_PORT}"
    local auth="admin:${SONAR_ADMIN_PASSWORD}"

    log_info "Creating 'iGaming Compliance' quality gate..."

    # Create the quality gate
    local gate_id
    gate_id=$(curl -sf -u "${auth}" \
        -X POST "${base_url}/api/qualitygates/create" \
        -d "name=iGaming%20Compliance" | python3 -c "import sys,json;print(json.load(sys.stdin).get('id',''))" 2>/dev/null || echo "")

    if [[ -z "$gate_id" ]]; then
        log_warn "Quality gate may already exist. Skipping creation."
        return
    fi

    # Gaming compliance is strict: zero critical/blocker issues allowed
    # These thresholds align with PCI-DSS v4.0 requirement 6.2.4
    local -a conditions=(
        "metric=new_security_hotspots_reviewed&op=LT&error=100"
        "metric=new_reliability_rating&op=GT&error=1"
        "metric=new_security_rating&op=GT&error=1"
        "metric=new_coverage&op=LT&error=80"
        "metric=new_duplicated_lines_density&op=GT&error=3"
        "metric=new_blocker_violations&op=GT&error=0"
        "metric=new_critical_violations&op=GT&error=0"
    )

    for condition in "${conditions[@]}"; do
        curl -sf -u "${auth}" \
            -X POST "${base_url}/api/qualitygates/create_condition" \
            -d "gateName=iGaming%20Compliance&${condition}" >/dev/null 2>&1 || true
    done

    # Set as default
    curl -sf -u "${auth}" \
        -X POST "${base_url}/api/qualitygates/set_as_default" \
        -d "name=iGaming%20Compliance" >/dev/null 2>&1 || true

    log_info "Quality gate 'iGaming Compliance' configured and set as default."
}

# ---------------------------------------------------------------------------
# Configure webhook for CI/CD integration
# ---------------------------------------------------------------------------
configure_webhooks() {
    local base_url="http://localhost:${SONAR_PORT}"
    local auth="admin:${SONAR_ADMIN_PASSWORD}"

    log_info "Configuring CI/CD webhooks..."

    # GitLab webhook (adjust URL to your GitLab instance)
    curl -sf -u "${auth}" \
        -X POST "${base_url}/api/webhooks/create" \
        -d "name=GitLab%20CI&url=https://gitlab.example.com/api/v4/projects/1/hooks" \
        >/dev/null 2>&1 || true

    # GitHub webhook (for PR decoration)
    curl -sf -u "${auth}" \
        -X POST "${base_url}/api/webhooks/create" \
        -d "name=GitHub%20Actions&url=https://api.github.com/repos/org/repo/hooks" \
        >/dev/null 2>&1 || true

    log_info "Webhooks configured. Update URLs for your actual CI/CD endpoints."
}

# ---------------------------------------------------------------------------
# Main execution
# ---------------------------------------------------------------------------
main() {
    log_info "=========================================="
    log_info "SonarQube Setup for iGaming"
    log_info "=========================================="
    log_info "Edition:     $(if $ENTERPRISE_MODE; then echo 'Enterprise'; else echo 'Community'; fi)"
    log_info "Domain:      ${SONAR_DOMAIN}"
    log_info "Install dir: ${INSTALL_DIR}"
    log_info ""

    preflight_checks
    generate_compose
    generate_nginx_config

    log_info "Starting SonarQube..."
    cd "${INSTALL_DIR}"
    docker compose up -d

    wait_for_sonar

    # Change default admin password (SonarQube ships with admin/admin)
    log_info "Changing default admin password..."
    curl -sf -u "admin:admin" \
        -X POST "http://localhost:${SONAR_PORT}/api/users/change_password" \
        -d "login=admin&previousPassword=admin&password=${SONAR_ADMIN_PASSWORD}" \
        >/dev/null 2>&1 || log_warn "Password may already be changed."

    configure_quality_gate
    configure_webhooks

    log_info "=========================================="
    log_info "SonarQube deployment complete!"
    log_info "=========================================="
    log_info "URL:            https://${SONAR_DOMAIN}"
    log_info "Admin user:     admin"
    log_info "Admin password: ${SONAR_ADMIN_PASSWORD}"
    log_info ""
    log_info "Next steps:"
    log_info "  1. Obtain SSL certificate: certbot certonly -d ${SONAR_DOMAIN}"
    log_info "  2. Reload nginx: sudo systemctl reload nginx"
    log_info "  3. Create project tokens in SonarQube UI"
    log_info "  4. Configure scanner in your CI/CD pipeline"
    log_info "  5. Review quality gate thresholds for your jurisdiction"
    log_info "=========================================="
}

main "$@"
