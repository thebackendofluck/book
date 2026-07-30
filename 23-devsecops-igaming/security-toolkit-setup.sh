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

# shellcheck disable=SC1090,SC2034,SC2076,SC2199,SC2207,SC2236

# ============================================================================
# Universal Security Toolkit Setup for Infrastructure as Code
# ============================================================================
# This script installs and configures security tools for any IaC repository
# Supports: Terraform, Python, Kubernetes, Docker, and general code security
#
# Usage: ./security-toolkit-setup.sh [options]
# ============================================================================

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

# Configuration file
CONFIG_FILE=".security-toolkit.conf"

# Tool categories with descriptions
declare -A SECURITY_TOOLS=(
    ["checkov"]="Infrastructure as Code security scanner (300+ policies)"
    ["tfsec"]="Terraform-specific security scanner"
    ["terrascan"]="Policy as Code for compliance validation"
    ["detect-secrets"]="Credential and secret detection"
    ["gitleaks"]="Git repository secret scanning"
    ["trufflehog"]="Search Git history for secrets"
    ["bandit"]="Python security vulnerability scanner"
    ["semgrep"]="Static analysis for security patterns"
    ["safety"]="Python dependency vulnerability checker"
)

declare -A TERRAFORM_TOOLS=(
    ["terraform-fmt"]="Format Terraform files to canonical style"
    ["terraform-validate"]="Validate Terraform configuration syntax"
    ["terraform-docs"]="Generate documentation from Terraform modules"
    ["tflint"]="Terraform linter with Azure/AWS/GCP rules"
    ["infracost"]="Cloud cost estimation for Terraform"
    ["terraform-compliance"]="BDD testing for Terraform"
    ["tfsec"]="Terraform security scanner (duplicate with security)"
    ["checkov"]="IaC scanner (duplicate with security)"
)

declare -A CODE_QUALITY_TOOLS=(
    ["black"]="Python code formatter"
    ["isort"]="Python import sorter"
    ["flake8"]="Python style guide enforcement"
    ["pylint"]="Python code analysis"
    ["mypy"]="Python static type checker"
    ["prettier"]="Code formatter for multiple languages"
    ["yamllint"]="YAML file linter"
    ["markdownlint"]="Markdown file linter"
    ["shellcheck"]="Shell script static analysis"
    ["hadolint"]="Dockerfile linter"
)

declare -A KUBERNETES_TOOLS=(
    ["kubeval"]="Kubernetes YAML validator"
    ["kubesec"]="Kubernetes security scanner"
    ["kube-score"]="Kubernetes object analysis"
    ["polaris"]="Kubernetes best practices validation"
    ["trivy"]="Container vulnerability scanner"
)

# Default selections
ENABLED_TOOLS=()
INSTALL_MODE="interactive"
PRESET_MODE=""

# Function to display banner
show_banner() {
    cat << "EOF"
╔══════════════════════════════════════════════════════════════════════╗
║                                                                      ║
║     🔐 UNIVERSAL SECURITY TOOLKIT FOR INFRASTRUCTURE AS CODE 🔐     ║
║                                                                      ║
║     Comprehensive Security & Compliance Tools for DevSecOps         ║
║                                                                      ║
╚══════════════════════════════════════════════════════════════════════╝
EOF
}

# Function to display help
show_help() {
    cat << EOF

${CYAN}USAGE:${NC}
    $(basename "$0") [OPTIONS]

${CYAN}OPTIONS:${NC}
    -h, --help              Show this help message
    -i, --interactive       Interactive mode to select tools (default)
    -a, --all              Install all available tools
    -p, --preset PRESET    Use a preset configuration:
                           terraform  - Terraform security suite
                           python     - Python security suite
                           kubernetes - Kubernetes security suite
                           minimal    - Essential tools only
                           maximum    - All security tools
    -c, --config FILE      Load configuration from file
    -s, --save FILE        Save current selection to config file
    -l, --list             List all available tools
    -d, --dry-run          Show what would be installed without installing

${CYAN}EXAMPLES:${NC}
    # Interactive selection
    ./$(basename "$0") --interactive

    # Install Terraform security preset
    ./$(basename "$0") --preset terraform

    # Install all tools
    ./$(basename "$0") --all

    # Save configuration for team use
    ./$(basename "$0") --interactive --save team-security.conf

    # Use saved configuration
    ./$(basename "$0") --config team-security.conf

${CYAN}PRESETS:${NC}
    ${GREEN}terraform${NC}  - Checkov, TFSec, TFLint, Terraform-Docs, Infracost
    ${GREEN}python${NC}     - Bandit, Safety, Black, Flake8, MyPy
    ${GREEN}kubernetes${NC} - Checkov, Kubesec, Trivy, Polaris
    ${GREEN}minimal${NC}    - Detect-Secrets, Gitleaks, TFSec
    ${GREEN}maximum${NC}    - All available security tools

EOF
}

