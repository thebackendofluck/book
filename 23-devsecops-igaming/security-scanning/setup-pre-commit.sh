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
# Pre-Commit Setup Script for iGaming Platform
# =============================================================================
# Chapter 23: DevSecOps - Security Scanning
#
# CRITICAL: This must be run on every developer machine before first commit.
#
# WHY: iGaming platforms are regulated financial systems. Every developer
# must have security scanning active locally to prevent secrets, vulnerable
# code, and non-compliant infrastructure from ever reaching the repository.
# Catching issues pre-commit is 100x cheaper than catching them in production
# where regulatory fines start at EUR 50,000.
#
# USAGE:
#   ./setup-pre-commit.sh              # Full setup
#   ./setup-pre-commit.sh --check      # Verify existing installation
#   ./setup-pre-commit.sh --help       # Show this help
#
# PREREQUISITES:
#   - Python 3.10+ (for pre-commit, bandit, ruff)
#   - Git 2.28+ (for core.hooksPath support)
#   - Docker (for hadolint)
#
# =============================================================================
set -euo pipefail

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || echo ".")"
GITLEAKS_VERSION="8.18.4"
GITLEAKS_CONFIG="${SCRIPT_DIR}/gitleaks-config.toml"
PRE_COMMIT_CONFIG="${SCRIPT_DIR}/.pre-commit-config.yaml"
LOG_FILE="${REPO_ROOT}/.security-setup.log"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# ---------------------------------------------------------------------------
# Helper Functions
# ---------------------------------------------------------------------------
log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[OK]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1" >&2
}

show_banner() {
    echo ""
    echo "============================================================================="
    echo "  iGaming Platform - Security Toolchain Setup"
    echo "  CRITICAL: This must be run on every developer machine before first commit"
    echo "============================================================================="
    echo ""
    echo "  This script installs and configures:"
    echo "    - pre-commit framework with security hooks"
    echo "    - gitleaks for secret detection"
    echo "    - detect-secrets baseline"
    echo "    - gitleaks ignore list for known false positives"
    echo ""
    echo "============================================================================="
    echo ""
}

show_help() {
    echo "Usage: $(basename "$0") [OPTIONS]"
    echo ""
    echo "Options:"
    echo "  --check     Verify existing installation without making changes"
    echo "  --force     Force reinstallation of all tools"
    echo "  --skip-scan Skip initial full-repository scan"
    echo "  --help      Show this help message"
    echo ""
    echo "Environment Variables:"
    echo "  GITLEAKS_VERSION    Gitleaks version to install (default: ${GITLEAKS_VERSION})"
    echo "  SKIP_DOCKER_HOOKS   Set to 'true' to skip Docker-dependent hooks"
    echo ""
    echo "Examples:"
    echo "  ./setup-pre-commit.sh              # Full setup with initial scan"
    echo "  ./setup-pre-commit.sh --check      # Verify installation"
    echo "  ./setup-pre-commit.sh --skip-scan  # Setup without scanning history"
    echo ""
}

# ---------------------------------------------------------------------------
# Dependency Checks
# ---------------------------------------------------------------------------
check_dependencies() {
    log_info "Checking required dependencies..."
    local missing=0

    # Python
    if command -v python3 &>/dev/null; then
        local py_version
        py_version=$(python3 --version 2>&1 | awk '{print $2}')
        log_success "Python ${py_version} found"
    else
        log_error "Python 3 is required but not installed"
        missing=1
    fi

    # Git
    if command -v git &>/dev/null; then
        local git_version
        git_version=$(git --version | awk '{print $3}')
        log_success "Git ${git_version} found"
    else
        log_error "Git is required but not installed"
        missing=1
    fi

    # Verify we're in a git repository
    if ! git rev-parse --is-inside-work-tree &>/dev/null; then
        log_error "Not inside a git repository. Run this from the project root."
        missing=1
    fi

    # pip
    if command -v pip3 &>/dev/null || python3 -m pip --version &>/dev/null 2>&1; then
        log_success "pip found"
    else
        log_error "pip is required but not installed"
        missing=1
    fi

    # Docker (optional but recommended)
    if command -v docker &>/dev/null; then
        log_success "Docker found (enables hadolint hook)"
    else
        log_warn "Docker not found. Hadolint hook will be skipped."
        log_warn "Install Docker for Dockerfile security linting."
    fi

    if [[ ${missing} -ne 0 ]]; then
        log_error "Missing required dependencies. Install them and re-run."
        exit 1
    fi

    echo ""
}

