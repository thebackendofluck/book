#!/usr/bin/env bash
# Companion code for "The Backend of Luck" - Chapter 24, Security and Compliance.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# =============================================================================
# build-coraza-nginx-module.sh — Build coraza-nginx (or ModSecurity v3) as a
# dynamic nginx module for nginx 1.24.x on Ubuntu 24.04 / 22.04
# =============================================================================
#
# Architecture after installation:
#   Internet → nginx (:80/:443)
#               └─► ngx_http_coraza_module.so  (WAF, inline)
#                     └─► upstream handlers (PHP-FPM, Node, static files)
#
# No Caddy. No Docker sidecar. No iptables redirects. WAF runs inside nginx.
#
# Usage (run as root on the target host):
#   bash build-coraza-nginx-module.sh            # auto-detect nginx version
#   bash build-coraza-nginx-module.sh --modsec   # force ModSecurity v3 fallback
#   bash build-coraza-nginx-module.sh --dry-run  # show steps without executing
#
# What this script does:
#   1. Detects the installed nginx version
#   2. Attempts to build coraza-nginx as a dynamic module
#   3. If coraza-nginx build fails, falls back to ModSecurity v3 (ngx_http_modsecurity_module)
#   4. Installs the .so to /usr/lib/nginx/modules/ (Ubuntu default)
#   5. Copies coraza.conf and CRS rules to /etc/nginx/coraza/
#   6. Prepends load_module to /etc/nginx/nginx.conf
#   7. Runs nginx -t and reloads
#
# Requirements:
#   - nginx 1.24.x installed from Ubuntu apt (--with-compat already set)
#   - Root or sudo access
#   - Build tools: gcc, g++, make, cmake, git, curl, pkg-config
#   - Go 1.21+ (for coraza-nginx build only)
#
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUILD_DIR="/tmp/coraza-nginx-build"
NGINX_MODULES_DIR="/usr/lib/nginx/modules"
CORAZA_CONF_DIR="/etc/nginx/coraza"
LOG_FILE="/tmp/build-coraza-nginx-$(date '+%Y%m%d-%H%M%S').log"

# Flags
FORCE_MODSEC=false
DRY_RUN=false

# Parse arguments
for arg in "$@"; do
    case "${arg}" in
        --modsec)   FORCE_MODSEC=true ;;
        --dry-run)  DRY_RUN=true ;;
        --help|-h)
            echo "Usage: $0 [--modsec] [--dry-run]"
            echo "  --modsec    Force ModSecurity v3 build instead of coraza-nginx"
            echo "  --dry-run   Show build steps without executing"
            exit 0
            ;;
    esac
done

# ---------------------------------------------------------------------------
# Utility functions
# ---------------------------------------------------------------------------
log()     { echo "[$(date '+%H:%M:%S')] $*" | tee -a "${LOG_FILE}"; }
log_err() { echo "[$(date '+%H:%M:%S')] ERROR: $*" | tee -a "${LOG_FILE}" >&2; }
# shellcheck disable=SC2294  # eval is intentional: run() accepts multi-token shell commands as a single string
run()     { if [[ "${DRY_RUN}" == true ]]; then echo "  [DRY-RUN] $*"; else eval "$@"; fi; }

# ---------------------------------------------------------------------------
# Step 0: Detect nginx version and verify --with-compat
# ---------------------------------------------------------------------------
detect_nginx() {
    if ! command -v nginx &>/dev/null; then
        log_err "nginx not found. Install with: apt-get install nginx"
        exit 1
    fi

    NGINX_VERSION="$(nginx -v 2>&1 | grep -oP '\d+\.\d+\.\d+')"
    log "Detected nginx version: ${NGINX_VERSION}"

    # Verify --with-compat is set (required for dynamic modules)
    if ! nginx -V 2>&1 | grep -q '\-\-with-compat'; then
        log_err "nginx was not built with --with-compat. Dynamic modules are not supported."
        log_err "The Ubuntu apt nginx package (nginx/1.24.0) includes --with-compat — are you on a custom build?"
        exit 1
    fi
    log "nginx --with-compat: confirmed"

    # Determine Ubuntu nginx source package version suffix
    NGINX_DEB_VERSION="$(dpkg -l nginx 2>/dev/null | grep '^ii' | awk '{print $3}' || echo "${NGINX_VERSION}-1")"
    log "nginx deb version: ${NGINX_DEB_VERSION}"
}

