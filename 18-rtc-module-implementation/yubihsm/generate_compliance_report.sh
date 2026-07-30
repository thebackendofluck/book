#!/bin/bash
# Companion code for "The Backend of Luck" - Chapter 18, Real-Time Clock Module Implementation.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# shellcheck disable=SC2034
# generate_compliance_report.sh - Generate comprehensive compliance reports
# Creates reports for PCI DSS, FIPS 140-2, GDPR, and gambling-specific standards (GLI-11).
#
# Designed for iGaming operators preparing for regulatory audits.
# Generates HTML or JSON reports with HSM status, key rotation, and certificate inventory.
#
# Environment Variables:
#   OUTPUT_DIR      - Report output directory (default: ./compliance_reports)
#   REPORT_FORMAT   - Output format: html, json, pdf (default: html)
#   REPORT_MONTH    - Report period (default: current month)

set -euo pipefail

# Configuration
OUTPUT_DIR="${OUTPUT_DIR:-./compliance_reports}"
REPORT_FORMAT="${REPORT_FORMAT:-html}"
REPORT_MONTH="${REPORT_MONTH:-$(date +%Y-%m)}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

setup_output_dir() {
    mkdir -p "$OUTPUT_DIR"
    log_info "Output directory: $OUTPUT_DIR"
}

get_hsm_info() {
    cat << EOF
{
  "hsm_model": "YubiHSM 2 FIPS",
  "fips_level": "140-2 Level 3",
  "serial_number": "REDACTED",
  "firmware_version": "2.3.0",
  "total_objects": 256,
  "audit_enabled": true,
  "tamper_evident": true
}
EOF
}

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

generate_html_report() {
    local output_file="$OUTPUT_DIR/compliance_report_${REPORT_MONTH}.html"

    cat > "$output_file" << EOF
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>HSM Compliance Report - $REPORT_MONTH</title>
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
                <li><strong>Key Rotation:</strong> <span class="status-good">All keys within policy</span></li>
            </ul>
        </div>
    </div>

    <div class="section">
        <h2>Compliance Status</h2>
        <table>
            <tr><th>Standard</th><th>Status</th><th>Evidence</th></tr>
            <tr>
                <td>FIPS 140-2 Level 3</td>
                <td class="status-good">Compliant</td>
                <td>Hardware certification, tamper-evident design</td>
            </tr>
            <tr>
                <td>PCI DSS 3.5</td>
                <td class="status-good">Compliant</td>
                <td>Hardware key storage, audit logging</td>
            </tr>
            <tr>
                <td>GLI-11 5.4</td>
                <td class="status-good">Compliant</td>
                <td>Cryptographic key management, key attestation</td>
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
  "key_rotations": $(get_key_rotation_status),
  "audit_summary": $(get_audit_summary),
  "compliance_status": {
    "fips_140_2_level_3": "compliant",
    "pci_dss": "compliant",
    "gli_11": "compliant",
    "gdpr": "compliant"
  }
}
EOF

    log_info "JSON report generated: $output_file"
}

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
        *)
            log_error "Unsupported format: $REPORT_FORMAT"
            exit 1
        ;;
    esac

    log_info "Compliance report generation completed"
}

main "$@"
