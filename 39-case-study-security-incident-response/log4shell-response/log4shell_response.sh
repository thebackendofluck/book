#!/usr/bin/env bash
# Companion code for "The Backend of Luck" - Chapter 39, Case Study.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# shellcheck disable=SC2034
# =============================================================================
# Log4Shell Emergency Response Toolkit for iGaming Platforms
# =============================================================================
#
# CONTEXT: When CVE-2021-44228 (Log4Shell) dropped on December 9, 2021,
# every Java-based iGaming backend became a potential target. This script
# was part of our emergency response toolkit, designed to:
#
#   1. Detect vulnerable Log4j versions across the platform
#   2. Apply immediate mitigations (JVM flags, class removal)
#   3. Scan logs for evidence of exploitation attempts
#   4. Generate a compliance report for regulators
#
# In a gambling platform, the attack surface is enormous: game servers,
# payment processors, player management backends, CRM integrations,
# reporting tools -- anything running Java and logging user input.
#
# SANITIZATION: All IPs use RFC 5737 ranges. All domains use example.com.
# Real server names and paths have been replaced.
# =============================================================================

set -euo pipefail

# --- Configuration ---
LOG_DIR="/var/log/platform"
REPORT_DIR="/tmp/log4shell-report-$(date +%Y%m%d-%H%M%S)"
JAVA_APPS_DIR="/opt/platform"

# RFC 5737 documentation IPs (sanitized from production values)
CALLBACK_LISTENER="192.0.2.10"
SCANNER_HOST="198.51.100.5"

# Colors for terminal output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

mkdir -p "$REPORT_DIR"

# =============================================================================
# PHASE 1: DETECTION — Find all Log4j instances on the system
# =============================================================================
# In an iGaming platform, you typically have:
#   - Game integration services (Tomcat/Spring Boot)
#   - Payment gateway middleware
#   - Back-office admin tools
#   - Reporting/BI applications
#   - Player authentication services
# Each may bundle its own Log4j version in different locations.

detect_vulnerable_jars() {
    echo -e "${YELLOW}[PHASE 1] Scanning for Log4j JAR files...${NC}"

    local vuln_count=0
    local scan_results="$REPORT_DIR/vulnerable_jars.txt"

    # Search for log4j-core JARs (the vulnerable component)
    # log4j-api is NOT vulnerable on its own; only log4j-core contains
    # the JNDI lookup class (JndiLookup.class)
    find "$JAVA_APPS_DIR" -name "log4j-core-*.jar" 2>/dev/null | while read -r jar; do
        # Extract version from filename
        version=$(echo "$jar" | grep -oP 'log4j-core-\K[0-9.]+')

        # Vulnerable versions: 2.0-beta9 through 2.14.1
        # 2.15.0 has partial fix, 2.16.0 is the real fix, 2.17.0 addresses
        # remaining DoS vector (CVE-2021-45105)
        if [[ "$version" =~ ^2\.(0|[1-9]|1[0-4])\. ]]; then
            echo -e "${RED}  VULNERABLE: $jar (version $version)${NC}"
            echo "VULNERABLE|$jar|$version" >> "$scan_results"
            ((vuln_count++)) || true
        else
            echo -e "${GREEN}  OK: $jar (version $version)${NC}"
            echo "OK|$jar|$version" >> "$scan_results"
        fi
    done

    # Also check for log4j bundled inside uber-JARs (common in Spring Boot)
    # Game provider SDKs often ship as fat JARs with embedded dependencies
    echo -e "${YELLOW}  Checking inside uber-JARs (Spring Boot fat JARs)...${NC}"
    find "$JAVA_APPS_DIR" -name "*.jar" -size +10M 2>/dev/null | while read -r jar; do
        if unzip -l "$jar" 2>/dev/null | grep -q "log4j-core.*\.jar"; then
            nested=$(unzip -l "$jar" 2>/dev/null | grep "log4j-core.*\.jar" | awk '{print $NF}')
            echo -e "${RED}  EMBEDDED VULNERABLE: $jar contains $nested${NC}"
            echo "EMBEDDED|$jar|$nested" >> "$scan_results"
        fi
    done

    echo ""
    echo "  Results saved to: $scan_results"
}


# =============================================================================
# PHASE 2: IMMEDIATE MITIGATION — Apply JVM flag and remove JNDI class
# =============================================================================
# Two mitigation strategies, applied in parallel:
#
# Strategy A: Set JVM flag to disable JNDI lookups in Log4j
#   -Dlog4j2.formatMsgNoLookups=true
#   This works for Log4j 2.10.0+ but NOT for earlier versions.
#
# Strategy B: Delete the JndiLookup.class from the JAR
#   This is more aggressive but works for ALL vulnerable versions.
#   In a gambling platform under regulatory scrutiny, we chose BOTH.