# ---------------------------------------------------------------------------
# Step 1: Install build dependencies
# ---------------------------------------------------------------------------
install_build_deps_coraza() {
    log "Installing build dependencies for coraza-nginx..."
    run "apt-get update -qq"
    run "apt-get install -y --no-install-recommends \
        build-essential \
        libpcre3-dev \
        libssl-dev \
        zlib1g-dev \
        git \
        curl \
        wget \
        pkg-config \
        cmake \
        golang-go \
        libyajl-dev \
        libgeoip-dev"
}

install_build_deps_modsec() {
    log "Installing build dependencies for ModSecurity v3..."
    run "apt-get update -qq"
    run "apt-get install -y --no-install-recommends \
        build-essential \
        libpcre3-dev \
        libssl-dev \
        zlib1g-dev \
        git \
        wget \
        pkg-config \
        libcurl4-openssl-dev \
        libxml2-dev \
        libyajl-dev \
        libgeoip-dev \
        libpcre2-dev \
        automake \
        libtool \
        autoconf \
        libfuzzy-dev \
        ssdeep"
}

# ---------------------------------------------------------------------------
# Step 2a: Build coraza-nginx dynamic module
# ---------------------------------------------------------------------------
build_coraza_nginx() {
    log "Building coraza-nginx dynamic module for nginx ${NGINX_VERSION}..."
    run "mkdir -p ${BUILD_DIR}"
    run "cd ${BUILD_DIR}"

    # Download matching nginx source
    log "Downloading nginx ${NGINX_VERSION} source..."
    run "wget -q http://nginx.org/download/nginx-${NGINX_VERSION}.tar.gz"
    run "tar xzf nginx-${NGINX_VERSION}.tar.gz"

    # Clone coraza-nginx
    log "Cloning coraza-nginx..."
    run "git clone --depth=1 https://github.com/corazawaf/coraza-nginx.git"

    # Build the dynamic module
    # IMPORTANT: configure flags must match the installed nginx exactly.
    # We pass --with-compat plus the same compiler flags nginx was built with.
    log "Compiling coraza-nginx module..."
    run "cd ${BUILD_DIR}/nginx-${NGINX_VERSION}"
    # shellcheck disable=SC2016
    run './configure \
        --with-compat \
        --with-cc-opt="-g -O2 -fstack-protector-strong -Wformat -Werror=format-security -fPIC -D_FORTIFY_SOURCE=2" \
        --with-ld-opt="-Wl,-Bsymbolic-functions -Wl,-z,relro -fPIC" \
        --add-dynamic-module=../coraza-nginx 2>&1 | tee -a '"${LOG_FILE}"

    run "make modules 2>&1 | tee -a ${LOG_FILE}"
    run "cp objs/ngx_http_coraza_module.so ${BUILD_DIR}/ngx_http_coraza_module.so"

    log "coraza-nginx module built successfully."
}

