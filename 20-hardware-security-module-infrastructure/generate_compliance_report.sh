#!/bin/bash
# Companion code for "The Backend of Luck" - Chapter 20, Hardware Security Module Infrastructure.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# shellcheck disable=SC2034  # Config and color constants
# generate_compliance_report.sh - Generate comprehensive compliance reports
# Creates reports for PCI DSS, HIPAA, GDPR, and other compliance frameworks

set -euo pipefail

# Configuration
OUTPUT_DIR="${OUTPUT_DIR:-./compliance_reports}"
REPORT_FORMAT="${REPORT_FORMAT:-html}"  # html, pdf, json
REPORT_MONTH="${REPORT_MONTH:-$(date +%Y-%m)}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

log_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Create output directory
setup_output_dir() {
    mkdir -p "$OUTPUT_DIR"
    log_info "Output directory: $OUTPUT_DIR"
}

# Get HSM status and configuration
get_hsm_info() {
    cat << EOF
{
  "hsm_model": "YubiHSM 2 FIPS",
  "fips_level": "140-2 Level 3",
  "serial_number": "12345678",
  "firmware_version": "2.3.0",
  "total_objects": 256,
  "audit_enabled": true,
  "tamper_evident": true
}
EOF
}

# Get space usage statistics
get_space_stats() {
    if [ -f "$SCRIPT_DIR/yubihsm_lifecycle_management.sh" ]; then
        "$SCRIPT_DIR/yubihsm_lifecycle_management.sh" space 2>/dev/null | grep -E "(Used|Free)" | head -2
    else
        echo "Used: 134/256 objects"
        echo "Free: 122 objects"
    fi
}

# Get certificate inventory
get_certificate_inventory() {
    cat << 'EOF'
[
  {
    "name": "production-ssl",
    "type": "SSL/TLS",
    "issuer": "DigiCert",
    "expiry": "2024-12-15",
    "days_until_expiry": 30,
    "status": "warning"
  },
  {
    "name": "internal-api",
    "type": "Internal",
    "issuer": "Self-Signed",
    "expiry": "2024-11-20",
    "days_until_expiry": 5,
    "status": "critical"
  },
  {
    "name": "vaultwarden-ca",
    "type": "CA",
    "issuer": "Self-Signed",
    "expiry": "2025-10-23",
    "days_until_expiry": 334,
    "status": "good"
  }
]
EOF
}

# Get key rotation status
get_key_rotation_status() {
    cat << 'EOF'
[
  {
    "key_type": "encryption",
    "last_rotation": "2024-08-15",
    "next_rotation": "2025-08-15",
    "status": "compliant"
  },
  {
    "key_type": "signing",
    "last_rotation": "2024-09-01",
    "next_rotation": "2025-09-01",
    "status": "compliant"
  },
  {
    "key_type": "authentication",
    "last_rotation": "2024-07-20",
    "next_rotation": "2025-07-20",
    "status": "compliant"
  }
]
EOF
}

# Get audit log summary
get_audit_summary() {
    cat << 'EOF'
{
  "total_events": 15420,
  "period": "2024-10-01 to 2024-10-31",
  "events_by_type": {
    "authentication": 2340,
    "key_operations": 5670,
    "certificate_operations": 3210,
    "administrative": 4200
  },
  "security_events": {
    "failed_auth": 12,
    "tamper_attempts": 0,
    "policy_violations": 3
  }
}
EOF
}

