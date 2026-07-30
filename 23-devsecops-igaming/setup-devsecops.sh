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

# shellcheck disable=SC2034,SC2129

# =============================================================================
# 🛡️ DevSecOps Standard Setup Script
# Universal Security Pipeline Installation
# =============================================================================
# This script sets up the complete DevSecOps security pipeline
# for any repository with comprehensive security scanning
# =============================================================================

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
MAGENTA='\033[0;35m'
CYAN='\033[0;36m'
WHITE='\033[1;37m'
NC='\033[0m' # No Color

# Configuration
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SECURITY_LEVEL="${1:-standard}"
INTERACTIVE="${2:-true}"
FORCE_INSTALL="${3:-false}"

# Functions
print_banner() {
    echo -e "${CYAN}"
    cat << "EOF"
╔══════════════════════════════════════════════════════════════════════╗
║                                                                      ║
║     🛡️ UNIVERSAL DEVSECOPS STANDARD SETUP 🛡️                       ║
║                                                                      ║
║     Comprehensive Security Pipeline for Any Project                 ║
║                                                                      ║
╚══════════════════════════════════════════════════════════════════════╝
EOF
    echo -e "${NC}"
}

print_status() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

print_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

print_header() {
    echo -e "\n${MAGENTA}═══ $1 ═══${NC}"
}

check_dependencies() {
    print_header "Checking Dependencies"

    local missing_deps=()

    # Check for required tools
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

    # Detect programming languages
    if find . -name "*.py" -type f | grep -q .; then
        languages+=("python")
        project_type="python"
    fi

    if find . -name "*.js" -o -name "*.ts" -type f | grep -q .; then
        languages+=("javascript")
        [ "$project_type" = "unknown" ] && project_type="javascript"
    fi

    if find . -name "*.go" -type f | grep -q .; then
        languages+=("go")
        [ "$project_type" = "unknown" ] && project_type="go"
    fi

    if find . -name "*.java" -type f | grep -q .; then
        languages+=("java")
        [ "$project_type" = "unknown" ] && project_type="java"
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
        cp "$SCRIPT_DIR/.pre-commit-config.yaml" ./
        print_success "Pre-commit configuration installed"
    else
        print_warning "Pre-commit configuration already exists"
    fi

    # Install pre-commit
    if ! command -v pre-commit >/dev/null 2>&1; then
        print_status "Installing pre-commit..."
        pip3 install pre-commit
        print_success "Pre-commit installed"
    fi

    # Install the pre-commit hooks
    print_status "Installing pre-commit hooks..."
    pre-commit install
    pre-commit install --hook-type commit-msg
    print_success "Pre-commit hooks installed"

    # Run initial scan
    print_status "Running initial security scan..."
    pre-commit run --all-files || true
    print_success "Initial security scan completed"
}

