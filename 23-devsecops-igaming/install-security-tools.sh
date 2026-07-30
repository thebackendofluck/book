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

# shellcheck disable=SC2086,SC2317

# ============================================================================
# Azure DevOps Security Tools Installation Script
# ============================================================================
# Container scanning and Infracost are DISABLED by default
# Enable only if needed (not using Aquasec for containers)
# ============================================================================

set -e

# Colors (work in Azure DevOps logs)
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# Configuration
TOOLS_DIR="/opt/security-tools"
REPORTS_DIR="${BUILD_ARTIFACTSTAGINGDIRECTORY:-./security-reports}"
PARALLEL_JOBS=4
RETRY_COUNT=3
RETRY_DELAY=5

# Feature flags (disabled by default)
ENABLE_CONTAINER_SCAN="${ENABLE_CONTAINER_SCAN:-false}"
ENABLE_COST_ESTIMATION="${ENABLE_COST_ESTIMATION:-false}"

# Detect environment
if [ -n "$AGENT_ID" ]; then
    echo "##[section]Running in Azure DevOps Pipeline"
    IS_AZURE_DEVOPS=true

    # Show configuration
    echo "##[section]Configuration:"
    echo "  Container Scanning: $ENABLE_CONTAINER_SCAN (default: false - using Aquasec)"
    echo "  Cost Estimation: $ENABLE_COST_ESTIMATION (default: false)"
else
    echo "Running locally"
    IS_AZURE_DEVOPS=false

    echo "Configuration:"
    echo "  Container Scanning: $ENABLE_CONTAINER_SCAN"
    echo "  Cost Estimation: $ENABLE_COST_ESTIMATION"
fi

# Function to log in Azure DevOps format
log_section() {
    if [ "$IS_AZURE_DEVOPS" = true ]; then
        echo "##[section]$1"
    else
        echo -e "${BLUE}═══ $1 ═══${NC}"
    fi
}

log_group_start() {
    if [ "$IS_AZURE_DEVOPS" = true ]; then
        echo "##[group]$1"
    else
        echo -e "${CYAN}▶ $1${NC}"
    fi
}

log_group_end() {
    if [ "$IS_AZURE_DEVOPS" = true ]; then
        echo "##[endgroup]"
    fi
}

log_success() {
    if [ "$IS_AZURE_DEVOPS" = true ]; then
        echo "##[command]✓ $1"
    else
        echo -e "${GREEN}✓ $1${NC}"
    fi
}

log_warning() {
    if [ "$IS_AZURE_DEVOPS" = true ]; then
        echo "##vso[task.logissue type=warning]$1"
    else
        echo -e "${YELLOW}⚠ $1${NC}"
    fi
}

log_error() {
    if [ "$IS_AZURE_DEVOPS" = true ]; then
        echo "##vso[task.logissue type=error]$1"
    else
        echo -e "${RED}✗ $1${NC}"
    fi
}

# Function to retry command
# shellcheck disable=SC2329
retry_command() {
    local command="$1"
    local description="$2"
    local attempt=1

    while [ $attempt -le $RETRY_COUNT ]; do
        if eval "$command"; then
            return 0
        fi

        if [ $attempt -lt $RETRY_COUNT ]; then
            log_warning "Failed to $description (attempt $attempt/$RETRY_COUNT). Retrying in ${RETRY_DELAY}s..."
            sleep $RETRY_DELAY
        fi
        attempt=$((attempt + 1))
    done

    log_error "Failed to $description after $RETRY_COUNT attempts"
    return 1
}

# Create directories
create_directories() {
    log_section "Creating directories"

    mkdir -p "$REPORTS_DIR"/{checkov,tfsec,terrascan,secrets,bandit,semgrep,infracost,trivy}
    mkdir -p "$TOOLS_DIR"

    log_success "Directories created"
}

# Install Python tools
install_python_tools() {
    log_section "Installing Python-based Security Tools"

    # Ensure pip is updated
    python3 -m pip install --upgrade pip --quiet

    # List of Python tools to install
    declare -a python_tools=(
        "checkov==3.2.521"
        "detect-secrets==1.5.0"
        "bandit[toml]==1.9.4"
        "semgrep==1.159.0"
        "safety==3.7.0"
        "terraform-compliance==1.14.1"
        "python-hcl2==8.1.2"
        "yamllint==1.33.0"
    )

    log_group_start "Installing Python packages"

    # Install in parallel for speed
    for tool in "${python_tools[@]}"; do
        (
            if pip3 install --user "$tool" --quiet; then
                log_success "Installed $tool"
            else
                log_error "Failed to install $tool"
            fi
        ) &

        # Limit parallel jobs
        if [[ $(jobs -r -p | wc -l) -ge $PARALLEL_JOBS ]]; then
            wait -n
        fi
    done
    wait

    log_group_end

    # Verify installations
    log_group_start "Verifying Python tool installations"
    checkov --version || log_warning "Checkov verification failed"
    detect-secrets --version || log_warning "Detect-secrets verification failed"
    bandit --version || log_warning "Bandit verification failed"
    semgrep --version || log_warning "Semgrep verification failed"
    safety --version || log_warning "Safety verification failed"
    log_group_end
}