apply_mitigations() {
    echo -e "${YELLOW}[PHASE 2] Applying emergency mitigations...${NC}"

    # Strategy A: Add JVM flag to all running Java processes
    echo "  Strategy A: Injecting JVM flag into service configurations..."

    # Typical iGaming service layout:
    #   /opt/platform/game-server/     -- game integration service
    #   /opt/platform/payment-gw/      -- payment gateway
    #   /opt/platform/player-mgmt/     -- player management API
    #   /opt/platform/backoffice/      -- admin panel

    local services=("game-server" "payment-gw" "player-mgmt" "backoffice" "reporting")

    for service in "${services[@]}"; do
        local config_file="$JAVA_APPS_DIR/$service/conf/jvm.options"
        if [[ -f "$config_file" ]]; then
            if ! grep -q "log4j2.formatMsgNoLookups" "$config_file"; then
                echo "-Dlog4j2.formatMsgNoLookups=true" >> "$config_file"
                echo -e "${GREEN}    Added JVM flag to $service${NC}"
            else
                echo "    JVM flag already present in $service"
            fi
        fi
    done

    # Strategy B: Remove JndiLookup.class from vulnerable JARs
    echo ""
    echo "  Strategy B: Removing JndiLookup.class from vulnerable JARs..."

    if [[ -f "$REPORT_DIR/vulnerable_jars.txt" ]]; then
        grep "^VULNERABLE" "$REPORT_DIR/vulnerable_jars.txt" | while IFS='|' read -r status jar version; do
            # Create backup before modifying
            cp "$jar" "${jar}.bak.$(date +%Y%m%d)"
            # Remove the dangerous class
            zip -q -d "$jar" "org/apache/logging/log4j/core/lookup/JndiLookup.class" 2>/dev/null
            echo -e "${GREEN}    Removed JndiLookup.class from $jar${NC}"
        done
    fi

    echo ""
    echo -e "${YELLOW}  NOTE: Services must be restarted for mitigations to take effect.${NC}"
    echo "  In a live gambling environment, coordinate restarts with operations"
    echo "  to minimize player impact. Use rolling restarts behind the load balancer."
}


# =============================================================================
# PHASE 3: LOG FORENSICS — Search for exploitation attempts
# =============================================================================
# After mitigating, we need to determine if we were already compromised.
# Log4Shell exploitation attempts leave distinctive patterns in access logs.
#
# In gambling platforms, attackers inject JNDI payloads via:
#   - HTTP headers (User-Agent, Referer, X-Forwarded-For)
#   - API request bodies (player registration, game launch params)
#   - WebSocket messages (live casino communication)
#   - URL parameters (affiliate tracking codes)

scan_logs_for_exploitation() {
    echo -e "${YELLOW}[PHASE 3] Scanning logs for exploitation attempts...${NC}"

    local ioc_report="$REPORT_DIR/exploitation_attempts.txt"
    local log_files=(
        "$LOG_DIR/access_log"
        "$LOG_DIR/ssl_access.log"
        "$LOG_DIR/game-server/application.log"
        "$LOG_DIR/payment-gw/transactions.log"
        "$LOG_DIR/modsec_audit.log"
    )

    # JNDI injection patterns to search for
    # These cover both direct payloads and WAF bypass attempts
    local patterns=(
        '\$\{jndi:'                              # Basic JNDI lookup
        '\$\{.*j.*n.*d.*i.*:'                    # Obfuscated variants
        '\$\{\$\{lower:j'                        # Nested lower-case bypass
        '\$\{\$\{::-j'                           # Character-by-character bypass
        'jndi:ldap://'                           # Direct LDAP reference
        'jndi:rmi://'                            # RMI variant
        'jndi:dns://'                            # DNS variant
        '\$\{env:'                               # Environment variable leak
        '\$\{sys:'                               # System property leak
        'JndiLookup'                             # Class reference in errors
    )

    echo "  Searching across ${#log_files[@]} log sources..."

    for log_file in "${log_files[@]}"; do
        if [[ -f "$log_file" ]]; then
            echo ""
            echo "  --- $log_file ---"

            for pattern in "${patterns[@]}"; do
                local matches
                matches=$(grep -ciP "$pattern" "$log_file" 2>/dev/null || echo "0")
                if [[ "$matches" -gt 0 ]]; then
                    echo -e "${RED}    ALERT: $matches matches for pattern: $pattern${NC}"
                    grep -nP "$pattern" "$log_file" 2>/dev/null | head -5 >> "$ioc_report"
                    echo "---" >> "$ioc_report"
                fi
            done
        else
            echo "  SKIP: $log_file (not found)"
        fi
    done

    # Check ModSecurity audit logs for blocked attempts
    # A well-configured ModSec will have blocked some attacks, but
    # obfuscated payloads may have slipped through
    if [[ -f "$LOG_DIR/modsec_audit.log" ]]; then
        echo ""
        echo "  --- ModSecurity Audit Analysis ---"
        local modsec_blocks
        modsec_blocks=$(grep -c "jndi" "$LOG_DIR/modsec_audit.log" 2>/dev/null || echo "0")
        echo "  ModSecurity JNDI-related entries: $modsec_blocks"
    fi

    echo ""
    echo "  Full IoC report: $ioc_report"
}


