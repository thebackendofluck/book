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
# AcmeToCasino - On-Premise WAF Installation Script
# Installs: nginx + ModSecurity v3 + OWASP CRS + MaxMind GeoLite2
#
# Supported platforms:
#   Ubuntu 22.04 (Jammy)   / Ubuntu 24.04 (Noble)
#   RHEL 8 / Rocky Linux 8
#   RHEL 9 / Rocky Linux 9 / AlmaLinux 9
#
# Usage:
#   sudo bash install_onprem_waf.sh [--maxmind-key <GEOIP_KEY>] [--dry-run]
#
# Options:
#   --maxmind-key KEY   MaxMind license key for GeoLite2 download
#   --dry-run           Print commands without executing them
#   --nginx-version VER Pin nginx version (default: latest stable)
#   --crs-version VER   Pin OWASP CRS version (default: 4.x latest)
#   --help              Show this help
# =============================================================================

set -euo pipefail

# ---------------------------------------------------------------------------
# Colour helpers
# ---------------------------------------------------------------------------
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'
info()  { echo -e "${BLUE}[INFO]${NC}  $*"; }
ok()    { echo -e "${GREEN}[OK]${NC}    $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*" >&2; }
die()   { error "$*"; exit 1; }

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------
DRY_RUN=false
MAXMIND_KEY=""
NGINX_VERSION=""   # empty = latest from repo
CRS_VERSION="4.7.0"
MODSEC_CONF_DIR="/etc/nginx/modsec"
SOAR_RULES_DIR="${MODSEC_CONF_DIR}/soar_rules"
GEOIP_DB_DIR="/etc/nginx/geoip"
LOG_DIR="/var/log/acmetocasino"
NGINX_CONF_DIR="/etc/nginx"
HEALTH_CHECK_PORT=9090

# OS-specific globals (set by detect_os)
OS_ID=""
OS_VER=""
PKG_MGR=""
UBUNTU_CODENAME_VAL=""   # Populated from /etc/os-release after detect_os runs

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------
while [[ $# -gt 0 ]]; do
    case "$1" in
        --maxmind-key)  MAXMIND_KEY="$2";   shift 2 ;;
        --dry-run)      DRY_RUN=true;       shift   ;;
        --nginx-version) NGINX_VERSION="$2"; shift 2 ;;
        --crs-version)  CRS_VERSION="$2";   shift 2 ;;
        --help)
            head -20 "$0" | grep "^#" | sed 's/^# \{0,2\}//'
            exit 0
            ;;
        *) die "Unknown argument: $1" ;;
    esac
done

# ---------------------------------------------------------------------------
# Dry-run wrapper
# ---------------------------------------------------------------------------
run() {
    if [[ "${DRY_RUN}" == "true" ]]; then
        echo "[DRY-RUN] $*"
    else
        "$@"
    fi
}

# ---------------------------------------------------------------------------
# OS detection
# ---------------------------------------------------------------------------
detect_os() {
    if [[ -f /etc/os-release ]]; then
        # shellcheck disable=SC1091
        source /etc/os-release
        OS_ID="${ID}"
        OS_VER="${VERSION_ID%%.*}"  # major version only
        # Capture the codename variable set by /etc/os-release
        # Ubuntu uses UBUNTU_CODENAME; Debian/others use VERSION_CODENAME
        UBUNTU_CODENAME_VAL="${UBUNTU_CODENAME:-${VERSION_CODENAME:-}}"
    else
        die "Cannot detect OS. /etc/os-release not found."
    fi

    case "${OS_ID}" in
        ubuntu)
            if [[ "${OS_VER}" != "22" && "${OS_VER}" != "24" ]]; then
                warn "Tested on Ubuntu 22/24. Proceeding on ${OS_ID} ${VERSION_ID}."
            fi
            PKG_MGR="apt"
            ;;
        rhel|centos|rocky|almalinux|ol)
            if [[ "${OS_VER}" != "8" && "${OS_VER}" != "9" ]]; then
                warn "Tested on RHEL/Rocky 8 and 9. Proceeding on ${OS_ID} ${VERSION_ID}."
            fi
            PKG_MGR="dnf"
            ;;
        *)
            die "Unsupported OS: ${OS_ID}. Supported: Ubuntu 22/24, RHEL/Rocky 8/9."
            ;;
    esac

    info "Detected OS: ${OS_ID} ${VERSION_ID:-?} (package manager: ${PKG_MGR})"
}