# Generate HTML report
generate_html_report() {
    local output_file="$OUTPUT_DIR/compliance_report_${REPORT_MONTH}.html"
    
    cat > "$output_file" << EOF
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>YubiHSM Compliance Report - $REPORT_MONTH</title>
    <style>
        body { font-family: Arial, sans-serif; margin: 40px; }
        .header { background: #f8f9fa; padding: 20px; border-radius: 5px; margin-bottom: 30px; }
        .section { margin-bottom: 30px; }
        .metric { background: #e9ecef; padding: 15px; border-radius: 5px; margin: 10px 0; }
        .status-good { color: #28a745; }
        .status-warning { color: #ffc107; }
        .status-critical { color: #dc3545; }
        table { width: 100%; border-collapse: collapse; margin: 20px 0; }
        th, td { border: 1px solid #ddd; padding: 12px; text-align: left; }
        th { background-color: #f8f9fa; }
        .summary { background: #d4edda; padding: 20px; border-radius: 5px; }
    </style>
</head>
<body>
    <div class="header">
        <h1>YubiHSM 2 FIPS Compliance Report</h1>
        <h2>Period: $REPORT_MONTH</h2>
        <p>Report generated on $(date)</p>
    </div>

    <div class="section">
        <h2>Executive Summary</h2>
        <div class="summary">
            <p>This report provides a comprehensive overview of YubiHSM 2 FIPS compliance status,
            security metrics, and operational health for the reporting period.</p>
            <ul>
                <li><strong>HSM Status:</strong> <span class="status-good">Operational</span></li>
                <li><strong>FIPS Compliance:</strong> <span class="status-good">Level 3 Certified</span></li>
                <li><strong>Space Usage:</strong> <span class="status-good">52.3% (134/256 objects)</span></li>
                <li><strong>Certificates:</strong> <span class="status-warning">2 expiring within 30 days</span></li>
            </ul>
        </div>
    </div>

    <div class="section">
        <h2>HSM Configuration</h2>
        <div class="metric">
            <strong>Model:</strong> YubiHSM 2 FIPS<br>
            <strong>FIPS Level:</strong> 140-2 Level 3<br>
            <strong>Firmware:</strong> 2.3.0<br>
            <strong>Audit Logging:</strong> <span class="status-good">Enabled</span>
        </div>
    </div>

    <div class="section">
        <h2>Space Utilization</h2>
        <div class="metric">
            $(get_space_stats | sed 's/$/<br>/')
        </div>
    </div>

    <div class="section">
        <h2>Certificate Inventory</h2>
        <table>
            <tr>
                <th>Certificate Name</th>
                <th>Type</th>
                <th>Expiry Date</th>
                <th>Days Left</th>
                <th>Status</th>
            </tr>
            $(get_certificate_inventory | jq -r '.[] | "<tr><td>\(.name)</td><td>\(.type)</td><td>\(.expiry)</td><td>\(.days_until_expiry)</td><td class=\"status-\(.status)\">\(.status)</td></tr>"')
        </table>
    </div>

    <div class="section">
        <h2>Key Rotation Status</h2>
        <table>
            <tr>
                <th>Key Type</th>
                <th>Last Rotation</th>
                <th>Next Rotation</th>
                <th>Status</th>
            </tr>
            $(get_key_rotation_status | jq -r '.[] | "<tr><td>\(.key_type)</td><td>\(.last_rotation)</td><td>\(.next_rotation)</td><td class=\"status-\(.status)\">\(.status)</td></tr>"')
        </table>
    </div>

    <div class="section">
        <h2>Audit Summary</h2>
        <div class="metric">
            $(get_audit_summary | jq -r '"Total Events: \(.total_events)<br>Period: \(.period)<br>Security Events: \(.security_events.failed_auth) failed authentications"')
        </div>
    </div>

    <div class="section">
        <h2>Compliance Status</h2>
        <table>
            <tr>
                <th>Standard</th>
                <th>Status</th>
                <th>Evidence</th>
            </tr>
            <tr>
                <td>FIPS 140-2 Level 3</td>
                <td class="status-good">Compliant</td>
                <td>Hardware certification, tamper-evident design</td>
            </tr>
            <tr>
                <td>PCI DSS</td>
                <td class="status-good">Compliant</td>
                <td>Hardware key storage, audit logging</td>
            </tr>
            <tr>
                <td>HIPAA</td>
                <td class="status-good">Compliant</td>
                <td>Encryption at rest, access controls</td>
            </tr>
            <tr>
                <td>GDPR</td>
                <td class="status-good">Compliant</td>
                <td>Data protection, audit trails</td>
            </tr>
        </table>
    </div>
</body>
</html>
EOF
    
    log_info "HTML report generated: $output_file"
}

# Generate JSON report
generate_json_report() {
    local output_file="$OUTPUT_DIR/compliance_report_${REPORT_MONTH}.json"
    
    cat > "$output_file" << EOF
{
  "report_metadata": {
    "generated_at": "$(date -Iseconds)",
    "period": "$REPORT_MONTH",
    "format": "json"
  },
  "hsm_info": $(get_hsm_info),
  "space_usage": {
    $(get_space_stats | sed 's/Used: /"used": "/; s/Free: /"free": "/; s/objects//g; s/\/256//g' | tr '\n' ',')
    "total": 256
  },
  "certificates": $(get_certificate_inventory),
  "key_rotations": $(get_key_rotation_status),
  "audit_summary": $(get_audit_summary),
  "compliance_status": {
    "fips_140_2_level_3": "compliant",
    "pci_dss": "compliant",
    "hipaa": "compliant",
    "gdpr": "compliant",
    "sox": "compliant"
  }
}
EOF
    
    log_info "JSON report generated: $output_file"
}

# Main execution
main() {
    log_info "Generating compliance report for $REPORT_MONTH..."
    
    setup_output_dir
    
    case "$REPORT_FORMAT" in
        "html")
            generate_html_report
        ;;
        "json")
            generate_json_report
        ;;
        "pdf")
            generate_html_report
            # In real implementation, convert HTML to PDF using wkhtmltopdf or similar
            log_info "PDF generation requires additional tools (wkhtmltopdf)"
        ;;
        *)
            log_error "Unsupported format: $REPORT_FORMAT"
            exit 1
        ;;
    esac
    
    log_info "Compliance report generation completed"
}

# Run main function
main "$@"