# Function to list all tools
list_tools() {
    echo -e "\n${CYAN}═══ AVAILABLE SECURITY TOOLS ═══${NC}\n"

    echo -e "${YELLOW}SECURITY SCANNERS:${NC}"
    for tool in "${!SECURITY_TOOLS[@]}"; do
        printf "  ${GREEN}%-20s${NC} - %s\n" "$tool" "${SECURITY_TOOLS[$tool]}"
    done

    echo -e "\n${YELLOW}TERRAFORM TOOLS:${NC}"
    for tool in "${!TERRAFORM_TOOLS[@]}"; do
        printf "  ${GREEN}%-20s${NC} - %s\n" "$tool" "${TERRAFORM_TOOLS[$tool]}"
    done

    echo -e "\n${YELLOW}CODE QUALITY TOOLS:${NC}"
    for tool in "${!CODE_QUALITY_TOOLS[@]}"; do
        printf "  ${GREEN}%-20s${NC} - %s\n" "$tool" "${CODE_QUALITY_TOOLS[$tool]}"
    done

    echo -e "\n${YELLOW}KUBERNETES TOOLS:${NC}"
    for tool in "${!KUBERNETES_TOOLS[@]}"; do
        printf "  ${GREEN}%-20s${NC} - %s\n" "$tool" "${KUBERNETES_TOOLS[$tool]}"
    done
}

# Function for interactive tool selection
interactive_selection() {
    echo -e "\n${CYAN}═══ INTERACTIVE TOOL SELECTION ═══${NC}\n"
    echo -e "${YELLOW}Select tools to install (y/n for each):${NC}\n"

    # Security tools
    echo -e "${MAGENTA}▶ Security Scanners:${NC}"
    for tool in "${!SECURITY_TOOLS[@]}"; do
        read -p "  Install $tool? (${SECURITY_TOOLS[$tool]}) [y/N]: " -n 1 -r
        echo
        if [[ $REPLY =~ ^[Yy]$ ]]; then
            ENABLED_TOOLS+=("$tool")
            echo -e "    ${GREEN}✓ $tool added${NC}"
        fi
    done

    # Terraform tools
    echo -e "\n${MAGENTA}▶ Terraform Tools:${NC}"
    for tool in "${!TERRAFORM_TOOLS[@]}"; do
        # Skip duplicates
        if [[ " ${ENABLED_TOOLS[@]} " =~ " ${tool} " ]]; then
            continue
        fi
        read -p "  Install $tool? (${TERRAFORM_TOOLS[$tool]}) [y/N]: " -n 1 -r
        echo
        if [[ $REPLY =~ ^[Yy]$ ]]; then
            ENABLED_TOOLS+=("$tool")
            echo -e "    ${GREEN}✓ $tool added${NC}"
        fi
    done

    # Code quality tools
    echo -e "\n${MAGENTA}▶ Code Quality Tools:${NC}"
    for tool in "${!CODE_QUALITY_TOOLS[@]}"; do
        read -p "  Install $tool? (${CODE_QUALITY_TOOLS[$tool]}) [y/N]: " -n 1 -r
        echo
        if [[ $REPLY =~ ^[Yy]$ ]]; then
            ENABLED_TOOLS+=("$tool")
            echo -e "    ${GREEN}✓ $tool added${NC}"
        fi
    done

    # Kubernetes tools
    echo -e "\n${MAGENTA}▶ Kubernetes Tools:${NC}"
    for tool in "${!KUBERNETES_TOOLS[@]}"; do
        read -p "  Install $tool? (${KUBERNETES_TOOLS[$tool]}) [y/N]: " -n 1 -r
        echo
        if [[ $REPLY =~ ^[Yy]$ ]]; then
            ENABLED_TOOLS+=("$tool")
            echo -e "    ${GREEN}✓ $tool added${NC}"
        fi
    done
}