# ---------------------------------------------------------------------------
# Preflight checks
# ---------------------------------------------------------------------------
preflight() {
    info "Running preflight checks..."

    [[ $EUID -eq 0 ]] || die "This script must be run as root."

    for cmd in curl wget tar git make gcc; do
        if ! command -v "${cmd}" &>/dev/null; then
            warn "Required tool not found in PATH: ${cmd} (will be installed)"
        fi
    done

    # Check available disk space (need at least 2 GB)
    local available_kb
    available_kb=$(df /usr/local --output=avail | tail -1)
    if [[ "${available_kb}" -lt 2097152 ]]; then
        warn "Less than 2 GB available on /usr/local. Installation may fail."
    fi

    ok "Preflight checks passed"
}

# ---------------------------------------------------------------------------
# Package installation helpers
# ---------------------------------------------------------------------------
apt_install() {
    run apt-get install -y "$@"
}

dnf_install() {
    run dnf install -y "$@"
}

install_build_deps() {
    info "Installing build dependencies..."
    if [[ "${PKG_MGR}" == "apt" ]]; then
        run apt-get update -qq
        apt_install \
            build-essential git curl wget tar libpcre3-dev libssl-dev \
            zlib1g-dev libgd-dev libgeoip-dev libyajl-dev libxml2-dev \
            libmaxminddb-dev libcurl4-openssl-dev lua5.3 liblua5.3-dev \
            automake autoconf libtool pkg-config python3 python3-pip \
            logrotate
    else
        run dnf groupinstall -y "Development Tools"
        dnf_install \
            git curl wget tar pcre-devel openssl-devel \
            zlib-devel gd-devel GeoIP-devel yajl-devel libxml2-devel \
            libmaxminddb-devel libcurl-devel lua lua-devel \
            automake autoconf libtool pkgconfig python3 python3-pip \
            logrotate
    fi
    ok "Build dependencies installed"
}

# ---------------------------------------------------------------------------
# nginx installation
# ---------------------------------------------------------------------------
install_nginx() {
    info "Installing nginx..."
    if [[ "${PKG_MGR}" == "apt" ]]; then
        curl -fsSL https://nginx.org/keys/nginx_signing.key | \
            run gpg --dearmor -o /usr/share/keyrings/nginx-archive-keyring.gpg

        # UBUNTU_CODENAME_VAL was captured by detect_os from /etc/os-release
        local codename="${UBUNTU_CODENAME_VAL}"
        if [[ -z "${codename}" ]]; then
            codename=$(lsb_release -sc 2>/dev/null || echo "jammy")
        fi

        echo "deb [signed-by=/usr/share/keyrings/nginx-archive-keyring.gpg] \
http://nginx.org/packages/ubuntu ${codename} nginx" \
            | run tee /etc/apt/sources.list.d/nginx.list

        run apt-get update -qq
        if [[ -n "${NGINX_VERSION}" ]]; then
            apt_install "nginx=${NGINX_VERSION}"
        else
            apt_install nginx
        fi
    else
        cat > /etc/yum.repos.d/nginx.repo <<'REPOEOF'
[nginx-stable]
name=nginx stable repo
baseurl=http://nginx.org/packages/centos/$releasever/$basearch/
gpgcheck=1
enabled=1
gpgkey=https://nginx.org/keys/nginx_signing.key
module_hotfixes=true
REPOEOF
        if [[ -n "${NGINX_VERSION}" ]]; then
            dnf_install "nginx-${NGINX_VERSION}"
        else
            dnf_install nginx
        fi
    fi
    ok "nginx installed: $(nginx -v 2>&1 || true)"
}