# =============================================================================
# PHASE 4: NMAP VERIFICATION SCAN — Confirm vulnerability status
# =============================================================================
# After applying mitigations, verify that services are no longer vulnerable.
# Uses the companion log4shell_scanner.nse script.

verify_mitigations() {
    echo -e "${YELLOW}[PHASE 4] Verification scan with Nmap...${NC}"

    local nmap_report="$REPORT_DIR/nmap_verification.txt"

    # Typical iGaming service ports
    local target_ports="8080,8443,443,8009,9090,8888"

    # Internal network ranges (sanitized)
    local targets=(
        "198.51.100.10"    # Game server cluster
        "198.51.100.20"    # Payment gateway
        "198.51.100.30"    # Player management API
        "198.51.100.40"    # Back-office admin
        "203.0.113.10"     # Reporting service
    )

    for target in "${targets[@]}"; do
        echo "  Scanning $target..."
        nmap --script log4shell_scanner.nse \
            --script-args "log4shell_scanner.callback-server=$CALLBACK_LISTENER:1389" \
            -p "$target_ports" \
            "$target" \
            -oN "$nmap_report.$target" 2>/dev/null || true
    done

    echo ""
    echo "  Nmap verification results saved to: $REPORT_DIR/nmap_verification.*"
}


# =============================================================================
# PHASE 5: REGULATORY REPORT — Generate compliance documentation
# =============================================================================
# Gambling regulators (MGA, UKGC, etc.) require formal notification of
# security incidents. This generates the technical portion of that report.

generate_regulatory_report() {
    echo -e "${YELLOW}[PHASE 5] Generating regulatory compliance report...${NC}"

    local report_file="$REPORT_DIR/regulatory_report.txt"

    cat > "$report_file" << 'REPORT'
============================================================
SECURITY INCIDENT REPORT: CVE-2021-44228 (Log4Shell)
============================================================

1. VULNERABILITY SUMMARY
   CVE ID:        CVE-2021-44228
   CVSS Score:    10.0 (Critical)
   Component:     Apache Log4j 2.x (Java logging library)
   Attack Vector: Remote Code Execution via JNDI injection

2. PLATFORM IMPACT ASSESSMENT
   [Populated by detection scan results]
   - Affected services: See vulnerable_jars.txt
   - Exploitation evidence: See exploitation_attempts.txt
   - Player data exposure: [PENDING FORENSIC ANALYSIS]

3. TIMELINE
   - CVE Published: 2021-12-09
   - Detection scan initiated: [TIMESTAMP]
   - Mitigations applied: [TIMESTAMP]
   - Verification completed: [TIMESTAMP]

4. MITIGATIONS APPLIED
   a) JVM flag: -Dlog4j2.formatMsgNoLookups=true
   b) JndiLookup.class removed from all vulnerable JARs
   c) WAF rules updated to block JNDI patterns
   d) Network egress rules tightened (block outbound LDAP/RMI)

5. REMEDIATION PLAN
   - Immediate: Upgrade all Log4j instances to 2.17.1+
   - 48 hours: Full forensic analysis of affected systems
   - 7 days:   Third-party penetration test to verify remediation
   - 30 days:  Comprehensive dependency audit across all services

6. PLAYER IMPACT
   [To be completed after forensic analysis]

7. REGULATORY NOTIFICATIONS
   - [Jurisdiction]: Notified via [channel] on [date]
REPORT

    echo "  Report generated: $report_file"
    echo ""
    echo -e "${GREEN}=== Log4Shell Response Complete ===${NC}"
    echo "  All results saved to: $REPORT_DIR/"
    echo ""
    echo "  Next steps:"
    echo "    1. Review exploitation_attempts.txt for evidence of compromise"
    echo "    2. Coordinate service restarts with operations team"
    echo "    3. Complete regulatory_report.txt with forensic findings"
    echo "    4. Schedule Log4j upgrades to 2.17.1+ within 48 hours"
}


# =============================================================================
# MAIN EXECUTION
# =============================================================================

main() {
    echo "============================================================"
    echo "  Log4Shell Emergency Response — iGaming Platform"
    echo "  $(date)"
    echo "============================================================"
    echo ""

    detect_vulnerable_jars
    echo ""
    apply_mitigations
    echo ""
    scan_logs_for_exploitation
    echo ""
    verify_mitigations
    echo ""
    generate_regulatory_report
}

main "$@"