# ---------------------------------------------------------------------------
# Step 2b: Build ModSecurity v3 + nginx connector (fallback)
# ---------------------------------------------------------------------------
build_modsecurity_nginx() {
    log "Building ModSecurity v3 (libmodsecurity) + nginx connector..."
    run "mkdir -p ${BUILD_DIR}"
    run "cd ${BUILD_DIR}"

    # Clone libmodsecurity v3
    log "Cloning ModSecurity v3..."
    run "git clone --depth=1 --branch v3/master https://github.com/SpiderLabs/ModSecurity.git"
    run "cd ${BUILD_DIR}/ModSecurity"
    run "git submodule update --init --recursive"
    run "./build.sh 2>&1 | tee -a ${LOG_FILE}"
    run "./configure 2>&1 | tee -a ${LOG_FILE}"
    run "make -j\$(nproc) 2>&1 | tee -a ${LOG_FILE}"
    run "make install 2>&1 | tee -a ${LOG_FILE}"

    # Clone ModSecurity-nginx connector
    log "Cloning ModSecurity-nginx connector..."
    run "cd ${BUILD_DIR}"
    run "git clone --depth=1 https://github.com/SpiderLabs/ModSecurity-nginx.git"

    # Download matching nginx source
    log "Downloading nginx ${NGINX_VERSION} source..."
    run "wget -q http://nginx.org/download/nginx-${NGINX_VERSION}.tar.gz"
    run "tar xzf nginx-${NGINX_VERSION}.tar.gz"

    # Build the dynamic module
    log "Compiling ModSecurity-nginx module..."
    run "cd ${BUILD_DIR}/nginx-${NGINX_VERSION}"
    # shellcheck disable=SC2016
    run './configure \
        --with-compat \
        --with-cc-opt="-g -O2 -fstack-protector-strong -Wformat -Werror=format-security -fPIC -D_FORTIFY_SOURCE=2" \
        --with-ld-opt="-Wl,-Bsymbolic-functions -Wl,-z,relro -fPIC" \
        --add-dynamic-module=../ModSecurity-nginx 2>&1 | tee -a '"${LOG_FILE}"

    run "make modules 2>&1 | tee -a ${LOG_FILE}"
    run "cp objs/ngx_http_modsecurity_module.so ${BUILD_DIR}/ngx_http_modsecurity_module.so"

    log "ModSecurity v3 nginx module built successfully."
    USING_MODSEC=true
}

# ---------------------------------------------------------------------------
# Step 3: Install the module
# ---------------------------------------------------------------------------
install_module() {
    log "Installing module to ${NGINX_MODULES_DIR}..."
    run "mkdir -p ${NGINX_MODULES_DIR}"

    if [[ "${USING_MODSEC:-false}" == true ]]; then
        run "cp ${BUILD_DIR}/ngx_http_modsecurity_module.so ${NGINX_MODULES_DIR}/"
        run "chmod 644 ${NGINX_MODULES_DIR}/ngx_http_modsecurity_module.so"
        MODULE_FILENAME="ngx_http_modsecurity_module.so"
    else
        run "cp ${BUILD_DIR}/ngx_http_coraza_module.so ${NGINX_MODULES_DIR}/"
        run "chmod 644 ${NGINX_MODULES_DIR}/ngx_http_coraza_module.so"
        MODULE_FILENAME="ngx_http_coraza_module.so"
    fi

    log "Module installed: ${NGINX_MODULES_DIR}/${MODULE_FILENAME}"
}