# Function to load preset
load_preset() {
    local preset=$1
    case $preset in
        terraform)
            ENABLED_TOOLS=("checkov" "tfsec" "tflint" "terraform-docs" "terraform-fmt" "terraform-validate" "infracost" "detect-secrets")
            echo -e "${GREEN}✓ Loaded Terraform security preset${NC}"
        ;;
        python)
            ENABLED_TOOLS=("bandit" "safety" "black" "flake8" "mypy" "isort" "detect-secrets")
            echo -e "${GREEN}✓ Loaded Python security preset${NC}"
        ;;
        kubernetes)
            ENABLED_TOOLS=("checkov" "kubesec" "trivy" "polaris" "kubeval")
            echo -e "${GREEN}✓ Loaded Kubernetes security preset${NC}"
        ;;
        minimal)
            ENABLED_TOOLS=("detect-secrets" "gitleaks" "tfsec")
            echo -e "${GREEN}✓ Loaded minimal security preset${NC}"
        ;;
        maximum)
            ENABLED_TOOLS=($(echo "${!SECURITY_TOOLS[@]}" "${!TERRAFORM_TOOLS[@]}" "${!CODE_QUALITY_TOOLS[@]}" "${!KUBERNETES_TOOLS[@]}" | tr ' ' '\n' | sort -u))
            echo -e "${GREEN}✓ Loaded maximum security preset (all tools)${NC}"
        ;;
        *)
            echo -e "${RED}✗ Unknown preset: $preset${NC}"
            exit 1
        ;;
    esac
}

# Function to save configuration
save_config() {
    local config_file=$1
    {
        echo "# Security Toolkit Configuration"
        echo "# Generated: $(date)"
        echo "ENABLED_TOOLS=("
        for tool in "${ENABLED_TOOLS[@]}"; do
            echo "  \"$tool\""
        done
        echo ")"
    } > "$config_file"
    echo -e "${GREEN}✓ Configuration saved to $config_file${NC}"
}

# Function to load configuration
load_config() {
    local config_file=$1
    if [[ -f "$config_file" ]]; then
        source "$config_file"
        echo -e "${GREEN}✓ Configuration loaded from $config_file${NC}"
    else
        echo -e "${RED}✗ Configuration file not found: $config_file${NC}"
        exit 1
    fi
}

# Function to check if command exists
command_exists() {
    command -v "$1" >/dev/null 2>&1
}

# Function to detect OS
detect_os() {
    if [[ "$OSTYPE" == "linux-gnu"* ]]; then
        OS="linux"
        if [[ -f /etc/debian_version ]]; then
            DISTRO="debian"
            elif [[ -f /etc/redhat-release ]]; then
            DISTRO="redhat"
        else
            DISTRO="unknown"
        fi
        elif [[ "$OSTYPE" == "darwin"* ]]; then
        OS="macos"
        DISTRO="macos"
    else
        OS="unknown"
        DISTRO="unknown"
    fi
}