setup_github_workflows() {
    print_header "Setting up GitHub Workflows"

    # Create .github directory structure
    mkdir -p .github/workflows
    mkdir -p .github/ISSUE_TEMPLATE
    mkdir -p .github/PULL_REQUEST_TEMPLATE

    # Copy workflow template
    if [ ! -f ".github/workflows/security.yml" ]; then
        print_status "Installing security workflow..."
        cp "$SCRIPT_DIR/security-pipeline-template.yml" .github/workflows/security.yml
        print_success "Security workflow installed"
    else
        print_warning "Security workflow already exists"
    fi

    # Copy GitHub templates
    print_status "Installing GitHub templates..."
    cp -r "$SCRIPT_DIR/.github/"* .github/ 2>/dev/null || true
    print_success "GitHub templates installed"
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
        yamllint \
        black \
        isort \
        flake8 \
        mypy \
        detect-secrets \
        terraform-compliance \
        python-hcl2 \
        ruff
        print_success "Python security tools installed"
    fi

    # Container security tools
    if command -v docker >/dev/null 2>&1; then
        print_status "Installing container security tools..."
        # Trivy installation
        if ! command -v trivy >/dev/null 2>&1; then
            curl -sfL https://raw.githubusercontent.com/aquasecurity/trivy/main/contrib/install.sh | sh -s -- -b /usr/local/bin
            print_success "Trivy installed"
        fi

        # Hadolint installation
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
        # TFSec installation
        if ! command -v tfsec >/dev/null 2>&1; then
            curl -s https://raw.githubusercontent.com/aquasecurity/tfsec/master/scripts/install_linux.sh | bash
            print_success "TFSec installed"
        fi

        # TFLint installation
        if ! command -v tflint >/dev/null 2>&1; then
            curl -s https://raw.githubusercontent.com/terraform-linters/tflint/master/install_linux.sh | bash
            print_success "TFLint installed"
        fi

        # Checkov installation
        pip3 install --user checkov
        print_success "Checkov installed"

        # Terrascan installation
        if ! command -v terrascan >/dev/null 2>&1; then
            curl -L "$(curl -s https://api.github.com/repos/accurics/terrascan/releases/latest | grep -o -E -m 1 "https://.+?_Linux_x86_64.tar.gz")" > terrascan.tar.gz
            tar -xf terrascan.tar.gz terrascan && rm terrascan.tar.gz
            sudo install terrascan /usr/local/bin && rm terrascan
            print_success "Terrascan installed"
        fi
    fi

    # Additional security tools from original Azure DevOps pipeline
    print_status "Installing additional security tools..."

    # ShellCheck for shell script analysis
    if ! command -v shellcheck >/dev/null 2>&1; then
        sudo apt-get update && sudo apt-get install -y shellcheck
        print_success "ShellCheck installed"
    fi

    # Gitleaks for secret detection
    if ! command -v gitleaks >/dev/null 2>&1; then
        wget -q -O - https://github.com/gitleaks/gitleaks/releases/download/v8.18.2/gitleaks_8.18.2_linux_x64.tar.gz | tar xz
        sudo mv gitleaks /usr/local/bin/
        print_success "Gitleaks installed"
    fi

    # TruffleHog for advanced secret detection
    if ! command -v trufflehog >/dev/null 2>&1; then
        curl -sSfL https://raw.githubusercontent.com/trufflesecurity/trufflehog/main/scripts/install.sh | sh -s -- -b /usr/local/bin
        print_success "TruffleHog installed"
    fi

    # Install ty for Python type checking
    if [ -f ".project-type" ] && grep -q "python" .project-type; then
        print_status "Installing ty for Python type checking..."
        pip3 install --user ty
        print_success "ty installed"
    fi

    # Node.js tools for comprehensive level
    if [ "$SECURITY_LEVEL" = "comprehensive" ] || [ "$SECURITY_LEVEL" = "maximum" ]; then
        print_status "Installing Node.js security tools..."
        npm install -g prettier@3.2.0 markdownlint-cli@0.38.0 @commitlint/cli@18.4.4
        print_success "Node.js security tools installed"
    fi

    # Infracost for cost analysis (comprehensive level only)
    if [ "$SECURITY_LEVEL" = "comprehensive" ] || [ "$SECURITY_LEVEL" = "maximum" ]; then
        if ! command -v infracost >/dev/null 2>&1; then
            curl -fsSL https://raw.githubusercontent.com/infracost/infracost/master/scripts/install.sh | sh
            sudo mv infracost /usr/local/bin/
            print_success "Infracost installed"
        fi
    fi
}