# ---------------------------------------------------------------------------
# Install pre-commit
# WHY: pre-commit is the framework that orchestrates all security hooks.
# Without it, developers must remember to run each tool manually -- and
# they won't.
# ---------------------------------------------------------------------------
install_pre_commit() {
    log_info "Installing pre-commit framework..."

    if command -v pre-commit &>/dev/null; then
        local current_version
        current_version=$(pre-commit --version | awk '{print $2}')
        log_success "pre-commit ${current_version} already installed"
    else
        python3 -m pip install --user pre-commit 2>>"${LOG_FILE}"
        log_success "pre-commit installed"
    fi
}

# ---------------------------------------------------------------------------
# Install gitleaks
# WHY: gitleaks is the primary secret scanner. It runs both as a pre-commit
# hook (scanning diffs) and standalone (scanning full history). Critical
# for iGaming because database credentials, payment keys, and JWT secrets
# are the most common breach vectors.
# ---------------------------------------------------------------------------
install_gitleaks() {
    log_info "Installing gitleaks ${GITLEAKS_VERSION}..."

    if command -v gitleaks &>/dev/null; then
        local current_version
        current_version=$(gitleaks version 2>&1 || echo "unknown")
        log_success "gitleaks ${current_version} already installed"
        return 0
    fi

    local os arch binary_name download_url
    os="$(uname -s | tr '[:upper:]' '[:lower:]')"
    arch="$(uname -m)"

    case "${arch}" in
        x86_64)  arch="x64" ;;
        aarch64) arch="arm64" ;;
        arm64)   arch="arm64" ;;
        *)
            log_error "Unsupported architecture: ${arch}"
            return 1
            ;;
    esac

    binary_name="gitleaks_${GITLEAKS_VERSION}_${os}_${arch}.tar.gz"
    download_url="https://github.com/gitleaks/gitleaks/releases/download/v${GITLEAKS_VERSION}/${binary_name}"

    local tmp_dir
    tmp_dir=$(mktemp -d)
    trap 'rm -rf "${tmp_dir}"' RETURN

    log_info "Downloading from ${download_url}..."
    if curl -sL "${download_url}" -o "${tmp_dir}/${binary_name}"; then
        tar -xzf "${tmp_dir}/${binary_name}" -C "${tmp_dir}"
        local install_dir="${HOME}/.local/bin"
        mkdir -p "${install_dir}"
        mv "${tmp_dir}/gitleaks" "${install_dir}/gitleaks"
        log_success "gitleaks installed to ${install_dir}/gitleaks"

        if ! echo "${PATH}" | grep -q "${install_dir}"; then
            log_warn "Add ${install_dir} to your PATH:"
            log_warn "  export PATH=\"${install_dir}:\${PATH}\""
        fi
    else
        log_error "Failed to download gitleaks. Install manually:"
        log_error "  https://github.com/gitleaks/gitleaks/releases"
        return 1
    fi
}

# ---------------------------------------------------------------------------
# Configure Git Hooks
# WHY: Git hooks enforce security checks automatically. Without them,
# security scanning is optional -- and optional security doesn't work.
# ---------------------------------------------------------------------------
configure_hooks() {
    log_info "Installing pre-commit git hooks..."

    # Copy the pre-commit config to repo root if not already there
    if [[ ! -f "${REPO_ROOT}/.pre-commit-config.yaml" ]]; then
        cp "${PRE_COMMIT_CONFIG}" "${REPO_ROOT}/.pre-commit-config.yaml"
        log_success "Copied .pre-commit-config.yaml to repository root"
    else
        log_info ".pre-commit-config.yaml already exists in repository root"
    fi

    # Copy gitleaks config to repo root
    if [[ ! -f "${REPO_ROOT}/.gitleaks.toml" ]]; then
        cp "${GITLEAKS_CONFIG}" "${REPO_ROOT}/.gitleaks.toml"
        log_success "Copied gitleaks config to repository root"
    else
        log_info ".gitleaks.toml already exists in repository root"
    fi

    # Install the hooks
    (cd "${REPO_ROOT}" && pre-commit install --install-hooks 2>>"${LOG_FILE}")
    log_success "Pre-commit hooks installed"

    # Install commit-msg hook for conventional commits
    (cd "${REPO_ROOT}" && pre-commit install --hook-type commit-msg 2>>"${LOG_FILE}")
    log_success "Commit-msg hook installed (conventional commits)"

    # Install pre-push hook
    if [[ -f "${SCRIPT_DIR}/pre-push-checks.sh" ]]; then
        local hooks_dir="${REPO_ROOT}/.git/hooks"
        cp "${SCRIPT_DIR}/pre-push-checks.sh" "${hooks_dir}/pre-push"
        chmod +x "${hooks_dir}/pre-push"
        log_success "Pre-push security hook installed"
    fi
}