# Function to install tool
install_tool() {
    local tool=$1

    echo -e "${CYAN}Installing $tool...${NC}"

    case $tool in
        # Security Scanners
        checkov)
            pip3 install --user checkov
        ;;
        tfsec)
            if [[ "$OS" == "macos" ]]; then
                brew install tfsec
            else
                curl -s https://raw.githubusercontent.com/aquasecurity/tfsec/master/scripts/install_linux.sh | bash
            fi
        ;;
        terrascan)
            if [[ "$OS" == "macos" ]]; then
                brew install terrascan
            else
                curl -L "$(curl -s https://api.github.com/repos/tenable/terrascan/releases/latest | grep -o -E "https://.+?_Linux_x86_64.tar.gz")" > terrascan.tar.gz
                tar -xf terrascan.tar.gz terrascan
                sudo mv terrascan /usr/local/bin/
                rm terrascan.tar.gz
            fi
        ;;
        detect-secrets)
            pip3 install --user detect-secrets
        ;;
        gitleaks)
            if [[ "$OS" == "macos" ]]; then
                brew install gitleaks
            else
                curl -sSL "$(curl -s https://api.github.com/repos/zricethezav/gitleaks/releases/latest | grep -o -E "https://.+?linux_x64.tar.gz")" | tar -xz
                sudo mv gitleaks /usr/local/bin/
            fi
        ;;
        trufflehog)
            if [[ "$OS" == "macos" ]]; then
                brew install trufflehog
            else
                curl -sSL "$(curl -s https://api.github.com/repos/trufflesecurity/trufflehog/releases/latest | grep -o -E "https://.+?linux_amd64.tar.gz")" | tar -xz
                sudo mv trufflehog /usr/local/bin/
            fi
        ;;
        bandit)
            pip3 install --user bandit
        ;;
        semgrep)
            pip3 install --user semgrep
        ;;
        safety)
            pip3 install --user safety
        ;;

        # Terraform Tools
        terraform-fmt|terraform-validate)
            if ! command_exists terraform; then
                echo -e "${YELLOW}Installing Terraform...${NC}"
                if [[ "$OS" == "macos" ]]; then
                    brew tap hashicorp/tap
                    brew install hashicorp/tap/terraform
                else
                    wget -O- https://apt.releases.hashicorp.com/gpg | sudo gpg --dearmor -o /usr/share/keyrings/hashicorp-archive-keyring.gpg
                    echo "deb [signed-by=/usr/share/keyrings/hashicorp-archive-keyring.gpg] https://apt.releases.hashicorp.com $(lsb_release -cs) main" | sudo tee /etc/apt/sources.list.d/hashicorp.list
                    sudo apt update && sudo apt install terraform
                fi
            fi
        ;;
        terraform-docs)
            if [[ "$OS" == "macos" ]]; then
                brew install terraform-docs
            else
                curl -Lo ./terraform-docs.tar.gz "$(curl -s https://api.github.com/repos/terraform-docs/terraform-docs/releases/latest | grep -o -E "https://.+?linux-amd64.tar.gz")"
                tar -xzf terraform-docs.tar.gz
                sudo mv terraform-docs /usr/local/bin/
                rm terraform-docs.tar.gz
            fi
        ;;
        tflint)
            if [[ "$OS" == "macos" ]]; then
                brew install tflint
            else
                curl -s https://raw.githubusercontent.com/terraform-linters/tflint/master/install_linux.sh | bash
            fi
        ;;
        infracost)
            if [[ "$OS" == "macos" ]]; then
                brew install infracost
            else
                curl -fsSL https://raw.githubusercontent.com/infracost/infracost/master/scripts/install.sh | sh
            fi
        ;;
        terraform-compliance)
            pip3 install --user terraform-compliance
        ;;

        # Code Quality Tools
        black|isort|flake8|pylint|mypy)
            pip3 install --user "$tool"
        ;;
        prettier)
            npm install -g prettier
        ;;
        yamllint)
            pip3 install --user yamllint
        ;;
        markdownlint)
            npm install -g markdownlint-cli
        ;;
        shellcheck)
            if [[ "$OS" == "macos" ]]; then
                brew install shellcheck
            else
                sudo apt-get install -y shellcheck
            fi
        ;;
        hadolint)
            if [[ "$OS" == "macos" ]]; then
                brew install hadolint
            else
                wget -O /usr/local/bin/hadolint https://github.com/hadolint/hadolint/releases/latest/download/hadolint-Linux-x86_64
                chmod +x /usr/local/bin/hadolint
            fi
        ;;

        # Kubernetes Tools
        kubeval)
            if [[ "$OS" == "macos" ]]; then
                brew install kubeval
            else
                wget https://github.com/instrumenta/kubeval/releases/latest/download/kubeval-linux-amd64.tar.gz
                tar xf kubeval-linux-amd64.tar.gz
                sudo mv kubeval /usr/local/bin
                rm kubeval-linux-amd64.tar.gz
            fi
        ;;
        kubesec)
            if [[ "$OS" == "macos" ]]; then
                brew install kubesec
            else
                curl -sSL https://github.com/controlplaneio/kubesec/releases/download/v2.11.0/kubesec_linux_amd64.tar.gz | tar -xz
                sudo mv kubesec /usr/local/bin/
            fi
        ;;
        trivy)
            if [[ "$OS" == "macos" ]]; then
                brew install trivy
            else
                wget -qO - https://aquasecurity.github.io/trivy-repo/deb/public.key | sudo apt-key add -
                echo "deb https://aquasecurity.github.io/trivy-repo/deb $(lsb_release -sc) main" | sudo tee -a /etc/apt/sources.list.d/trivy.list
                sudo apt-get update
                sudo apt-get install -y trivy
            fi
        ;;
        *)
            echo -e "${YELLOW}⚠ Unknown tool: $tool${NC}"
        ;;
    esac

    if command_exists "$tool"; then
        echo -e "${GREEN}✓ $tool installed successfully${NC}"
    else
        echo -e "${YELLOW}⚠ $tool installation may require PATH update or restart${NC}"
    fi
}

