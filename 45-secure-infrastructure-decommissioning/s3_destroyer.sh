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

#
# S3 Data Destroyer for Secure Data Destruction System
# Uses s5cmd for high-performance parallel S3 object destruction
#

set -euo pipefail

# Simulation by default, matching terraform_obliterator.sh in this directory.
# This deletes every object and every bucket in every configured account, and
# nothing here is recoverable, so it must be asked for explicitly.
DRY_RUN="${DRY_RUN:-true}"

# Configuration
LOG_FILE="/var/log/s3_destroyer.log"
CONFIG_FILE="config/s3_accounts.json"
PARALLEL_JOBS=50

# Colors for output
RED='\033[0;31m'
# shellcheck disable=SC2034  # palette kept complete for consistency across ch45
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Logging function
log() {
    local level="$1"
    local message="$2"
    local timestamp
    timestamp=$(date '+%Y-%m-%d %H:%M:%S')
    echo "[$timestamp] [$level] $message" >> "$LOG_FILE"
    echo -e "${BLUE}[$timestamp]${NC} ${level:0:1}$message"
}

# Error handling
error_exit() {
    local message="$1"
    log "ERROR" "$message"
    echo -e "${RED}ERROR: $message${NC}" >&2
    exit 1
}

# Check if s5cmd is installed
check_s5cmd() {
    if ! command -v s5cmd &> /dev/null; then
        error_exit "s5cmd is not installed. Install from: https://github.com/peak/s5cmd"
    fi
    log "INFO" "s5cmd version: $(s5cmd version)"
}

# Load S3 account configurations
load_s3_accounts() {
    if [[ ! -f "$CONFIG_FILE" ]]; then
        log "WARNING" "S3 accounts config not found: $CONFIG_FILE"
        echo "[]" > "$CONFIG_FILE"
    fi

    # Load AWS accounts
    AWS_ACCOUNTS=$(jq -r '.aws[]? | @base64' "$CONFIG_FILE" 2>/dev/null || echo "")

    # Load external S3 accounts (Wasabi, etc.)
    EXTERNAL_ACCOUNTS=$(jq -r '.external[]? | @base64' "$CONFIG_FILE" 2>/dev/null || echo "")
}

# Discover all S3 buckets across accounts
discover_buckets() {
    log "INFO" "Discovering S3 buckets across all configured accounts"

    BUCKETS_FILE="/tmp/s3_buckets_discovery.txt"
    > "$BUCKETS_FILE"

    # AWS accounts
    if [[ -n "$AWS_ACCOUNTS" ]]; then
        while IFS= read -r account_data; do
            if [[ -n "$account_data" ]]; then
                local account=$(echo "$account_data" | base64 --decode | jq -r '.name')
                local access_key=$(echo "$account_data" | base64 --decode | jq -r '.access_key')
                local secret_key=$(echo "$account_data" | base64 --decode | jq -r '.secret_key')
                local regions=$(echo "$account_data" | base64 --decode | jq -r '.regions // ["us-east-1"] | join(" ")')

                log "INFO" "Discovering buckets for AWS account: $account"

                for region in $regions; do
                    export AWS_ACCESS_KEY_ID="$access_key"
                    export AWS_SECRET_ACCESS_KEY="$secret_key"
                    export AWS_DEFAULT_REGION="$region"

                    # List buckets in this region
                    aws s3api list-buckets --region "$region" \
                        --query 'Buckets[].Name' \
                        --output text 2>/dev/null | \
                    while read -r bucket; do
                        if [[ -n "$bucket" ]]; then
                            echo "aws|$account|$region|$bucket" >> "$BUCKETS_FILE"
                        fi
                    done
                done
            fi
        done <<< "$AWS_ACCOUNTS"
    fi

    # External S3 accounts (Wasabi, etc.)
    if [[ -n "$EXTERNAL_ACCOUNTS" ]]; then
        while IFS= read -r account_data; do
            if [[ -n "$account_data" ]]; then
                local name=$(echo "$account_data" | base64 --decode | jq -r '.name')
                local endpoint=$(echo "$account_data" | base64 --decode | jq -r '.endpoint')
                local access_key=$(echo "$account_data" | base64 --decode | jq -r '.access_key')
                local secret_key=$(echo "$account_data" | base64 --decode | jq -r '.secret_key')

                log "INFO" "Discovering buckets for external S3 account: $name ($endpoint)"

                export AWS_ACCESS_KEY_ID="$access_key"
                export AWS_SECRET_ACCESS_KEY="$secret_key"

                # List buckets from external provider
                s5cmd --endpoint-url "$endpoint" ls s3:// 2>/dev/null | \
                awk '{print $4}' | sed 's|s3://||' | \
                while read -r bucket; do
                    if [[ -n "$bucket" ]]; then
                        echo "external|$name|$endpoint|$bucket" >> "$BUCKETS_FILE"
                    fi
                done
            fi
        done <<< "$EXTERNAL_ACCOUNTS"
    fi

    local bucket_count=$(wc -l < "$BUCKETS_FILE")
    log "INFO" "Discovered $bucket_count S3 buckets"
}