# ---------------------------------------------------------------------------
# Configure .gitleaksignore
# WHY: False positives train developers to ignore security warnings. A
# maintained ignore file ensures real findings get attention while known
# safe patterns (test fixtures, documentation examples) are suppressed.
# ---------------------------------------------------------------------------
configure_gitleaks_ignore() {
    log_info "Configuring .gitleaksignore for known false positives..."

    local ignore_file="${REPO_ROOT}/.gitleaksignore"

    if [[ -f "${ignore_file}" ]]; then
        log_info ".gitleaksignore already exists, preserving existing entries"
        return 0
    fi

    cat > "${ignore_file}" << 'IGNORE_EOF'
# =============================================================================
# Gitleaks Ignore File - Known False Positives
# =============================================================================
# Each line is a gitleaks finding fingerprint to suppress.
#
# PROCESS: When a finding is a confirmed false positive:
#   1. Verify with a second team member
#   2. Add the fingerprint here with a comment explaining WHY it's safe
#   3. Get PR approval from security team
#
# DO NOT add real secrets here. If you leaked a real secret:
#   1. Rotate the credential IMMEDIATELY
#   2. Report to security team
#   3. Do NOT just add it to this ignore file
# =============================================================================

# Example: test fixture database URL used only in CI
# abc123def456...

# Example: documentation sample showing connection string format
# fed654cba321...
IGNORE_EOF

    log_success "Created .gitleaksignore template"
}

# ---------------------------------------------------------------------------
# Initialize detect-secrets baseline
# WHY: detect-secrets uses a baseline file to track known secrets and
# reduce noise. The initial baseline captures existing (presumably safe)
# high-entropy strings so only NEW potential secrets trigger alerts.
# ---------------------------------------------------------------------------
initialize_detect_secrets_baseline() {
    log_info "Initializing detect-secrets baseline..."

    if [[ -f "${REPO_ROOT}/.secrets.baseline" ]]; then
        log_info ".secrets.baseline already exists"
        return 0
    fi

    if command -v detect-secrets &>/dev/null; then
        (cd "${REPO_ROOT}" && detect-secrets scan \
            --exclude-files 'tests/fixtures/.*' \
            --exclude-files 'docs/examples/.*' \
            --exclude-files '\.git/.*' \
            > .secrets.baseline 2>>"${LOG_FILE}")
        log_success "detect-secrets baseline created"
    else
        log_warn "detect-secrets not installed. Install with: pip install detect-secrets"
        log_warn "Skipping baseline creation"
    fi
}

