#!/bin/bash
# Companion code for "The Backend of Luck" - Chapter 45, Secure Infrastructure Decommissioning.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# Terraform Obliteration Script
# Complete destruction of all Terraform-managed infrastructure

set -euo pipefail

# Configuration
LOG_FILE="/var/log/terraform_obliterator.log"
BACKUP_DIR="/backup/terraform_$(date +%Y%m%d_%H%M%S)"
DRY_RUN=true

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# Logging
log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOG_FILE"
}

info() {
    echo -e "${BLUE}[INFO]${NC} $1"
    log "INFO: $1"
}

success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
    log "SUCCESS: $1"
}

error() {
    echo -e "${RED}[ERROR]${NC} $1" >&2
    log "ERROR: $1"
    exit 1
}

warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
    log "WARNING: $1"
}

# Validate prerequisites
validate_prerequisites() {
    info "Validating Terraform obliteration prerequisites..."
    
    # Check if terraform is installed
    if ! command -v terraform &> /dev/null; then
        error "Terraform is not installed or not in PATH"
    fi
    
    # Check Terraform version
    TF_VERSION=$(terraform version | head -n 1 | cut -d' ' -f2)
    info "Terraform version: $TF_VERSION"
    
    # Check AWS credentials
    if ! aws sts get-caller-identity &> /dev/null; then
        error "AWS credentials not configured"
    fi
    
    success "Prerequisites validation completed"
}

# Discover Terraform workspaces
discover_workspaces() {
    info "Discovering Terraform workspaces..."
    
    # Known account directories from documentation analysis
    ACCOUNTS=(
        "../infrastructure/infrastructure-live/dev"
        "../infrastructure/infrastructure-live/prod"
        "../infrastructure/infrastructure-live/security"
        "../infrastructure/infrastructure-live/shared"
        "../infrastructure/infrastructure-live/stage"
    )
    
    WORKSPACES=()
    
    for account_dir in "${ACCOUNTS[@]}"; do
        if [ -d "$account_dir" ]; then
            info "Scanning account: $account_dir"
            
            # Find all directories that might contain Terraform configs
            while IFS= read -r -d '' dir; do
                if [ -f "$dir/main.tf" ] || [ -f "$dir/terragrunt.hcl" ]; then
                    WORKSPACES+=("$dir")
                    info "Found workspace: $dir"
                fi
            done < <(find "$account_dir" -type d -print0)
        else
            warning "Account directory not found: $account_dir"
        fi
    done
    
    info "Discovered ${#WORKSPACES[@]} Terraform workspaces"
    echo "${WORKSPACES[@]}"
}

# Backup Terraform state (if requested)
backup_terraform_state() {
    local workspace="$1"
    
    if [ "$DRY_RUN" = true ]; then
        info "DRY RUN: Would backup Terraform state for $workspace"
        return
    fi
    
    info "Backing up Terraform state for $workspace"
    
    mkdir -p "$BACKUP_DIR"
    
    # Copy terraform.tfstate files
    find "$workspace" -name "terraform.tfstate*" -type f | while read -r state_file; do
        rel_path="${state_file#"$workspace"/}"
        backup_path="$BACKUP_DIR/${workspace##*/}/$rel_path"
        mkdir -p "$(dirname "$backup_path")"
        cp "$state_file" "$backup_path"
        info "Backed up: $state_file -> $backup_path"
    done
}

# Destroy single workspace
destroy_workspace() {
    local workspace="$1"
    local account_name
    
    account_name=$(basename "$(dirname "$workspace")")
    
    info "Destroying Terraform workspace: $workspace (Account: $account_name)"
    
    if [ "$DRY_RUN" = true ]; then
        info "DRY RUN: Would destroy workspace $workspace"
        return
    fi
    
    cd "$workspace" || error "Cannot change to workspace directory: $workspace"
    
    # Initialize Terraform (if needed)
    if [ -f "terragrunt.hcl" ]; then
        info "Using Terragrunt for workspace destruction"
        if ! terragrunt init -upgrade; then
            warning "Terragrunt init failed, attempting Terraform init"
            terraform init -upgrade || error "Failed to initialize Terraform in $workspace"
        fi
        
        # Get current workspace
        CURRENT_WS=$(terragrunt workspace show 2>/dev/null || echo "default")
        
        # Destroy with Terragrunt
        if ! terragrunt destroy -auto-approve; then
            error "Terragrunt destroy failed in $workspace"
        fi
    elif [ -f "main.tf" ]; then
        info "Using Terraform for workspace destruction"
        terraform init -upgrade || error "Failed to initialize Terraform in $workspace"
        
        # Get current workspace
        CURRENT_WS=$(terraform workspace show 2>/dev/null || echo "default")
        
        # Destroy with Terraform
        if ! terraform destroy -auto-approve; then
            error "Terraform destroy failed in $workspace"
        fi
    else
        warning "No main.tf or terragrunt.hcl found in $workspace"
        return
    fi
    
    success "Destroyed workspace: $workspace"
    log "DESTROYED: $workspace (Account: $account_name, Workspace: $CURRENT_WS)"
}