# ---------------------------------------------------------------------------
# ModSecurity v3 (libmodsecurity) from source
# ---------------------------------------------------------------------------
install_modsecurity() {
    info "Building ModSecurity v3 from source..."
    local build_dir="/tmp/modsecurity_build"
    run mkdir -p "${build_dir}"

    # libmodsecurity
    if [[ ! -d "${build_dir}/ModSecurity" ]]; then
        run git clone --depth 1 --branch v3/master \
            https://github.com/SpiderLabs/ModSecurity \
            "${build_dir}/ModSecurity"
    fi

    pushd "${build_dir}/ModSecurity" > /dev/null
    run git submodule update --init --recursive
    run ./build.sh
    run ./configure --prefix=/usr/local
    run make -j"$(nproc)"
    run make install
    popd > /dev/null

    # nginx connector
    if [[ ! -d "${build_dir}/ModSecurity-nginx" ]]; then
        run git clone --depth 1 \
            https://github.com/SpiderLabs/ModSecurity-nginx \
            "${build_dir}/ModSecurity-nginx"
    fi

    # Determine nginx version for matching source
    local ng_version
    ng_version=$(nginx -v 2>&1 | grep -oP '[\d.]+')
    info "Compiling nginx connector for nginx ${ng_version}..."

    local nginx_src_dir="${build_dir}/nginx-${ng_version}"
    if [[ ! -d "${nginx_src_dir}" ]]; then
        run wget -q -O /tmp/nginx.tar.gz \
            "http://nginx.org/download/nginx-${ng_version}.tar.gz"
        run tar -xzf /tmp/nginx.tar.gz -C "${build_dir}"
    fi

    pushd "${nginx_src_dir}" > /dev/null
    run ./configure \
        --with-compat \
        --add-dynamic-module="${build_dir}/ModSecurity-nginx"
    run make -j"$(nproc)" modules
    run cp objs/ngx_http_modsecurity_module.so /usr/lib/nginx/modules/
    popd > /dev/null

    ok "ModSecurity v3 installed"
}

# ---------------------------------------------------------------------------
# OWASP CRS
# ---------------------------------------------------------------------------
install_owasp_crs() {
    info "Installing OWASP CRS ${CRS_VERSION}..."
    local crs_dir="${MODSEC_CONF_DIR}/crs"

    run mkdir -p "${crs_dir}"
    run wget -q -O /tmp/crs.tar.gz \
        "https://github.com/coreruleset/coreruleset/archive/v${CRS_VERSION}.tar.gz"
    run tar -xzf /tmp/crs.tar.gz -C /tmp/
    run cp -r "/tmp/coreruleset-${CRS_VERSION}/rules" "${crs_dir}/"
    run cp "/tmp/coreruleset-${CRS_VERSION}/crs-setup.conf.example" \
        "${MODSEC_CONF_DIR}/crs-setup.conf"

    # Set reasonable defaults for iGaming (paranoia level 2)
    if [[ "${DRY_RUN}" != "true" ]]; then
        sed -i 's/^#\(.*tx\.paranoia_level\)=1/\1=2/' "${MODSEC_CONF_DIR}/crs-setup.conf" || true
        sed -i 's/^#\(.*tx\.inbound_anomaly_score_threshold\)=5/\1=5/' \
            "${MODSEC_CONF_DIR}/crs-setup.conf" || true
        sed -i 's/^#\(.*tx\.outbound_anomaly_score_threshold\)=4/\1=4/' \
            "${MODSEC_CONF_DIR}/crs-setup.conf" || true
    fi

    ok "OWASP CRS ${CRS_VERSION} installed at ${crs_dir}"
}