# ---------------------------------------------------------------------------
# Step 4: Install coraza.conf and CRS rules
# ---------------------------------------------------------------------------
install_waf_config() {
    log "Installing WAF config and CRS rules to ${CORAZA_CONF_DIR}..."
    run "mkdir -p ${CORAZA_CONF_DIR}/crs/rules"
    run "mkdir -p /var/log/coraza"

    # Copy coraza.conf
    if [[ -f "${SCRIPT_DIR}/coraza.conf" ]]; then
        run "cp ${SCRIPT_DIR}/coraza.conf ${CORAZA_CONF_DIR}/coraza.conf"
        log "Copied coraza.conf"
    else
        log_err "coraza.conf not found at ${SCRIPT_DIR}/coraza.conf"
        exit 1
    fi

    # Copy CRS setup config
    if [[ -f "${SCRIPT_DIR}/crs-setup.conf" ]]; then
        run "cp ${SCRIPT_DIR}/crs-setup.conf ${CORAZA_CONF_DIR}/crs/crs-setup.conf"
        log "Copied crs-setup.conf"
    else
        log_err "crs-setup.conf not found at ${SCRIPT_DIR}/crs-setup.conf"
        exit 1
    fi

    # Copy CRS rules
    local rule_count
    rule_count="$(find "${SCRIPT_DIR}/crs-rules" -name "*.conf" 2>/dev/null | wc -l | tr -d ' ')"
    if [[ "${rule_count}" -lt 10 ]]; then
        log_err "CRS rules not found in ${SCRIPT_DIR}/crs-rules/ (found ${rule_count} files)"
        log_err "Run: ./download-crs-rules.sh first"
        exit 1
    fi
    run "cp -r ${SCRIPT_DIR}/crs-rules/. ${CORAZA_CONF_DIR}/crs/rules/"
    log "Copied ${rule_count} CRS rule files"

    # Set permissions
    run "chown -R www-data:www-data /var/log/coraza"
    run "chmod 755 /var/log/coraza"
}

# ---------------------------------------------------------------------------
# Step 5: Patch nginx.conf to load the module
# ---------------------------------------------------------------------------
patch_nginx_conf() {
    local nginx_conf="/etc/nginx/nginx.conf"
    log "Patching ${nginx_conf} to load the WAF module..."

    # Determine the load_module directive
    local load_directive
    if [[ "${USING_MODSEC:-false}" == true ]]; then
        load_directive="load_module modules/ngx_http_modsecurity_module.so;"
    else
        load_directive="load_module modules/ngx_http_coraza_module.so;"
    fi

    # Check if already loaded
    if grep -q "${load_directive}" "${nginx_conf}" 2>/dev/null; then
        log "load_module already present in nginx.conf — skipping"
        return 0
    fi

    # Backup nginx.conf before modifying
    run "cp ${nginx_conf} ${nginx_conf}.waf-backup-$(date '+%Y%m%d-%H%M%S')"

    # Prepend load_module at the top of nginx.conf (before the first non-comment line)
    # We insert after the last existing load_module block, or at the top if none exists.
    if grep -q '^load_module' "${nginx_conf}" 2>/dev/null; then
        # Append after the last load_module line
        run "sed -i '/^load_module.*\\.so;/a\\${load_directive}' ${nginx_conf}"
    else
        # Prepend at the very top
        run "sed -i '1s|^|${load_directive}\n|' ${nginx_conf}"
    fi

    log "Added: ${load_directive}"
}

# ---------------------------------------------------------------------------
# Step 6: Install nginx-coraza.conf snippet (includes/sites-available)
# ---------------------------------------------------------------------------
install_nginx_snippet() {
    local snippet_dir="/etc/nginx/conf.d"
    local snippet_file="${snippet_dir}/coraza-waf.conf"

    log "Installing nginx WAF snippet to ${snippet_file}..."

    if [[ "${USING_MODSEC:-false}" == true ]]; then
        # ModSecurity v3 directives
        cat > "${snippet_file}" <<'NGINX_MODSEC'
# ModSecurity v3 WAF — loaded globally, can be overridden per-location
# Generated by build-coraza-nginx-module.sh

modsecurity on;
modsecurity_rules_file /etc/nginx/coraza/coraza.conf;

# Per-location override example (add inside a server{} block):
#   location /internal-only/ {
#       modsecurity off;
#   }
NGINX_MODSEC
    else
        # coraza-nginx directives
        cat > "${snippet_file}" <<'NGINX_CORAZA'
# Coraza WAF — loaded globally, can be overridden per-location
# Generated by build-coraza-nginx-module.sh

coraza on;
coraza_rules_file /etc/nginx/coraza/coraza.conf;

# Per-location override example (add inside a server{} block):
#   location /internal-only/ {
#       coraza_rules 'SecRuleEngine Off';
#   }
NGINX_CORAZA
    fi

    log "Installed WAF snippet: ${snippet_file}"
}