# Cryptographically erase S3 objects
crypto_erase_s3_objects() {
    local provider="$1"
    local account="$2"
    local region_or_endpoint="$3"
    local bucket="$4"

    log "INFO" "Starting cryptographic erasure of s3://$bucket"

    # Create temporary directory for operations
    local temp_dir="/tmp/s3_erase_$bucket"
    mkdir -p "$temp_dir"

    # Set credentials based on provider
    if [[ "$provider" == "aws" ]]; then
        export AWS_ACCESS_KEY_ID=$(jq -r ".aws[] | select(.name==\"$account\") | .access_key" "$CONFIG_FILE")
        export AWS_SECRET_ACCESS_KEY=$(jq -r ".aws[] | select(.name==\"$account\") | .secret_key" "$CONFIG_FILE")
        export AWS_DEFAULT_REGION="$region_or_endpoint"
        local endpoint=""
    else
        export AWS_ACCESS_KEY_ID=$(jq -r ".external[] | select(.name==\"$account\") | .access_key" "$CONFIG_FILE")
        export AWS_SECRET_ACCESS_KEY=$(jq -r ".external[] | select(.name==\"$account\") | .secret_key" "$CONFIG_FILE")
        local endpoint="--endpoint-url $region_or_endpoint"
    fi

    # Step 1: List all objects (including versions)
    log "INFO" "Listing all objects in $bucket"
    s5cmd $endpoint ls "s3://$bucket/" --recursive --show-etag > "$temp_dir/objects.txt" 2>/dev/null || true

    # Step 2: Generate cryptographic overwrite data
    local object_count=$(wc -l < "$temp_dir/objects.txt")
    log "INFO" "Found $object_count objects to erase in $bucket"

    if [[ $object_count -eq 0 ]]; then
        log "INFO" "Bucket $bucket is empty, skipping"
        rm -rf "$temp_dir"
        return
    fi

    # Step 3: Parallel cryptographic overwrite
    log "WARNING" "Starting cryptographic overwrite of $object_count objects"

    # Create overwrite script for parallel execution
    cat > "$temp_dir/overwrite.sh" << 'EOF'
#!/bin/bash
OBJECT_KEY="$1"
BUCKET="$2"
ENDPOINT="$3"

# Generate random data the same size as the object
# This is a simplified version - in production, you'd get actual object size
RANDOM_SIZE=$((RANDOM % 1048576 + 1024))  # 1KB to 1MB random size

# Create random data file
dd if=/dev/urandom of="/tmp/random_data_$OBJECT_KEY" bs=1 count=$RANDOM_SIZE 2>/dev/null

# Overwrite the object with random data
s5cmd $ENDPOINT cp "/tmp/random_data_$OBJECT_KEY" "s3://$BUCKET/$OBJECT_KEY" --concurrency 1

# Clean up
rm -f "/tmp/random_data_$OBJECT_KEY"

echo "Overwritten: $OBJECT_KEY"
EOF

    chmod +x "$temp_dir/overwrite.sh"

    # Execute parallel overwrite
    cat "$temp_dir/objects.txt" | \
    awk '{print $4}' | sed 's|s3://[^/]*/||' | \
    xargs -n 1 -P "$PARALLEL_JOBS" -I {} bash "$temp_dir/overwrite.sh" "{}" "$bucket" "$endpoint"

    # Step 4: Delete all objects
    log "INFO" "Deleting all objects from $bucket"
    s5cmd $endpoint rm "s3://$bucket/*" --recursive --concurrency "$PARALLEL_JOBS"

    # Step 5: Delete all versions (if versioning enabled)
    log "INFO" "Deleting all versions from $bucket"
    s5cmd $endpoint rm "s3://$bucket/*" --recursive --all-versions --concurrency "$PARALLEL_JOBS" 2>/dev/null || true

    # Step 6: Delete the bucket itself
    log "INFO" "Deleting bucket $bucket"
    if [[ "$provider" == "aws" ]]; then
        aws s3 rb "s3://$bucket" --force 2>/dev/null || true
    else
        # For external providers, try s5cmd delete bucket
        s5cmd $endpoint rb "s3://$bucket" 2>/dev/null || true
    fi

    # Clean up
    rm -rf "$temp_dir"

    log "INFO" "Cryptographic erasure complete for bucket: $bucket"
}