# ---------------------------------------------------------------------------
# MaxMind GeoLite2
# ---------------------------------------------------------------------------
install_geoip() {
    if [[ -z "${MAXMIND_KEY}" ]]; then
        warn "No MaxMind key provided. Skipping GeoIP installation."
        warn "Provide one with --maxmind-key to enable country-based blocking."
        return
    fi

    info "Installing MaxMind GeoLite2 databases..."
    run mkdir -p "${GEOIP_DB_DIR}"

    for db in GeoLite2-City GeoLite2-Country GeoLite2-ASN; do
        run wget -q -O "/tmp/${db}.tar.gz" \
            "https://download.maxmind.com/app/geoip_download?edition_id=${db}&license_key=${MAXMIND_KEY}&suffix=tar.gz"
        run tar -xzf "/tmp/${db}.tar.gz" -C /tmp/
        local mmdb_file
        mmdb_file=$(find /tmp -name "${db}.mmdb" -newer "/tmp/${db}.tar.gz" | head -1)
        if [[ -n "${mmdb_file}" ]]; then
            run cp "${mmdb_file}" "${GEOIP_DB_DIR}/"
        fi
    done

    ok "GeoLite2 databases installed at ${GEOIP_DB_DIR}"
}

# ---------------------------------------------------------------------------
# Write configuration files
# ---------------------------------------------------------------------------
write_modsecurity_conf() {
    info "Writing ModSecurity configuration..."
    run mkdir -p "${MODSEC_CONF_DIR}" "${SOAR_RULES_DIR}"

    if [[ "${DRY_RUN}" == "true" ]]; then
        info "[DRY-RUN] Would write ${MODSEC_CONF_DIR}/modsecurity.conf"
        return
    fi

    # Base modsecurity.conf
    cat > "${MODSEC_CONF_DIR}/modsecurity.conf" <<'MODSECEOF'
# ModSecurity configuration for AcmeToCasino
# Managed by install_onprem_waf.sh

SecRuleEngine On
SecRequestBodyAccess On
SecResponseBodyAccess Off

SecRequestBodyLimit 104857600
SecRequestBodyNoFilesLimit 131072
SecRequestBodyInMemoryLimit 131072
SecRequestBodyLimitAction Reject

SecPcreMatchLimit 100000
SecPcreMatchLimitRecursion 100000

SecDebugLog /var/log/nginx/modsec_debug.log
SecDebugLogLevel 0

SecAuditEngine RelevantOnly
SecAuditLogRelevantStatus "^(?:5|4(?!04))"
SecAuditLog /var/log/nginx/modsec_audit.log
SecAuditLogFormat JSON
SecAuditLogParts ABCFHIJZ
SecAuditLogType Serial

SecTmpDir /tmp/
SecDataDir /tmp/modsec_data/

SecUnicodeMapFile unicode.mapping 20127
SecStatusEngine Off

# Admin IP whitelist - restrict backoffice access
SecRule REMOTE_ADDR "!@ipMatch 10.0.0.0/8,172.16.0.0/12,192.168.0.0/16" \
    "id:8999900,\
    phase:1,\
    chain,\
    log,auditlog,pass,\
    msg:'Non-RFC1918 admin access attempt'"
    SecRule REQUEST_URI "@rx (?i)/(admin|backoffice|cms)" \
        "t:none"
MODSECEOF

    # Main nginx modsecurity include file
    cat > "${MODSEC_CONF_DIR}/main.conf" <<MAINEOF
Include ${MODSEC_CONF_DIR}/modsecurity.conf
Include ${MODSEC_CONF_DIR}/crs-setup.conf
Include ${MODSEC_CONF_DIR}/crs/rules/*.conf
Include ${MODSEC_CONF_DIR}/soar_rules/soar_rules.conf
Include ${MODSEC_CONF_DIR}/soar_rules/soar_dynamic.conf
Include ${MODSEC_CONF_DIR}/soar_rules/virtual_patches.conf
MAINEOF

    # Create empty SOAR dynamic rule files (populated at runtime)
    for f in soar_dynamic.conf virtual_patches.conf; do
        if [[ ! -f "${SOAR_RULES_DIR}/${f}" ]]; then
            cat > "${SOAR_RULES_DIR}/${f}" <<EOF
# AcmeToCasino SOAR dynamic rules - managed by modsecurity_manager.py
# Do not edit manually
EOF
        fi
    done

    # Copy the SOAR baseline rules
    local script_dir
    script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    if [[ -f "${script_dir}/modsecurity/soar_rules.conf" ]]; then
        run cp "${script_dir}/modsecurity/soar_rules.conf" \
               "${SOAR_RULES_DIR}/soar_rules.conf"
    fi

    run mkdir -p /tmp/modsec_data
    ok "ModSecurity configuration written"
}

write_nginx_conf() {
    info "Writing nginx configuration..."
    if [[ "${DRY_RUN}" == "true" ]]; then
        info "[DRY-RUN] Would write nginx.conf and site configs"
        return
    fi

    # Load the ModSecurity dynamic module
    local modules_conf="${NGINX_CONF_DIR}/modules-enabled/modsecurity.conf"
    run mkdir -p "${NGINX_CONF_DIR}/modules-enabled"
    cat > "${modules_conf}" <<'MODEOF'
load_module modules/ngx_http_modsecurity_module.so;
MODEOF

    # Main nginx.conf additions (we write a snippet; the full nginx.conf is OS-packaged)
    local snip="${NGINX_CONF_DIR}/conf.d/acmetocasino-waf.conf"
    cat > "${snip}" <<NGINXEOF
# AcmeToCasino WAF virtual host configuration
# Installed by install_onprem_waf.sh

geo \$limit_key {
    default             \$binary_remote_addr;
    127.0.0.1           "";
    10.0.0.0/8          "";
    172.16.0.0/12       "";
    192.168.0.0/16      "";
}

limit_req_zone \$limit_key zone=login:10m rate=10r/s;
limit_req_zone \$limit_key zone=api:10m   rate=100r/s;
limit_req_zone \$limit_key zone=global:50m rate=200r/s;
limit_conn_zone \$binary_remote_addr zone=conn_limit:10m;

server {
    listen 80 default_server;
    server_name _;
    return 301 https://\$host\$request_uri;
}

server {
    listen 443 ssl http2;
    server_name casino.acmetocasino.com;

    ssl_certificate     /etc/ssl/acmetocasino/fullchain.pem;
    ssl_certificate_key /etc/ssl/acmetocasino/privkey.pem;
    ssl_protocols       TLSv1.2 TLSv1.3;
    ssl_ciphers         ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256:ECDHE-ECDSA-AES256-GCM-SHA384:ECDHE-RSA-AES256-GCM-SHA384;
    ssl_prefer_server_ciphers off;
    ssl_session_cache   shared:SSL:50m;
    ssl_session_timeout 1d;
    ssl_stapling        on;
    ssl_stapling_verify on;

    # Security headers
    add_header Strict-Transport-Security "max-age=63072000; includeSubDomains; preload" always;
    add_header X-Content-Type-Options    "nosniff" always;
    add_header X-Frame-Options           "DENY" always;
    add_header X-XSS-Protection          "1; mode=block" always;
    add_header Referrer-Policy           "strict-origin-when-cross-origin" always;
    add_header Content-Security-Policy   "default-src 'self'; script-src 'self' 'nonce-\${request_id}'; style-src 'self'; img-src 'self' data:; font-src 'self'" always;

    # ModSecurity WAF
    modsecurity on;
    modsecurity_rules_file ${MODSEC_CONF_DIR}/main.conf;

    # Connection limits
    limit_conn conn_limit 50;
    limit_req  zone=global burst=400 nodelay;

    # Hide server version
    server_tokens off;

    # Access and error logs
    access_log /var/log/nginx/acmetocasino_access.log combined;
    error_log  /var/log/nginx/acmetocasino_error.log warn;

    # API endpoints - tighter rate limiting
    location /api/ {
        limit_req zone=api burst=50 nodelay;
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host               \$host;
        proxy_set_header X-Real-IP          \$remote_addr;
        proxy_set_header X-Forwarded-For    \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto  \$scheme;
        proxy_read_timeout  60s;
        proxy_send_timeout  60s;
        client_max_body_size 10m;
    }

    # Auth / login - strictest rate limit
    location ~ ^/api/[^/]+/(auth/)?login {
        limit_req zone=login burst=5 nodelay;
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host               \$host;
        proxy_set_header X-Real-IP          \$remote_addr;
        proxy_set_header X-Forwarded-For    \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto  \$scheme;
        client_max_body_size 1m;
    }

    # Static assets - no WAF, high performance
    location /static/ {
        modsecurity off;
        alias /var/www/acmetocasino/static/;
        expires 30d;
        add_header Cache-Control "public, immutable";
    }

    # Health check endpoint - internal only
    location /healthz {
        modsecurity off;
        access_log off;
        allow 127.0.0.1;
        allow 10.0.0.0/8;
        allow 172.16.0.0/12;
        allow 192.168.0.0/16;
        deny  all;
        return 200 "OK\n";
        add_header Content-Type text/plain;
    }

    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host               \$host;
        proxy_set_header X-Real-IP          \$remote_addr;
        proxy_set_header X-Forwarded-For    \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto  \$scheme;
    }
}
NGINXEOF

    ok "nginx WAF configuration written"
}

write_health_check_service() {
    info "Setting up WAF health check service on port ${HEALTH_CHECK_PORT}..."
    if [[ "${DRY_RUN}" == "true" ]]; then
        info "[DRY-RUN] Would create systemd health check service"
        return
    fi

    cat > /usr/local/bin/waf_health_check.py <<'HCEOF'
#!/usr/bin/env python3
"""Minimal HTTP health check server for the AcmeToCasino WAF."""
import http.server
import json
import os
import subprocess
import sys
from datetime import datetime, timezone

PORT = int(os.environ.get("HEALTH_CHECK_PORT", "9090"))

class HealthHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):  # suppress default access log
        pass

    def do_GET(self):  # noqa: N802
        if self.path not in ("/health", "/healthz", "/"):
            self.send_response(404)
            self.end_headers()
            return

        nginx_ok = subprocess.run(
            ["/usr/sbin/nginx", "-t"],
            capture_output=True
        ).returncode == 0

        status = "healthy" if nginx_ok else "degraded"
        code = 200 if nginx_ok else 503

        body = json.dumps({
            "status": status,
            "nginx_config": "ok" if nginx_ok else "error",
            "ts": datetime.now(timezone.utc).isoformat(),
        }).encode()

        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

if __name__ == "__main__":
    server = http.server.HTTPServer(("127.0.0.1", PORT), HealthHandler)
    print(f"WAF health check listening on 127.0.0.1:{PORT}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        sys.exit(0)
HCEOF
    chmod +x /usr/local/bin/waf_health_check.py

    cat > /etc/systemd/system/waf-health-check.service <<SVCEOF
[Unit]
Description=AcmeToCasino WAF health check endpoint
After=nginx.service
Requires=nginx.service

[Service]
Type=simple
ExecStart=/usr/bin/python3 /usr/local/bin/waf_health_check.py
Environment=HEALTH_CHECK_PORT=${HEALTH_CHECK_PORT}
Restart=always
RestartSec=5
User=www-data
PrivateTmp=true
NoNewPrivileges=true
ProtectSystem=strict

[Install]
WantedBy=multi-user.target
SVCEOF

    run systemctl daemon-reload
    run systemctl enable waf-health-check.service
    ok "WAF health check service configured on port ${HEALTH_CHECK_PORT}"
}

# ---------------------------------------------------------------------------
# Log rotation
# ---------------------------------------------------------------------------
configure_log_rotation() {
    info "Configuring log rotation..."
    run mkdir -p "${LOG_DIR}"

    if [[ "${DRY_RUN}" == "true" ]]; then
        info "[DRY-RUN] Would write /etc/logrotate.d/acmetocasino-waf"
        return
    fi

    cat > /etc/logrotate.d/acmetocasino-waf <<'LOGEOF'
/var/log/nginx/acmetocasino_*.log
/var/log/nginx/modsec_audit.log
/var/log/acmetocasino/*.jsonl
{
    daily
    rotate 90
    compress
    delaycompress
    missingok
    notifempty
    create 0640 www-data adm
    sharedscripts
    postrotate
        if [ -f /var/run/nginx.pid ]; then
            kill -USR1 "$(cat /var/run/nginx.pid)"
        fi
    endscript
}
LOGEOF

    ok "Log rotation configured (90-day retention, daily, compressed)"
}

# ---------------------------------------------------------------------------
# Enable and start services
# ---------------------------------------------------------------------------
start_services() {
    info "Enabling and starting services..."
    run systemctl enable nginx
    run systemctl restart nginx
    run systemctl start waf-health-check.service
    ok "Services started"
}

# ---------------------------------------------------------------------------
# Post-installation verification
# ---------------------------------------------------------------------------
verify_installation() {
    info "Verifying installation..."
    local errors=0

    if ! nginx -t 2>/dev/null; then
        error "nginx configuration test failed"
        (( errors++ )) || true
    else
        ok "nginx configuration valid"
    fi

    if [[ -f /usr/local/lib/libmodsecurity.so ]]; then
        ok "libmodsecurity installed"
    else
        warn "libmodsecurity not found at /usr/local/lib/libmodsecurity.so"
        (( errors++ )) || true
    fi

    if [[ -d "${MODSEC_CONF_DIR}/crs/rules" ]]; then
        local rule_count
        rule_count=$(find "${MODSEC_CONF_DIR}/crs/rules" -name "*.conf" | wc -l)
        ok "OWASP CRS loaded (${rule_count} rule files)"
    else
        warn "OWASP CRS rules directory not found"
        (( errors++ )) || true
    fi

    if [[ "${errors}" -eq 0 ]]; then
        ok "All verification checks passed"
    else
        warn "${errors} verification check(s) failed. Review errors above."
    fi
}

# ---------------------------------------------------------------------------
# Print post-install summary
# ---------------------------------------------------------------------------
print_summary() {
    echo ""
    echo -e "${GREEN}====================================================="
    echo "  AcmeToCasino WAF Installation Complete"
    echo -e "=====================================================${NC}"
    echo ""
    echo "  nginx WAF:          $(nginx -v 2>&1 | tr -d '\n')"
    echo "  ModSecurity:        /usr/local/lib/libmodsecurity.so"
    echo "  OWASP CRS:          ${MODSEC_CONF_DIR}/crs/rules/"
    echo "  SOAR rules dir:     ${SOAR_RULES_DIR}/"
    echo "  GeoIP databases:    ${GEOIP_DB_DIR}/"
    echo "  WAF audit log:      /var/log/nginx/modsec_audit.log"
    echo "  Health check:       http://127.0.0.1:${HEALTH_CHECK_PORT}/health"
    echo ""
    echo "  Next steps:"
    echo "  1. Copy your TLS certificate to /etc/ssl/acmetocasino/"
    echo "  2. Update proxy_pass in ${NGINX_CONF_DIR}/conf.d/acmetocasino-waf.conf"
    echo "  3. Run: python3 modsecurity_manager.py status"
    echo "  4. Set CRS paranoia: python3 modsecurity_manager.py crs-paranoia --level 2"
    echo ""
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
main() {
    echo -e "${BLUE}"
    echo "  AcmeToCasino On-Premise WAF Installer"
    echo "  Platform: $(uname -rs)"
    echo -e "${NC}"

    detect_os
    preflight
    install_build_deps
    install_nginx
    install_modsecurity
    install_owasp_crs
    install_geoip
    write_modsecurity_conf
    write_nginx_conf
    write_health_check_service
    configure_log_rotation
    start_services
    verify_installation
    print_summary
}

main