# Destroy all workspaces
destroy_all_workspaces() {
    local workspaces=("$@")
    
    warning "=== STARTING TERRAFORM INFRASTRUCTURE OBLITERATION ==="
    warning "This will permanently destroy ALL Terraform-managed infrastructure!"
    
    if [ "$DRY_RUN" = true ]; then
        warning "Running in DRY RUN mode - no actual destruction will occur"
    else
        echo "Type 'DESTROY ALL TERRAFORM INFRASTRUCTURE' to confirm:"
        read -r confirmation
        if [ "$confirmation" != "DESTROY ALL TERRAFORM INFRASTRUCTURE" ]; then
            info "Terraform obliteration cancelled"
            exit 0
        fi
    fi
    
    local total_workspaces=${#workspaces[@]}
    local destroyed_count=0
    
    for workspace in "${workspaces[@]}"; do
        echo "----------------------------------------"
        info "Processing workspace $((destroyed_count + 1))/$total_workspaces: $workspace"
        
        # Backup state first
        backup_terraform_state "$workspace"
        
        # Destroy workspace
        if destroy_workspace "$workspace"; then
            ((destroyed_count++))
        else
            error "Failed to destroy workspace: $workspace"
        fi
    done
    
    success "=== TERRAFORM OBLITERATION COMPLETED ==="
    success "Destroyed $destroyed_count/$total_workspaces workspaces"
    
    if [ "$DRY_RUN" = false ]; then
        log "OBLITERATION COMPLETE: Destroyed $destroyed_count workspaces"
    fi
}

# Verify destruction
verify_destruction() {
    local workspaces=("$@")
    
    info "Verifying Terraform destruction..."
    
    local remaining_resources=0
    
    for workspace in "${workspaces[@]}"; do
        if [ -d "$workspace" ]; then
            cd "$workspace" || continue
            
            # Check for remaining state
            if [ -f "terraform.tfstate" ]; then
                warning "State file still exists: $workspace/terraform.tfstate"
                ((remaining_resources++))
            fi
            
            # Check for remaining resources via Terraform
            if command -v terraform &> /dev/null; then
                if terraform state list &> /dev/null; then
                    resource_count=$(terraform state list | wc -l)
                    if [ "$resource_count" -gt 0 ]; then
                        warning "Workspace still has $resource_count resources: $workspace"
                        ((remaining_resources++))
                    fi
                fi
            fi
        fi
    done
    
    if [ $remaining_resources -eq 0 ]; then
        success "✓ Terraform destruction verification successful: No resources remaining"
        return 0
    else
        warning "⚠ Terraform destruction verification found $remaining_resources issues"
        return 1
    fi
}

# Generate report
generate_report() {
    local workspaces=("$@")
    local report_file
    report_file="terraform_obliteration_report_$(date +%Y%m%d_%H%M%S).json"
    
    info "Generating Terraform obliteration report..."
    
    cat > "$report_file" << EOF
{
  "generated_at": "$(date -Iseconds)",
  "dry_run": $DRY_RUN,
  "total_workspaces_discovered": ${#workspaces[@]},
  "backup_directory": "$BACKUP_DIR",
  "terraform_version": "$TF_VERSION",
  "workspaces_processed": [
EOF
    
    for i in "${!workspaces[@]}"; do
        workspace="${workspaces[$i]}"
        account_name=$(basename "$(dirname "$workspace")")
        
        cat >> "$report_file" << EOF
    {
      "index": $((i + 1)),
      "path": "$workspace",
      "account": "$account_name",
      "processed_at": "$(date -Iseconds)"
    }$(if [ "$i" -lt $((${#workspaces[@]} - 1)) ]; then echo ","; fi)
EOF
    done
    
    cat >> "$report_file" << EOF
  ],
  "log_file": "$LOG_FILE"
}
EOF
    
    success "Report generated: $report_file"
}

# Main execution
main() {
    # Parse arguments
    while [[ $# -gt 0 ]]; do
        case $1 in
            --no-dry-run)
                DRY_RUN=false
                warning "DANGER: Dry run disabled - actual destruction will occur!"
                shift
            ;;
            --backup-dir)
                BACKUP_DIR="$2"
                shift 2
            ;;
            --help)
                echo "Usage: $0 [--no-dry-run] [--backup-dir DIR]"
                echo ""
                echo "Options:"
                echo "  --no-dry-run    Disable dry run mode (actual destruction)"
                echo "  --backup-dir    Custom backup directory"
                echo "  --help          Show this help"
                exit 0
            ;;
            *)
                error "Unknown option: $1"
            ;;
        esac
    done
    
    # Validate prerequisites
    validate_prerequisites
    
    # Discover workspaces
    mapfile -t workspaces < <(discover_workspaces)
    
    if [ ${#workspaces[@]} -eq 0 ]; then
        error "No Terraform workspaces found"
    fi
    
    # Destroy all workspaces
    destroy_all_workspaces "${workspaces[@]}"
    
    # Verify destruction
    if verify_destruction "${workspaces[@]}"; then
        success "Terraform obliteration completed successfully"
    else
        warning "Terraform obliteration completed with verification warnings"
    fi
    
    # Generate report
    generate_report "${workspaces[@]}"
}

# Run main function
main "$@"