# Function to create pre-commit config
create_precommit_config() {
    echo -e "\n${CYAN}Creating pre-commit configuration...${NC}"

    cat > .pre-commit-config.yaml << 'EOF'
# Auto-generated pre-commit configuration
# Customize based on your selected tools

repos:
  - repo: https://github.com/pre-commit/pre-commit-hooks
    rev: v4.5.0
    hooks:
      - id: trailing-whitespace
      - id: end-of-file-fixer
      - id: check-yaml
      - id: check-added-large-files
      - id: detect-private-key
EOF

    # Add selected tools to pre-commit
    if [[ " ${ENABLED_TOOLS[@]} " =~ " tfsec " ]]; then
        cat >> .pre-commit-config.yaml << 'EOF'

  - repo: https://github.com/antonbabenko/pre-commit-terraform
    rev: v1.86.0
    hooks:
      - id: terraform_tfsec
EOF
    fi

    if [[ " ${ENABLED_TOOLS[@]} " =~ " checkov " ]]; then
        cat >> .pre-commit-config.yaml << 'EOF'
      - id: terraform_checkov
EOF
    fi

    if [[ " ${ENABLED_TOOLS[@]} " =~ " detect-secrets " ]]; then
        cat >> .pre-commit-config.yaml << 'EOF'

  - repo: https://github.com/Yelp/detect-secrets
    rev: v1.4.0
    hooks:
      - id: detect-secrets
        args: ['--baseline', '.secrets.baseline']
EOF
    fi

    if [[ " ${ENABLED_TOOLS[@]} " =~ " gitleaks " ]]; then
        cat >> .pre-commit-config.yaml << 'EOF'

  - repo: https://github.com/zricethezav/gitleaks
    rev: v8.18.2
    hooks:
      - id: gitleaks
EOF
    fi

    echo -e "${GREEN}✓ Created .pre-commit-config.yaml${NC}"
}

# Function to show summary
show_summary() {
    echo -e "\n${CYAN}═══ INSTALLATION SUMMARY ═══${NC}\n"

    echo -e "${GREEN}Successfully configured tools:${NC}"
    for tool in "${ENABLED_TOOLS[@]}"; do
        if command_exists "$tool"; then
            echo -e "  ✓ $tool"
        else
            echo -e "  ⚠ $tool (may need PATH update)"
        fi
    done

    echo -e "\n${CYAN}Next Steps:${NC}"
    echo "1. Update your PATH if needed:"
    echo "   export PATH=\"\$HOME/.local/bin:\$PATH\""
    echo ""
    echo "2. Initialize pre-commit hooks:"
    echo "   pre-commit install"
    echo "   pre-commit run --all-files"
    echo ""
    echo "3. Run security scans:"
    for tool in "${ENABLED_TOOLS[@]}"; do
        case $tool in
            checkov)
                echo "   checkov -d ."
            ;;
            tfsec)
                echo "   tfsec ."
            ;;
            detect-secrets)
                echo "   detect-secrets scan"
            ;;
            gitleaks)
                echo "   gitleaks detect"
            ;;
            bandit)
                echo "   bandit -r ."
            ;;
        esac
    done
    echo ""
    echo -e "${GREEN}Security toolkit setup complete!${NC}"
}