# Install TFSec
install_tfsec() {
    log_section "Installing TFSec"

    if command -v tfsec &> /dev/null; then
        log_success "TFSec is already installed ($(tfsec --version))"
        return 0
    fi

    TFSEC_VERSION=$(curl -s https://api.github.com/repos/aquasecurity/tfsec/releases/latest | grep -Po '"tag_name": "\K.*?(?=")')
    curl -fsSL https://github.com/aquasecurity/tfsec/releases/download/${TFSEC_VERSION}/tfsec-linux-amd64 -o /tmp/tfsec
    chmod +x /tmp/tfsec
    sudo mv /tmp/tfsec /usr/local/bin/

    log_success "TFSec installed: $(tfsec --version)"
}

# Install TFLint
install_tflint() {
    log_section "Installing TFLint"

    if command -v tflint &> /dev/null; then
        log_success "TFLint is already installed ($(tflint --version | head -1))"
        return 0
    fi

    curl -s https://raw.githubusercontent.com/terraform-linters/tflint/master/install_linux.sh | bash

    # Install Azure plugin
    mkdir -p ~/.tflint.d/plugins
    tflint --init

    log_success "TFLint installed with Azure plugin"
}

# Install Gitleaks
install_gitleaks() {
    log_section "Installing Gitleaks"

    if command -v gitleaks &> /dev/null; then
        log_success "Gitleaks is already installed ($(gitleaks version))"
        return 0
    fi

    GITLEAKS_VERSION=$(curl -s https://api.github.com/repos/gitleaks/gitleaks/releases/latest | grep -Po '"tag_name": "\K.*?(?=")')
    curl -fsSL https://github.com/gitleaks/gitleaks/releases/download/${GITLEAKS_VERSION}/gitleaks_${GITLEAKS_VERSION#v}_linux_x64.tar.gz | tar xz
    sudo mv gitleaks /usr/local/bin/

    log_success "Gitleaks installed: $(gitleaks version)"
}

# Install TruffleHog
install_trufflehog() {
    log_section "Installing TruffleHog"

    if command -v trufflehog &> /dev/null; then
        log_success "TruffleHog is already installed"
        return 0
    fi

    curl -fsSL https://raw.githubusercontent.com/trufflesecurity/trufflehog/main/scripts/install.sh | sh -s -- -b /usr/local/bin

    log_success "TruffleHog installed"
}

# Install Terrascan
install_terrascan() {
    log_section "Installing Terrascan"

    if command -v terrascan &> /dev/null; then
        log_success "Terrascan is already installed ($(terrascan version))"
        return 0
    fi

    TERRASCAN_VERSION="${TERRASCAN_VERSION:-v1.19.9}"
    curl -fsSL "https://github.com/tenable/terrascan/releases/download/${TERRASCAN_VERSION}/terrascan_${TERRASCAN_VERSION#v}_Linux_x86_64.tar.gz" -o /tmp/terrascan.tar.gz
    tar -xzf /tmp/terrascan.tar.gz -C /tmp terrascan
    sudo install /tmp/terrascan /usr/local/bin/terrascan
    rm -f /tmp/terrascan /tmp/terrascan.tar.gz

    log_success "Terrascan installed: $(terrascan version)"
}

# Install Infracost (Optional - disabled by default)
install_infracost() {
    log_section "Installing Infracost (Optional)"

    if [ "$ENABLE_COST_ESTIMATION" != "true" ]; then
        log_warning "Infracost installation skipped (ENABLE_COST_ESTIMATION=false)"
        return 0
    fi

    if command -v infracost &> /dev/null; then
        log_success "Infracost is already installed ($(infracost --version))"
        return 0
    fi

    log_group_start "Installing Infracost (explicitly enabled)"
    curl -fsSL https://raw.githubusercontent.com/infracost/infracost/master/scripts/install.sh | sh
    sudo mv infracost /usr/local/bin/
    log_group_end

    log_success "Infracost installed"
}

# Install Trivy (Optional - disabled by default)
install_trivy() {
    log_section "Installing Trivy (Optional)"

    if [ "$ENABLE_CONTAINER_SCAN" != "true" ]; then
        log_warning "Trivy installation skipped (ENABLE_CONTAINER_SCAN=false - using Aquasec)"
        return 0
    fi

    if command -v trivy &> /dev/null; then
        log_success "Trivy is already installed ($(trivy --version))"
        return 0
    fi

    log_group_start "Installing Trivy (explicitly enabled)"
    wget -qO - https://aquasecurity.github.io/trivy-repo/deb/public.key | sudo apt-key add -
    echo "deb https://aquasecurity.github.io/trivy-repo/deb $(lsb_release -sc) main" | sudo tee -a /etc/apt/sources.list.d/trivy.list
    sudo apt-get update -qq
    sudo apt-get install -y trivy
    log_group_end

    log_success "Trivy installed"
}

# Install additional tools
install_additional_tools() {
    log_section "Installing Additional Tools"

    # Hadolint (Optional - only if container scanning is enabled)
    if [ "$ENABLE_CONTAINER_SCAN" = "true" ]; then
        if ! command -v hadolint &> /dev/null; then
            log_group_start "Installing Hadolint (container scanning enabled)"
            wget -O /tmp/hadolint https://github.com/hadolint/hadolint/releases/latest/download/hadolint-Linux-x86_64
            chmod +x /tmp/hadolint
            sudo mv /tmp/hadolint /usr/local/bin/
            log_success "Hadolint installed"
            log_group_end
        else
            log_success "Hadolint already installed"
        fi
    else
        log_warning "Hadolint installation skipped (container scanning disabled)"
    fi

    # ShellCheck
    if ! command -v shellcheck &> /dev/null; then
        sudo apt-get update -qq && sudo apt-get install -y shellcheck
        log_success "ShellCheck installed"
    fi

    # KICS (Keeping Infrastructure as Code Secure)
    if ! command -v kics &> /dev/null; then
        KICS_VERSION=$(curl -s https://api.github.com/repos/Checkmarx/kics/releases/latest | grep -Po '"tag_name": "\K.*?(?=")')
        curl -fsSL https://github.com/Checkmarx/kics/releases/download/${KICS_VERSION}/kics_${KICS_VERSION#v}_linux_x64.tar.gz | tar xz
        sudo mv kics /usr/local/bin/
        log_success "KICS installed"
    fi
}

# Create configuration files
create_config_files() {
    log_section "Creating Configuration Files"

    # TFLint config
    cat > .tflint.hcl << 'EOF'
plugin "azurerm" {
  enabled = true
  version = "0.25.1"
  source  = "github.com/terraform-linters/tflint-ruleset-azurerm"
}

config {
  module = true
  force = false
}

rule "terraform_deprecated_interpolation" { enabled = true }
rule "terraform_documented_outputs" { enabled = true }
rule "terraform_documented_variables" { enabled = true }
rule "terraform_typed_variables" { enabled = true }
rule "terraform_naming_convention" { enabled = true }
rule "terraform_required_version" { enabled = true }
rule "terraform_required_providers" { enabled = true }
EOF

    # Checkov config
    cat > .checkov.yml << 'EOF'
framework:
  - terraform
  - terraform_plan
  - azure
  - secrets

output: cli
compact: false
quiet: false

skip-path:
  - .terraform
  - .git

download-external-modules: true
evaluate-variables: true
EOF

    log_success "Configuration files created"
}

# Generate installation report
generate_report() {
    log_section "Generating Installation Report"

    REPORT_FILE="$REPORTS_DIR/tools-installation-report.txt"

    {
        echo "Security Tools Installation Report"
        echo "===================================="
        echo "Date: $(date)"
        echo "Environment: ${AGENT_NAME:-Local}"
        echo ""
        echo "Configuration:"
        echo "  Container Scanning: $ENABLE_CONTAINER_SCAN (default: false)"
        echo "  Cost Estimation: $ENABLE_COST_ESTIMATION (default: false)"
        echo ""
        echo "Installed Tools:"
        echo "----------------"

        # Check each tool (skip optional ones if not enabled)
        for tool in checkov tfsec tflint gitleaks trufflehog bandit semgrep safety terrascan shellcheck kics; do
            if command -v "$tool" &> /dev/null; then
                version=$($tool --version 2>&1 | head -1 || echo "version unknown")
                echo "✓ $tool: $version"
            else
                echo "✗ $tool: NOT INSTALLED"
            fi
        done

        # Optional tools (only check if enabled)
        if [ "$ENABLE_CONTAINER_SCAN" = "true" ]; then
            for tool in trivy hadolint; do
                if command -v "$tool" &> /dev/null; then
                    version=$($tool --version 2>&1 | head -1 || echo "version unknown")
                    echo "✓ $tool: $version (optional - enabled)"
                else
                    echo "✗ $tool: NOT INSTALLED"
                fi
            done
        else
            echo "○ trivy: SKIPPED (container scanning disabled)"
            echo "○ hadolint: SKIPPED (container scanning disabled)"
        fi

        if [ "$ENABLE_COST_ESTIMATION" = "true" ]; then
            if command -v infracost &> /dev/null; then
                version=$(infracost --version 2>&1 | head -1 || echo "version unknown")
                echo "✓ infracost: $version (optional - enabled)"
            else
                echo "✗ infracost: NOT INSTALLED"
            fi
        else
            echo "○ infracost: SKIPPED (cost estimation disabled)"
        fi

        echo ""
        echo "Python Packages:"
        echo "----------------"
        pip3 list | grep -E "(checkov|bandit|semgrep|safety|detect-secrets|terrascan)" || true

    } > "$REPORT_FILE"

    cat "$REPORT_FILE"

    if [ "$IS_AZURE_DEVOPS" = true ]; then
        echo "##vso[artifact.upload artifactname=ToolsReport;]$REPORT_FILE"
    fi

    log_success "Installation report generated: $REPORT_FILE"
}

# Main installation function
main() {
    log_section "Starting Security Tools Installation"

    # Show configuration
    echo "==========================================="
    echo "Configuration:"
    echo "  Container Scanning: $ENABLE_CONTAINER_SCAN"
    echo "  Cost Estimation: $ENABLE_COST_ESTIMATION"
    echo "==========================================="
    echo ""

    # Update PATH
    export PATH="$PATH:/usr/local/bin:$HOME/.local/bin"

    # Check if running with sufficient permissions
    if [ "$EUID" -ne 0 ] && [ "$IS_AZURE_DEVOPS" = false ]; then
        log_warning "Not running as root. Some installations may require sudo password."
    fi

    # Create directories
    create_directories

    # Install tools in parallel where possible
    (
        install_python_tools &
        PID_PYTHON=$!

        install_tfsec &
        PID_TFSEC=$!

        install_tflint &
        PID_TFLINT=$!

        install_gitleaks &
        PID_GITLEAKS=$!

        install_trufflehog &
        PID_TRUFFLEHOG=$!

        install_terrascan &
        PID_TERRASCAN=$!

        # Optional tools (only if enabled)
        if [ "$ENABLE_COST_ESTIMATION" = "true" ]; then
            install_infracost &
            PID_INFRACOST=$!
        fi

        if [ "$ENABLE_CONTAINER_SCAN" = "true" ]; then
            install_trivy &
            PID_TRIVY=$!
        fi

        # Wait for all background jobs
        wait $PID_PYTHON $PID_TFSEC $PID_TFLINT $PID_GITLEAKS $PID_TRUFFLEHOG $PID_TERRASCAN

        # Wait for optional tools if enabled
        [ "$ENABLE_COST_ESTIMATION" = "true" ] && wait $PID_INFRACOST
        [ "$ENABLE_CONTAINER_SCAN" = "true" ] && wait $PID_TRIVY
    )

    # Install additional tools
    install_additional_tools

    # Create configuration files
    create_config_files

    # Generate report
    generate_report

    log_section "Installation Complete"

    # Set Azure DevOps variables if running in pipeline
    if [ "$IS_AZURE_DEVOPS" = true ]; then
        echo "##vso[task.setvariable variable=SECURITY_TOOLS_INSTALLED]true"
        echo "##vso[task.setvariable variable=REPORTS_PATH]$REPORTS_DIR"
    fi

    # Summary
    echo ""
    echo "==========================================="
    echo "Summary:"
    echo "  ✅ Core security tools installed"
    if [ "$ENABLE_CONTAINER_SCAN" = "true" ]; then
        echo "  ✅ Container scanning tools installed"
    else
        echo "  ⏭️  Container scanning tools skipped (using Aquasec)"
    fi
    if [ "$ENABLE_COST_ESTIMATION" = "true" ]; then
        echo "  ✅ Cost estimation tools installed"
    else
        echo "  ⏭️  Cost estimation tools skipped"
    fi
    echo "==========================================="

    log_success "All requested security tools have been installed successfully!"

    # Return success
    exit 0
}

# Run main function
main "$@"
