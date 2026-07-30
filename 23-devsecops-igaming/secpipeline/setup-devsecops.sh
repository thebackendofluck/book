#!/bin/bash
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
# DevSecOps Standard Setup Script
# Universal Security Pipeline Installation for iGaming Platforms
# =============================================================================
# Sets up a complete DevSecOps security pipeline with tiered scanning
# levels (minimal, standard, comprehensive, maximum) appropriate for
# iGaming environments requiring PCI DSS and multi-jurisdictional compliance.
# =============================================================================

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
MAGENTA='\033[0;35m'
NC='\033[0m'

# Configuration
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SECURITY_LEVEL="${1:-standard}"
INTERACTIVE="${2:-true}"

print_status() { echo -e "${BLUE}[INFO]${NC} $1"; }
print_success() { echo -e "${GREEN}[OK]${NC} $1"; }
print_warning() { echo -e "${YELLOW}[WARN]${NC} $1"; }
print_error() { echo -e "${RED}[ERROR]${NC} $1"; }
print_header() { echo -e "\n${MAGENTA}=== $1 ===${NC}"; }

check_dependencies() {
    print_header "Checking Dependencies"

    local missing_deps=()

    command -v git >/dev/null 2>&1 || missing_deps+=("git")
    command -v python3 >/dev/null 2>&1 || missing_deps+=("python3")
    command -v pip3 >/dev/null 2>&1 || missing_deps+=("pip3")
    command -v docker >/dev/null 2>&1 || print_warning "Docker not found - container scanning will be limited"

    if [ ${#missing_deps[@]} -ne 0 ]; then
        print_error "Missing dependencies: ${missing_deps[*]}"
        echo "Please install the missing dependencies and run this script again."
        exit 1
    fi

    print_success "All required dependencies found"
}

detect_project_type() {
    print_header "Detecting Project Type"

    local project_type="unknown"
    local languages=()

    if find . -name "*.py" -type f | grep -q .; then
        languages+=("python")
        project_type="python"
    fi

    if find . \( -name "*.js" -o -name "*.ts" \) -type f | grep -q .; then
        languages+=("javascript")
        [ "$project_type" = "unknown" ] && project_type="javascript"
    fi

    if find . -name "*.go" -type f | grep -q .; then
        languages+=("go")
        [ "$project_type" = "unknown" ] && project_type="go"
    fi

    if find . -name "Dockerfile*" -type f | grep -q .; then
        project_type="container"
    fi

    if find . -name "*.tf" -type f | grep -q .; then
        project_type="terraform"
    fi

    print_status "Detected project type: $project_type"
    print_status "Languages found: ${languages[*]}"

    echo "$project_type" > .project-type
    echo "${languages[*]}" > .languages-detected
}

install_pre_commit() {
    print_header "Installing Pre-commit Security Hooks"

    if [ ! -f ".pre-commit-config.yaml" ]; then
        print_status "Installing pre-commit configuration..."
        cp "$SCRIPT_DIR/pre-commit-config.yaml" ./.pre-commit-config.yaml
        print_success "Pre-commit configuration installed"
    else
        print_warning "Pre-commit configuration already exists"
    fi

    if ! command -v pre-commit >/dev/null 2>&1; then
        print_status "Installing pre-commit..."
        pip3 install pre-commit
        print_success "Pre-commit installed"
    fi

    print_status "Installing pre-commit hooks..."
    pre-commit install
    pre-commit install --hook-type commit-msg
    print_success "Pre-commit hooks installed"

    print_status "Running initial security scan..."
    pre-commit run --all-files || print_warning "Pre-commit found issues (exit $?) - review output above"
    print_success "Initial security scan completed"
}

install_security_tools() {
    print_header "Installing Security Tools"

    # Python security tools
    if [ -f ".project-type" ] && grep -q "python" .project-type; then
        print_status "Installing Python security tools..."
        pip3 install --user \
            bandit[toml] \
            safety \
            semgrep \
            detect-secrets \
            ruff
        print_success "Python security tools installed"
    fi

    # Container security tools
    if command -v docker >/dev/null 2>&1; then
        print_status "Installing container security tools..."
        if ! command -v trivy >/dev/null 2>&1; then
            curl -sfL https://raw.githubusercontent.com/aquasecurity/trivy/main/contrib/install.sh | sh -s -- -b /usr/local/bin
            print_success "Trivy installed"
        fi

        if ! command -v hadolint >/dev/null 2>&1; then
            wget -O /tmp/hadolint https://github.com/hadolint/hadolint/releases/latest/download/hadolint-Linux-x86_64
            chmod +x /tmp/hadolint
            sudo mv /tmp/hadolint /usr/local/bin/
            print_success "Hadolint installed"
        fi
    fi

    # Infrastructure security tools
    if [ -f ".project-type" ] && grep -q "terraform" .project-type; then
        print_status "Installing Terraform security tools..."
        pip3 install --user checkov
        print_success "Checkov installed"
    fi

    # Secret detection tools
    print_status "Installing secret detection tools..."
    if ! command -v gitleaks >/dev/null 2>&1; then
        wget -q -O - https://github.com/gitleaks/gitleaks/releases/download/v8.18.2/gitleaks_8.18.2_linux_x64.tar.gz | tar xz
        sudo mv gitleaks /usr/local/bin/
        print_success "Gitleaks installed"
    fi

    if ! command -v trufflehog >/dev/null 2>&1; then
        curl -sSfL https://raw.githubusercontent.com/trufflesecurity/trufflehog/main/scripts/install.sh | sh -s -- -b /usr/local/bin
        print_success "TruffleHog installed"
    fi

    # Shell analysis
    if ! command -v shellcheck >/dev/null 2>&1; then
        sudo apt-get update && sudo apt-get install -y shellcheck
        print_success "ShellCheck installed"
    fi
}

setup_github_workflows() {
    print_header "Setting up GitHub Workflows"

    mkdir -p .github/workflows

    if [ ! -f ".github/workflows/security.yml" ]; then
        print_status "Installing security workflow..."
        cp "$SCRIPT_DIR/security-pipeline.yml" .github/workflows/security.yml
        print_success "Security workflow installed"
    else
        print_warning "Security workflow already exists"
    fi
}

run_security_scan() {
    print_header "Running Security Scan"

    if command -v bandit >/dev/null 2>&1; then
        bandit -r . -f json -o bandit-report.json || print_warning "Bandit found issues"
        print_success "Bandit scan completed"
    fi

    if command -v safety >/dev/null 2>&1; then
        safety check --json --output safety-report.json || print_warning "Safety found issues"
        print_success "Safety scan completed"
    fi

    if command -v trivy >/dev/null 2>&1; then
        trivy fs --format json --output trivy-fs-report.json . || print_warning "Trivy found issues"
        print_success "Trivy filesystem scan completed"
    fi

    if command -v checkov >/dev/null 2>&1; then
        checkov -d . --output json --output-file checkov-report.json || print_warning "Checkov found issues"
        print_success "Checkov scan completed"
    fi
}

setup_continuous_security() {
    print_header "Setting up Continuous Security"

    if [ ! -f ".github/dependabot.yml" ]; then
        print_status "Setting up Dependabot..."
        mkdir -p .github
        cat > .github/dependabot.yml << EOF
version: 2
updates:
  - package-ecosystem: "pip"
    directory: "/"
    schedule:
      interval: "weekly"
    open-pull-requests-limit: 10

  - package-ecosystem: "docker"
    directory: "/"
    schedule:
      interval: "weekly"

  - package-ecosystem: "github-actions"
    directory: "/"
    schedule:
      interval: "weekly"
EOF
        print_success "Dependabot configured"
    fi
}

main() {
    echo ""
    echo "DevSecOps Security Pipeline Setup"
    echo "================================="
    echo ""

    print_status "Security level: $SECURITY_LEVEL"

    if [ "$INTERACTIVE" = "true" ]; then
        echo -e "${YELLOW}This will set up a comprehensive security pipeline. Continue? (y/N):${NC}"
        read -r response
        if [[ ! "$response" =~ ^[Yy]$ ]]; then
            print_error "Setup cancelled by user"
            exit 0
        fi
    fi

    check_dependencies
    detect_project_type
    install_pre_commit
    setup_github_workflows
    install_security_tools
    run_security_scan
    setup_continuous_security

    print_header "Setup Complete"

    echo ""
    echo "Next Steps:"
    echo "1. Review security scan output above"
    echo "2. Address any identified issues"
    echo "3. Commit the security configuration files"
    echo "4. Push to trigger the CI/CD security pipeline"
    echo "5. Monitor security metrics regularly"
    echo ""
}

main "$@"
