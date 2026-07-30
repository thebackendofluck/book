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
# install-prebuilt.sh — Install Coraza WAF from prebuilt packages (if available)
# =============================================================================
#
# Before compiling from source (build-coraza-nginx-module.sh), check whether
# a prebuilt package is available. This is faster and avoids build failures
# caused by missing toolchains.
#
# Checks in order:
#   1. libnginx-mod-security2 (Ubuntu/Debian apt) — ModSecurity v3 for nginx
#   2. nginx-plus-module-modsecurity (nginx official repo) — requires nginx+
#   3. ModSecurity PPA (unofficial but maintained)
#   4. Falls back to: build-coraza-nginx-module.sh
#
# Usage (run as root):
#   bash install-prebuilt.sh
#   bash install-prebuilt.sh --check-only   # only check; do not install
#
# =============================================================================
set -euo pipefail

CHECK_ONLY=false
for arg in "$@"; do
    case "${arg}" in
        --check-only) CHECK_ONLY=true ;;
        --help|-h)
            echo "Usage: $0 [--check-only]"
            exit 0
            ;;
    esac
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

log()     { echo "[$(date '+%H:%M:%S')] $*"; }
log_ok()  { echo "[$(date '+%H:%M:%S')] OK  $*"; }
log_err() { echo "[$(date '+%H:%M:%S')] ERR $*" >&2; }

# ---------------------------------------------------------------------------
# Detect the installed nginx version
# ---------------------------------------------------------------------------
detect_nginx_version() {
    if ! command -v nginx &>/dev/null; then
        log_err "nginx not found"
        return 1
    fi
    nginx -v 2>&1 | grep -oP '\d+\.\d+\.\d+'
}

# ---------------------------------------------------------------------------
# Check 1: libnginx-mod-security2 (Ubuntu/Debian apt)
# ---------------------------------------------------------------------------
# This is the most reliable prebuilt option. Maintained by the Ubuntu/Debian
# security team. Available on Ubuntu 22.04+ and Debian 11+.
# Installs: /usr/lib/nginx/modules/ngx_http_modsecurity_module.so
# ---------------------------------------------------------------------------
check_apt_modsecurity() {
    log "Checking apt for libnginx-mod-security2..."

    if ! command -v apt-get &>/dev/null; then
        log "apt not available — skipping"
        return 1
    fi

    # Check if already installed
    if dpkg -l libnginx-mod-security2 2>/dev/null | grep -q '^ii'; then
        log_ok "libnginx-mod-security2 is already installed"
        return 0
    fi

    # Check if available
    if apt-cache show libnginx-mod-security2 &>/dev/null; then
        local pkg_version
        pkg_version="$(apt-cache show libnginx-mod-security2 | grep '^Version:' | head -1 | awk '{print $2}')"
        log_ok "libnginx-mod-security2 available: ${pkg_version}"

        if [[ "${CHECK_ONLY}" == true ]]; then
            echo "  Install with: apt-get install -y libnginx-mod-security2"
            return 0
        fi

        log "Installing libnginx-mod-security2..."
        apt-get install -y libnginx-mod-security2

        # Verify the module .so was installed
        if [[ -f /usr/lib/nginx/modules/ngx_http_modsecurity_module.so ]]; then
            log_ok "Module installed: /usr/lib/nginx/modules/ngx_http_modsecurity_module.so"
            return 0
        else
            log_err "Module file not found after install"
            return 1
        fi
    else
        log "libnginx-mod-security2 not in apt cache"
        return 1
    fi
}

# ---------------------------------------------------------------------------
# Check 2: Ubuntu mainline nginx PPA (ppa:ondrej/nginx)
# ---------------------------------------------------------------------------
# The Ondrej PPA ships nginx with ModSecurity v3 module support.
# Requires adding the PPA, which also upgrades nginx.
# Use with caution on production — it replaces the system nginx.
# ---------------------------------------------------------------------------
check_ondrej_ppa() {
    log "Checking ppa:ondrej/nginx availability..."

    if ! command -v add-apt-repository &>/dev/null; then
        log "add-apt-repository not available — skipping PPA check"
        return 1
    fi

    log "NOTE: ppa:ondrej/nginx is available but will REPLACE the system nginx."
    log "      It bundles ModSecurity v3. Only use if you are OK with upgrading nginx."

    if [[ "${CHECK_ONLY}" == true ]]; then
        cat <<'EOF'
  To install via Ondrej PPA:
    add-apt-repository ppa:ondrej/nginx
    apt-get update
    apt-get install -y nginx libnginx-mod-security2 modsecurity-crs
EOF
    fi
    return 1  # Don't auto-install from PPA — require explicit --ondrej flag
}

# ---------------------------------------------------------------------------
# Check 3: ModSecurity apt search (any distro package)
# ---------------------------------------------------------------------------
check_apt_search() {
    log "Searching apt for any modsecurity/coraza packages..."

    if ! command -v apt-cache &>/dev/null; then
        return 1
    fi

    local found
    found="$(apt-cache search 'modsecurity\|coraza\|nginx.*waf\|waf.*nginx' 2>/dev/null || true)"

    if [[ -n "${found}" ]]; then
        log "Found apt packages:"
        echo "${found}" | while read -r line; do
            echo "  ${line}"
        done
    else
        log "No modsecurity/coraza packages found in apt"
    fi

    return 1  # Always fall through to source build
}

# ---------------------------------------------------------------------------
# Summary and fallback
# ---------------------------------------------------------------------------
main() {
    log "=== Prebuilt WAF package check ==="
    log "nginx version: $(detect_nginx_version 2>/dev/null || echo 'not found')"
    echo

    # Try each source in order
    if check_apt_modsecurity; then
        log "Prebuilt package installed successfully."
        log "Next step: ./deploy-coraza.sh --env staging --bare-metal"
        exit 0
    fi

    check_ondrej_ppa || true
    check_apt_search || true

    echo
    log "No prebuilt package available for this nginx version."
    log "Falling back to source build..."
    echo

    if [[ "${CHECK_ONLY}" == true ]]; then
        echo "Run source build with:"
        echo "  sudo bash ${SCRIPT_DIR}/build-coraza-nginx-module.sh"
    else
        log "Launching build-coraza-nginx-module.sh..."
        exec bash "${SCRIPT_DIR}/build-coraza-nginx-module.sh" "$@"
    fi
}

main "$@"
