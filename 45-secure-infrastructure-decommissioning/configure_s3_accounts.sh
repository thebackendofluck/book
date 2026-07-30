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
# Configure S3 Accounts for SDDS
# Sets up AWS and external S3 accounts for destruction
#

set -euo pipefail

CONFIG_FILE="config/s3_accounts.json"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;31m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Logging function
log() {
    local level="$1"
    local message="$2"
    local timestamp
    timestamp=$(date '+%Y-%m-%d %H:%M:%S')
    echo -e "${BLUE}[$timestamp]${NC} ${level:0:1}$message"
}

# Create config directory
mkdir -p "$(dirname "$CONFIG_FILE")"

# Initialize config file
cat > "$CONFIG_FILE" << 'EOF'
{
  "aws": [
    {
      "name": "prod",
      "access_key": "CHANGE_ME_AWS_ACCESS_KEY",
      "secret_key": "CHANGE_ME_AWS_SECRET_KEY",
      "regions": ["us-east-1", "us-west-2", "eu-west-1"]
    },
    {
      "name": "staging",
      "access_key": "CHANGE_ME_AWS_ACCESS_KEY",
      "secret_key": "CHANGE_ME_AWS_SECRET_KEY",
      "regions": ["us-east-1"]
    }
  ],
  "external": [
    {
      "name": "wasabi",
      "endpoint": "https://s3.wasabisys.com",
      "access_key": "CHANGE_ME_WASABI_ACCESS_KEY",
      "secret_key": "CHANGE_ME_WASABI_SECRET_KEY"
    },
    {
      "name": "backblaze",
      "endpoint": "https://s3.us-west-002.backblazeb2.com",
      "access_key": "CHANGE_ME_BACKBLAZE_ACCESS_KEY",
      "secret_key": "CHANGE_ME_BACKBLAZE_SECRET_KEY"
    }
  ]
}
EOF

log "INFO" "Created S3 accounts configuration template: $CONFIG_FILE"
echo -e "${YELLOW}WARNING: Please edit $CONFIG_FILE and replace all CHANGE_ME values with actual credentials${NC}"
echo -e "${YELLOW}WARNING: This configuration contains sensitive credentials. Secure this file appropriately.${NC}"

# Validate configuration
if command -v jq &> /dev/null; then
    if jq empty "$CONFIG_FILE" 2>/dev/null; then
        log "INFO" "Configuration file is valid JSON"
    else
        echo -e "${RED}ERROR: Configuration file contains invalid JSON${NC}"
        exit 1
    fi
else
    log "WARNING" "jq not installed - cannot validate JSON syntax"
fi

echo ""
echo "S3 Accounts Configuration:"
echo "=========================="
echo "AWS Accounts:"
jq -r '.aws[] | "- \(.name): \(.regions | join(", "))"' "$CONFIG_FILE" 2>/dev/null || echo "  (configure manually)"
echo ""
echo "External S3 Accounts:"
jq -r '.external[] | "- \(.name): \(.endpoint)"' "$CONFIG_FILE" 2>/dev/null || echo "  (configure manually)"

echo ""
echo -e "${GREEN}Configuration template created successfully!${NC}"
echo -e "${YELLOW}Next steps:${NC}"
echo "1. Edit $CONFIG_FILE with your actual credentials"
echo "2. Run: chmod 600 $CONFIG_FILE  # Secure permissions"
echo "3. Test with: ./s3_destroyer.sh --help"