# Main destruction function
destroy_all_s3() {
    local crypto_erase="$1"

    log "WARNING" "=== STARTING S3 DESTRUCTION SEQUENCE ==="
    log "INFO" "Cryptographic erase: $crypto_erase"

    # Process each bucket
    while IFS='|' read -r provider account region_or_endpoint bucket; do
        if [[ -n "$bucket" && "$bucket" != "s3://" ]]; then
            log "INFO" "Processing bucket: $bucket (Provider: $provider, Account: $account)"

            if [[ "$crypto_erase" == "true" ]]; then
                crypto_erase_s3_objects "$provider" "$account" "$region_or_endpoint" "$bucket"
            else
                # Simple delete without cryptographic overwrite
                if [[ "$provider" == "aws" ]]; then
                    export AWS_ACCESS_KEY_ID=$(jq -r ".aws[] | select(.name==\"$account\") | .access_key" "$CONFIG_FILE")
                    export AWS_SECRET_ACCESS_KEY=$(jq -r ".aws[] | select(.name==\"$account\") | .secret_key" "$CONFIG_FILE")
                    export AWS_DEFAULT_REGION="$region_or_endpoint"

                    aws s3 rb "s3://$bucket" --force 2>/dev/null || true
                else
                    export AWS_ACCESS_KEY_ID=$(jq -r ".external[] | select(.name==\"$account\") | .access_key" "$CONFIG_FILE")
                    export AWS_SECRET_ACCESS_KEY=$(jq -r ".external[] | select(.name==\"$account\") | .secret_key" "$CONFIG_FILE")

                    s5cmd --endpoint-url "$region_or_endpoint" rb "s3://$bucket" 2>/dev/null || true
                fi
                log "INFO" "Deleted bucket: $bucket"
            fi
        fi
    done < "$BUCKETS_FILE"

    log "WARNING" "=== S3 DESTRUCTION SEQUENCE COMPLETE ==="
}

# Parse command line arguments
parse_args() {
    CRYPTO_ERASE=false
    ALL_BUCKETS=true
    ENDPOINT=""

    while [[ $# -gt 0 ]]; do
        case $1 in
            --crypto-erase)
                CRYPTO_ERASE=true
                ;;
            --endpoint)
                ENDPOINT="$2"
                shift
                ;;
            --parallel)
                # Already using parallel by default
                ;;
            --help)
                show_help
                exit 0
                ;;
            *)
                error_exit "Unknown option: $1"
                ;;
        esac
        shift
    done
}

show_help() {
    cat << EOF
S3 Data Destroyer for SDDS

USAGE:
    ./s3_destroyer.sh [OPTIONS]

OPTIONS:
    --crypto-erase        Use cryptographic erasure (overwrite with random data)
    --endpoint URL        S3 endpoint URL for external providers (e.g., Wasabi)
    --parallel           Use parallel processing (default)
    --help               Show this help message

EXAMPLES:
    # Destroy all S3 buckets with cryptographic erasure
    ./s3_destroyer.sh --crypto-erase

    # Destroy Wasabi buckets
    ./s3_destroyer.sh --endpoint https://s3.wasabisys.com --crypto-erase

CONFIGURATION:
    Configure S3 accounts in config/s3_accounts.json:
    {
      "aws": [
        {
          "name": "prod",
          "access_key": "AKIA...",
          "secret_key": "...",
          "regions": ["us-east-1", "us-west-2"]
        }
      ],
      "external": [
        {
          "name": "wasabi",
          "endpoint": "https://s3.wasabisys.com",
          "access_key": "...",
          "secret_key": "..."
        }
      ]
    }

WARNING:
    This will permanently delete all objects and buckets.
    Use --crypto-erase for secure data destruction.
EOF
}

# Main execution
main() {
    log "INFO" "S3 Destroyer started"

    check_s5cmd
    load_s3_accounts
    discover_buckets

    if [ "$DRY_RUN" != "false" ]; then
        echo -e "${YELLOW}[SIMULATED] No objects were deleted.${NC}"
        echo "  every bucket discovered above would be emptied and removed"
        echo "  in every account listed in ${CONFIG_FILE}"
        echo
        echo "To run this for real: DRY_RUN=false $0 ..."
        log "INFO" "Simulated run, nothing deleted"
        exit 0
    fi

    echo -e "${RED}This permanently deletes every object and bucket listed above.${NC}"
    echo "Type 'DESTROY ALL S3 DATA' to confirm:"
    read -r confirmation
    if [ "$confirmation" != "DESTROY ALL S3 DATA" ]; then
        log "INFO" "Destruction cancelled by user"
        echo "Cancelled."
        exit 0
    fi

    destroy_all_s3 "$CRYPTO_ERASE"

    log "INFO" "S3 Destroyer completed"
}

# Run the script
parse_args "$@"
main

exit 0