# ---------------------------------------------------------------------------
# Run Initial Scan
# WHY: Before a developer starts committing, we need to know the current
# state of the repository. This catches any secrets that may have been
# committed before security scanning was set up.
# ---------------------------------------------------------------------------
run_initial_scan() {
    log_info "Running initial security scan on existing codebase..."
    echo ""

    local findings=0

    # Gitleaks scan on current state
    log_info "Scanning current files with gitleaks..."
    if command -v gitleaks &>/dev/null; then
        if gitleaks detect \
            --config "${REPO_ROOT}/.gitleaks.toml" \
            --source "${REPO_ROOT}" \
            --report-path "${REPO_ROOT}/.security-scan-report.json" \
            --report-format json \
            --no-git 2>>"${LOG_FILE}"; then
            log_success "No secrets found in current files"
        else
            log_error "SECRETS FOUND in current files! Review .security-scan-report.json"
            findings=1
        fi
    else
        log_warn "gitleaks not available, skipping file scan"
    fi

    # Pre-commit run on all files
    log_info "Running all pre-commit hooks on existing files..."
    if (cd "${REPO_ROOT}" && pre-commit run --all-files 2>>"${LOG_FILE}"); then
        log_success "All pre-commit hooks passed"
    else
        log_warn "Some pre-commit hooks reported findings. Review output above."
        findings=1
    fi

    echo ""
    if [[ ${findings} -ne 0 ]]; then
        log_warn "============================================================"
        log_warn "  SECURITY FINDINGS DETECTED"
        log_warn "  Review findings before continuing development."
        log_warn "  Report: ${REPO_ROOT}/.security-scan-report.json"
        log_warn "============================================================"
    else
        log_success "============================================================"
        log_success "  INITIAL SCAN CLEAN - No security issues found"
        log_success "============================================================"
    fi

    return ${findings}
}

# ---------------------------------------------------------------------------
# Verify Installation
# ---------------------------------------------------------------------------
verify_installation() {
    log_info "Verifying security toolchain installation..."
    echo ""

    local all_ok=0

    # Check pre-commit
    if command -v pre-commit &>/dev/null; then
        log_success "pre-commit: $(pre-commit --version)"
    else
        log_error "pre-commit: NOT INSTALLED"
        all_ok=1
    fi

    # Check gitleaks
    if command -v gitleaks &>/dev/null; then
        log_success "gitleaks: $(gitleaks version 2>&1)"
    else
        log_error "gitleaks: NOT INSTALLED"
        all_ok=1
    fi

    # Check hooks installed
    if [[ -f "${REPO_ROOT}/.git/hooks/pre-commit" ]]; then
        log_success "pre-commit hook: INSTALLED"
    else
        log_error "pre-commit hook: NOT INSTALLED"
        all_ok=1
    fi

    # Check config files
    if [[ -f "${REPO_ROOT}/.pre-commit-config.yaml" ]]; then
        log_success ".pre-commit-config.yaml: PRESENT"
    else
        log_error ".pre-commit-config.yaml: MISSING"
        all_ok=1
    fi

    if [[ -f "${REPO_ROOT}/.gitleaks.toml" ]]; then
        log_success ".gitleaks.toml: PRESENT"
    else
        log_error ".gitleaks.toml: MISSING"
        all_ok=1
    fi

    echo ""
    if [[ ${all_ok} -eq 0 ]]; then
        log_success "All security tools properly configured!"
    else
        log_error "Some tools are missing. Run setup again without --check."
    fi

    return ${all_ok}
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
main() {
    local check_only=false
    local force=false
    local skip_scan=false

    while [[ $# -gt 0 ]]; do
        case "$1" in
            --check)
                check_only=true
                shift
                ;;
            --force)
                force=true  # Reserved for future forced reinstallation
                export force
                shift
                ;;
            --skip-scan)
                skip_scan=true
                shift
                ;;
            --help|-h)
                show_help
                exit 0
                ;;
            *)
                log_error "Unknown option: $1"
                show_help
                exit 1
                ;;
        esac
    done

    show_banner

    if [[ "${check_only}" == "true" ]]; then
        verify_installation
        exit $?
    fi

    check_dependencies
    install_pre_commit
    install_gitleaks
    configure_hooks
    configure_gitleaks_ignore
    initialize_detect_secrets_baseline

    if [[ "${skip_scan}" != "true" ]]; then
        run_initial_scan || true
    fi

    echo ""
    log_success "============================================================"
    log_success "  Security toolchain setup complete!"
    log_success "  All commits will now be scanned for security issues."
    log_success "============================================================"
    echo ""
    log_info "Next steps:"
    echo "  1. Review .pre-commit-config.yaml for project-specific tuning"
    echo "  2. Run: pre-commit run --all-files (to verify all hooks work)"
    echo "  3. Commit the config files: git add .pre-commit-config.yaml .gitleaks.toml .gitleaksignore"
    echo ""
}

main "$@"