# Main function
main() {
    detect_os

    # Parse command line arguments
    while [[ $# -gt 0 ]]; do
        case $1 in
            -h|--help)
                show_banner
                show_help
                exit 0
            ;;
            -i|--interactive)
                INSTALL_MODE="interactive"
                shift
            ;;
            -a|--all)
                INSTALL_MODE="all"
                shift
            ;;
            -p|--preset)
                INSTALL_MODE="preset"
                PRESET_MODE="$2"
                shift 2
            ;;
            -c|--config)
                load_config "$2"
                INSTALL_MODE="config"
                shift 2
            ;;
            -s|--save)
                SAVE_CONFIG="$2"
                shift 2
            ;;
            -l|--list)
                show_banner
                list_tools
                exit 0
            ;;
            -d|--dry-run)
                DRY_RUN=true
                shift
            ;;
            *)
                echo -e "${RED}Unknown option: $1${NC}"
                show_help
                exit 1
            ;;
        esac
    done

    # Show banner
    show_banner

    # Determine which tools to install
    case $INSTALL_MODE in
        interactive)
            interactive_selection
        ;;
        all)
            ENABLED_TOOLS=($(echo "${!SECURITY_TOOLS[@]}" "${!TERRAFORM_TOOLS[@]}" "${!CODE_QUALITY_TOOLS[@]}" "${!KUBERNETES_TOOLS[@]}" | tr ' ' '\n' | sort -u))
        ;;
        preset)
            load_preset "$PRESET_MODE"
        ;;
        config)
            # Tools already loaded from config
        ;;
    esac

    # Save configuration if requested
    if [[ -n "$SAVE_CONFIG" ]]; then
        save_config "$SAVE_CONFIG"
    fi

    # Show what will be installed
    echo -e "\n${CYAN}Tools to be installed:${NC}"
    for tool in "${ENABLED_TOOLS[@]}"; do
        echo "  • $tool"
    done

    # Dry run mode
    if [[ "$DRY_RUN" == true ]]; then
        echo -e "\n${YELLOW}DRY RUN MODE - No tools were installed${NC}"
        exit 0
    fi

    # Confirm installation
    echo ""
    read -p "Proceed with installation? [Y/n]: " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]] && [[ ! -z "$REPLY" ]]; then
        echo -e "${YELLOW}Installation cancelled${NC}"
        exit 0
    fi

    # Install prerequisites
    echo -e "\n${CYAN}Checking prerequisites...${NC}"

    if ! command_exists pip3; then
        echo -e "${YELLOW}Installing pip3...${NC}"
        if [[ "$OS" == "macos" ]]; then
            brew install python3
        else
            sudo apt-get update && sudo apt-get install -y python3-pip
        fi
    fi

    if ! command_exists npm; then
        echo -e "${YELLOW}Installing npm...${NC}"
        if [[ "$OS" == "macos" ]]; then
            brew install node
        else
            curl -fsSL https://deb.nodesource.com/setup_lts.x | sudo -E bash -
            sudo apt-get install -y nodejs
        fi
    fi

    # Install selected tools
    echo -e "\n${CYAN}Installing security tools...${NC}"
    for tool in "${ENABLED_TOOLS[@]}"; do
        if command_exists "$tool"; then
            echo -e "${GREEN}✓ $tool already installed${NC}"
        else
            install_tool "$tool"
        fi
    done

    # Create pre-commit configuration
    create_precommit_config

    # Show summary
    show_summary
}

# Run main function
main "$@"