# ---------------------------------------------------------------------------
# Step 7: Validate and reload nginx
# ---------------------------------------------------------------------------
reload_nginx() {
    log "Testing nginx config..."
    if nginx -t 2>&1 | tee -a "${LOG_FILE}"; then
        log "nginx config test: OK"
    else
        log_err "nginx config test FAILED — restoring backup"
        local backup
        backup="$(find /etc/nginx -maxdepth 1 -name 'nginx.conf.waf-backup-*' -printf '%T@ %p\n' 2>/dev/null | sort -rn | head -1 | cut -d' ' -f2-)"
        if [[ -n "${backup}" ]]; then
            cp "${backup}" /etc/nginx/nginx.conf
            log "Restored: ${backup}"
        fi
        exit 1
    fi

    log "Reloading nginx..."
    run "systemctl reload nginx"
    log "nginx reloaded successfully"
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
main() {
    log "=== Build Coraza/ModSecurity nginx dynamic module ==="
    log "Build dir  : ${BUILD_DIR}"
    log "Log file   : ${LOG_FILE}"
    log "Force modsec: ${FORCE_MODSEC}"
    echo

    # Must run as root (or with sudo)
    if [[ "${DRY_RUN}" == false ]] && [[ "$(id -u)" -ne 0 ]]; then
        log_err "This script must run as root. Use: sudo bash $0 $*"
        exit 1
    fi

    detect_nginx
    USING_MODSEC=${FORCE_MODSEC}

    if [[ "${USING_MODSEC}" == false ]]; then
        log "Attempting coraza-nginx build..."
        install_build_deps_coraza

        if build_coraza_nginx 2>&1 | tee -a "${LOG_FILE}"; then
            log "coraza-nginx build succeeded."
        else
            log "coraza-nginx build failed — falling back to ModSecurity v3"
            USING_MODSEC=true
        fi
    fi

    if [[ "${USING_MODSEC}" == true ]]; then
        log "Building ModSecurity v3 + nginx connector..."
        install_build_deps_modsec
        build_modsecurity_nginx
    fi

    install_module
    install_waf_config
    patch_nginx_conf
    install_nginx_snippet
    reload_nginx

    # Summary
    local waf_type
    if [[ "${USING_MODSEC:-false}" == true ]]; then
        waf_type="ModSecurity v3 (ngx_http_modsecurity_module)"
    else
        waf_type="Coraza (ngx_http_coraza_module)"
    fi

    cat <<EOF

================================================================
  WAF Module Installation Complete
================================================================
  nginx version    : ${NGINX_VERSION}
  WAF              : ${waf_type}
  Module           : ${NGINX_MODULES_DIR}/${MODULE_FILENAME}
  WAF config       : ${CORAZA_CONF_DIR}/coraza.conf
  CRS rules        : ${CORAZA_CONF_DIR}/crs/rules/
  nginx snippet    : /etc/nginx/conf.d/coraza-waf.conf
  Audit log        : /var/log/coraza/audit.log
  Build log        : ${LOG_FILE}

  Verify:
    nginx -t
    systemctl status nginx
    curl -s -o /dev/null -w '%{http_code}' http://localhost/

  Test WAF:
    ./test-coraza.sh localhost 80

  To temporarily disable WAF (per-location in a site config):
    modsecurity off;    # ModSecurity
    coraza_rules 'SecRuleEngine Off';  # coraza-nginx

  To uninstall:
    sed -i '/load_module.*modsecurity_module\|coraza_module/d' /etc/nginx/nginx.conf
    rm -f ${NGINX_MODULES_DIR}/${MODULE_FILENAME}
    nginx -t && systemctl reload nginx
================================================================
EOF
}

main "$@"