configure_security_files() {
    print_header "Configuring Security Files"

    # Create security configuration files
    if [ ! -f "sonar-project.properties" ]; then
        print_status "Creating SonarQube configuration..."
        cat > sonar-project.properties << EOF
# SonarQube Configuration
sonar.projectKey=${PWD##*/}
sonar.projectName=${PWD##*/}
sonar.projectVersion=1.0.0
sonar.sources=.
sonar.exclusions=**/migrations/**,**/venv/**,**/__pycache__/**
sonar.python.version=3.11
EOF
        print_success "SonarQube configuration created"
    fi

    # Create OWASP ZAP rules if not exists
    if [ ! -f ".zap/rules.tsv" ]; then
        print_status "Creating OWASP ZAP rules..."
        mkdir -p .zap
        cp "$SCRIPT_DIR/.zap/rules.tsv" .zap/ 2>/dev/null || true
        print_success "OWASP ZAP rules configured"
    fi

    # Create security policy if not exists
    if [ ! -f "SECURITY.md" ]; then
        print_status "Creating security policy..."
        cat > SECURITY.md << 'EOF'
# Security Policy

## Reporting Security Vulnerabilities

If you discover a security vulnerability, please report it through our responsible disclosure process:

1. **Email**: security@company.com
2. **Subject**: Include "SECURITY" in the subject line
3. **Details**: Provide comprehensive information about the vulnerability

## Response Timeline

- **Acknowledgment**: Within 24 hours
- **Initial Assessment**: Within 72 hours
- **Remediation Plan**: Within 1 week
- **Full Resolution**: Timeline depends on severity

## Supported Versions

We provide security updates for:
- Current major version
- Previous major version (for 6 months after new major release)

## Security Measures

This project implements:
- Automated security scanning
- Regular dependency updates
- Secure coding practices
- Comprehensive security testing

## Recognition

We appreciate responsible disclosure and will acknowledge contributors who help improve our security posture.
EOF
        print_success "Security policy created"
    fi
}

run_security_scan() {
    print_header "Running Comprehensive Security Scan"

    print_status "Running SAST scans..."
    if command -v bandit >/dev/null 2>&1; then
        bandit -r . -f json -o bandit-report.json || true
        print_success "Bandit scan completed"
    fi

    if command -v safety >/dev/null 2>&1; then
        safety check --json --output safety-report.json || true
        print_success "Safety scan completed"
    fi

    print_status "Running Python linting with ruff..."
    if command -v ruff >/dev/null 2>&1; then
        ruff check . --output-format=json --output-file=ruff-report.json || true
        print_success "ruff linting completed"
    fi

    print_status "Running Python formatting check with ruff..."
    if command -v ruff >/dev/null 2>&1; then
        ruff format . --diff > ruff-format-report.txt 2>&1 || true
        print_success "ruff formatting check completed"
    fi

    print_status "Running container security scans..."
    if command -v trivy >/dev/null 2>&1; then
        trivy fs --format json --output trivy-fs-report.json . || true
        print_success "Trivy filesystem scan completed"
    fi

    if command -v hadolint >/dev/null 2>&1; then
        find . -name "Dockerfile*" -exec hadolint {} \; > hadolint-report.txt 2>&1 || true
        print_success "Hadolint scan completed"
    fi

    print_status "Running infrastructure security scans..."
    if command -v tfsec >/dev/null 2>&1; then
        tfsec . --format json --out tfsec-report.json || true
        print_success "TFSec scan completed"
    fi

    if command -v checkov >/dev/null 2>&1; then
        checkov -d . --output json --output-file checkov-report.json || true
        print_success "Checkov scan completed"
    fi

    print_status "Running Python type checking with ty..."
    if command -v ty >/dev/null 2>&1; then
        ty check . > ty-report.txt 2>&1 || true
        print_success "ty type checking completed"
    fi
}

generate_security_report() {
    print_header "Generating Security Report"

    local report_file="security-assessment-report.md"

    cat > "$report_file" << EOF
# 🛡️ Security Assessment Report

**Date**: $(date)
**Project**: ${PWD##*/}
**Security Level**: $SECURITY_LEVEL

## 📋 Summary

This report summarizes the security assessment performed during DevSecOps setup.

## 🔍 Security Scans Performed

EOF

    # Add scan results to report
    if [ -f "bandit-report.json" ]; then
        echo "### Bandit (Python Security)" >> "$report_file"
        echo "- Status: Completed" >> "$report_file"
        echo "- Report: bandit-report.json" >> "$report_file"
        echo "" >> "$report_file"
    fi

    if [ -f "safety-report.json" ]; then
        echo "### Safety (Dependencies)" >> "$report_file"
        echo "- Status: Completed" >> "$report_file"
        echo "- Report: safety-report.json" >> "$report_file"
        echo "" >> "$report_file"
    fi

    if [ -f "trivy-fs-report.json" ]; then
        echo "### Trivy (Container/File System)" >> "$report_file"
        echo "- Status: Completed" >> "$report_file"
        echo "- Report: trivy-fs-report.json" >> "$report_file"
        echo "" >> "$report_file"
    fi

    if [ -f "hadolint-report.txt" ]; then
        echo "### Hadolint (Dockerfile)" >> "$report_file"
        echo "- Status: Completed" >> "$report_file"
        echo "- Report: hadolint-report.txt" >> "$report_file"
        echo "" >> "$report_file"
    fi

    if [ -f "tfsec-report.json" ]; then
        echo "### TFSec (Terraform)" >> "$report_file"
        echo "- Status: Completed" >> "$report_file"
        echo "- Report: tfsec-report.json" >> "$report_file"
        echo "" >> "$report_file"
    fi

    if [ -f "checkov-report.json" ]; then
        echo "### Checkov (Infrastructure)" >> "$report_file"
        echo "- Status: Completed" >> "$report_file"
        echo "- Report: checkov-report.json" >> "$report_file"
        echo "" >> "$report_file"
    fi

    if [ -f "ty-report.txt" ]; then
        echo "### ty (Python Type Checking)" >> "$report_file"
        echo "- Status: Completed" >> "$report_file"
        echo "- Report: ty-report.txt" >> "$report_file"
        echo "" >> "$report_file"
    fi

    if [ -f "ruff-report.json" ]; then
        echo "### ruff (Python Linting)" >> "$report_file"
        echo "- Status: Completed" >> "$report_file"
        echo "- Report: ruff-report.json" >> "$report_file"
        echo "" >> "$report_file"
    fi

    if [ -f "ruff-format-report.txt" ]; then
        echo "### ruff (Python Formatting)" >> "$report_file"
        echo "- Status: Completed" >> "$report_file"
        echo "- Report: ruff-format-report.txt" >> "$report_file"
        echo "" >> "$report_file"
    fi

    cat >> "$report_file" << EOF

## 🚀 Next Steps

1. Review all security scan reports
2. Address any high or critical severity issues
3. Set up continuous monitoring
4. Configure automated security updates
5. Train team on security processes

## 📞 Support

For security-related questions or issues, contact the security team.

---
*This report was generated automatically by the DevSecOps setup script*
EOF

    print_success "Security report generated: $report_file"
}

setup_continuous_security() {
    print_header "Setting up Continuous Security"

    # Configure Dependabot
    if [ ! -f ".github/dependabot.yml" ]; then
        print_status "Setting up Dependabot..."
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

    # Configure security policy
    if [ ! -f ".github/security.md" ]; then
        print_status "Creating security policy..."
        cat > .github/security.md << EOF
# Security Policy

## Supported Versions

| Version | Supported |
| ------- | --------- |
| Latest  | ✅        |
| Previous| ✅        |

## Reporting Vulnerabilities

Please report security vulnerabilities to security@company.com

## Security Updates

Security updates are released as needed and announced through our security channels.
EOF
        print_success "Security policy created"
    fi
}

main() {
    print_banner

    print_status "Starting DevSecOps setup with security level: $SECURITY_LEVEL"
    print_status "Interactive mode: $INTERACTIVE"

    if [ "$INTERACTIVE" = "true" ]; then
        echo -e "${YELLOW}This will set up a comprehensive security pipeline.${NC}"
        echo -e "${YELLOW}Continue? (y/N):${NC}"
        read -r response
        if [[ ! "$response" =~ ^[Yy]$ ]]; then
            print_error "Setup cancelled by user"
            exit 0
        fi
    fi

    # Execute setup steps
    check_dependencies
    detect_project_type
    install_pre_commit
    setup_github_workflows
    install_security_tools
    configure_security_files
    run_security_scan
    generate_security_report
    setup_continuous_security

    print_header "Setup Complete!"

    echo -e "${GREEN}🎉 DevSecOps security pipeline setup completed successfully!${NC}"
    echo -e "${GREEN}📊 Security report generated: security-assessment-report.md${NC}"
    echo -e "${GREEN}🔧 All security tools and configurations are ready${NC}"
    echo ""
    echo -e "${CYAN}Next Steps:${NC}"
    echo "1. Review the security report"
    echo "2. Address any identified issues"
    echo "3. Commit the security configuration files"
    echo "4. Push to trigger the security pipeline"
    echo "5. Monitor security metrics regularly"
    echo ""
    echo -e "${YELLOW}For support, refer to the documentation in the secpipeline folder${NC}"
}

# Run main function
main "$@"
