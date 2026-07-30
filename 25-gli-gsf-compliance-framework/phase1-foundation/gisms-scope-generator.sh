#!/usr/bin/env bash
# Companion code for "The Backend of Luck" - Chapter 25, GLI-GSF Compliance Framework.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

###############################################################################
# gisms-scope-generator.sh
#
# Interactive GISMS (Gaming Information Security Management System) scope
# document generator for GLI-GSF-1 compliance.
#
# GLI-GSF-1, Section 2.2 requires a documented GIS policy that defines:
#   - The scope of the GISMS
#   - The Gaming Production Environment (GPE) boundaries
#   - Roles and responsibilities (GIS Officer, Compliance Officer, GIS Forum)
#   - Policy review cadence (minimum annual)
#
# Usage:
#   chmod +x gisms-scope-generator.sh
#   ./gisms-scope-generator.sh
#
# Output:
#   ./output/GISMS-Scope-<ORG>-<DATE>.md
#   ./output/GISMS-Policy-<ORG>-<DATE>.md
###############################################################################

set -euo pipefail

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
VERSION="1.0.0"
OUTPUT_DIR="./output"
TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
DATE_STAMP=$(date +"%Y-%m-%d")

# ANSI colors for interactive prompts
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
log_info()  { echo -e "${GREEN}[INFO]${NC}  $*"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
log_error() { echo -e "${RED}[ERROR]${NC} $*" >&2; }
log_step()  { echo -e "${CYAN}[STEP]${NC}  $*"; }

# ---------------------------------------------------------------------------
# Helper: prompt with default
# ---------------------------------------------------------------------------
prompt() {
    local var_name="$1"
    local prompt_text="$2"
    local default="${3:-}"
    local response

    if [[ -n "$default" ]]; then
        read -rp "$(echo -e "${CYAN}?${NC} ${prompt_text} [${default}]: ")" response
        eval "$var_name='${response:-$default}'"
    else
        read -rp "$(echo -e "${CYAN}?${NC} ${prompt_text}: ")" response
        while [[ -z "$response" ]]; do
            log_warn "This field is required."
            read -rp "$(echo -e "${CYAN}?${NC} ${prompt_text}: ")" response
        done
        eval "$var_name='$response'"
    fi
}

prompt_yn() {
    local var_name="$1"
    local prompt_text="$2"
    local default="${3:-y}"
    local response

    read -rp "$(echo -e "${CYAN}?${NC} ${prompt_text} [${default}]: ")" response
    response="${response:-$default}"
    if [[ "${response,,}" == "y" || "${response,,}" == "yes" ]]; then
        eval "$var_name=true"
    else
        eval "$var_name=false"
    fi
}

prompt_multi() {
    local var_name="$1"
    local prompt_text="$2"
    local items=()

    echo -e "${CYAN}?${NC} ${prompt_text} (enter one per line, empty line to finish):"
    while true; do
        read -rp "  > " item
        [[ -z "$item" ]] && break
        items+=("$item")
    done

    # Join with ||| delimiter for later splitting
    local IFS='|||'
    eval "$var_name='${items[*]:-}'"
}

# ---------------------------------------------------------------------------
# Banner
# ---------------------------------------------------------------------------
echo ""
echo "============================================================"
echo "  GISMS Scope & Policy Document Generator v${VERSION}"
echo "  GLI-GSF-1 Compliance Tool"
echo "============================================================"
echo ""
echo "  This tool generates two documents required for GLI-GSF-1"
echo "  Section 2.2 compliance:"
echo ""
echo "    1. GISMS Scope Document - defines GPE boundaries and CSCs"
echo "    2. GIS Policy Document  - security policy with roles"
echo ""
echo "  All outputs are saved to: ${OUTPUT_DIR}/"
echo ""
echo "============================================================"
echo ""

# ---------------------------------------------------------------------------
# Section 1: Organization Information
# ---------------------------------------------------------------------------
log_step "Section 1/7: Organization Information"
echo ""

prompt ORG_NAME "Organization (legal entity) name"
prompt ORG_TRADING "Trading name (if different)" "$ORG_NAME"
prompt ORG_JURISDICTION "Primary licensing jurisdiction (e.g., MGA, UKGC, DGE)"
prompt ORG_LICENSE_NUM "License number (or 'pending')" "pending"
prompt ORG_DOMAIN "Primary gaming domain (e.g., acmetocasino.com)"

echo ""

# ---------------------------------------------------------------------------
# Section 2: Gaming Implementation Group (GIG)
# ---------------------------------------------------------------------------
log_step "Section 2/7: Gaming Implementation Group (GIG) Classification"
echo ""
echo "  GIG1: Basic hygiene (small operator, limited products)"
echo "  GIG2: Complex operations (multiple products, moderate volume)"
echo "  GIG3: Advanced environment (online gaming, high volume)"
echo ""

prompt GIG_LEVEL "Select GIG level (1/2/3)" "3"

case "$GIG_LEVEL" in
    1) GIG_DESC="Basic Hygiene - limited gaming products, small player base" ;;
    2) GIG_DESC="Complex Operations - multiple gaming products, moderate transaction volume" ;;
    3) GIG_DESC="Advanced Environment - online gaming platform with high transaction volume" ;;
    *)
        log_error "Invalid GIG level. Must be 1, 2, or 3."
        exit 1
        ;;
esac

echo ""

# ---------------------------------------------------------------------------
# Section 3: Gaming Production Environment (GPE) Boundaries
# ---------------------------------------------------------------------------
log_step "Section 3/7: Gaming Production Environment (GPE) Definition"
echo ""

prompt_yn GPE_CLOUD "Does the GPE include cloud infrastructure (AWS/GCP/Azure)?"
if [[ "$GPE_CLOUD" == "true" ]]; then
    prompt GPE_CLOUD_PROVIDER "Cloud provider(s) (comma-separated)" "AWS"
    prompt GPE_CLOUD_REGIONS "Cloud region(s) (comma-separated)" "eu-west-1"
fi

prompt_yn GPE_ONPREM "Does the GPE include on-premises infrastructure?"
if [[ "$GPE_ONPREM" == "true" ]]; then
    prompt GPE_DC_LOCATION "Data center location(s) (comma-separated)"
fi

prompt_yn GPE_HYBRID "Does the GPE span land-based and online operations?"

prompt_multi GPE_NETWORK_SEGMENTS "List network segments/VLANs in the GPE"

echo ""

# ---------------------------------------------------------------------------
# Section 4: Critical System Components (CSCs)
# ---------------------------------------------------------------------------
log_step "Section 4/7: Critical System Component (CSC) Categories"
echo ""
echo "  GLI-GSF-1 Section 1.3 requires a full inventory of CSCs."
echo "  Indicate which categories are present in your GPE."
echo ""

prompt_yn CSC_RNG "RNG (Random Number Generator) systems?"
prompt_yn CSC_GAME_SERVERS "Game logic / game engine servers?"
prompt_yn CSC_PAYMENT "Payment gateway / processing systems?"
prompt_yn CSC_PLAYER_DB "Player account / identity databases?"
prompt_yn CSC_BONUS "Bonus engine / promotional systems?"
prompt_yn CSC_SPORTSBOOK "Sportsbook / odds engine?"
prompt_yn CSC_BACKOFFICE "Back-office administration systems?"
prompt_yn CSC_CRM "CRM / player communication systems?"
prompt_yn CSC_AML "AML / transaction monitoring systems?"
prompt_yn CSC_CDN "CDN / content delivery infrastructure?"
prompt_yn CSC_MOBILE "Mobile application backends?"
prompt_yn CSC_RTC "Real-time communication (WebSocket/RTC) servers?"

# Count CSCs
CSC_COUNT=0
for csc_var in CSC_RNG CSC_GAME_SERVERS CSC_PAYMENT CSC_PLAYER_DB CSC_BONUS \
               CSC_SPORTSBOOK CSC_BACKOFFICE CSC_CRM CSC_AML CSC_CDN \
               CSC_MOBILE CSC_RTC; do
    if [[ "${!csc_var}" == "true" ]]; then
        ((CSC_COUNT++))
    fi
done

echo ""
log_info "Identified ${CSC_COUNT} CSC categories for the GPE."
echo ""

# ---------------------------------------------------------------------------
# Section 5: Roles and Governance
# ---------------------------------------------------------------------------
log_step "Section 5/7: GIS Governance Structure"
echo ""

prompt GIS_OFFICER "GIS Officer name (or 'TBD')" "TBD"
prompt GIS_OFFICER_TITLE "GIS Officer title" "Head of Information Security"
prompt COMPLIANCE_OFFICER "Compliance Officer name (or 'TBD')" "TBD"
prompt GIS_FORUM_FREQ "GIS Forum meeting frequency (minimum: 6x/year)" "monthly"
prompt GIS_FORUM_MEMBERS "Number of GIS Forum members" "5"

echo ""

# ---------------------------------------------------------------------------
# Section 6: Third-Party / Vendor Scope
# ---------------------------------------------------------------------------
log_step "Section 6/7: Third-Party Vendor Scope (GLI-GSF-3)"
echo ""

prompt VENDOR_COUNT "Approximate number of third-party vendors with GPE access" "10"
prompt_multi VENDOR_CATEGORIES "List vendor categories (e.g., 'Game Provider', 'Payment Processor')"

echo ""

# ---------------------------------------------------------------------------
# Section 7: Compliance Timeline
# ---------------------------------------------------------------------------
log_step "Section 7/7: Compliance Timeline"
echo ""

prompt LAUNCH_DATE "Planned launch date or operational start (YYYY-MM-DD)" "$DATE_STAMP"
prompt INITIAL_ASSESSMENT_TARGET "Target date for initial GLI-GSF assessment" ""
prompt POLICY_REVIEW_DATE "Next policy review date" ""

echo ""

# ---------------------------------------------------------------------------
# Generate Output
# ---------------------------------------------------------------------------
mkdir -p "$OUTPUT_DIR"

SAFE_ORG=$(echo "$ORG_NAME" | tr ' ' '-' | tr '[:upper:]' '[:lower:]')
SCOPE_FILE="${OUTPUT_DIR}/GISMS-Scope-${SAFE_ORG}-${DATE_STAMP}.md"
POLICY_FILE="${OUTPUT_DIR}/GISMS-Policy-${SAFE_ORG}-${DATE_STAMP}.md"

log_info "Generating GISMS Scope Document..."

# ---- SCOPE DOCUMENT ----
cat > "$SCOPE_FILE" << SCOPE_EOF
# GISMS Scope Document

| Field | Value |
|-------|-------|
| **Organization** | ${ORG_NAME} |
| **Trading Name** | ${ORG_TRADING} |
| **Document Version** | 1.0 |
| **Date** | ${DATE_STAMP} |
| **Classification** | CONFIDENTIAL |
| **GLI-GSF Reference** | GLI-GSF-1, Section 2.2 |
| **GIG Level** | GIG${GIG_LEVEL} - ${GIG_DESC} |

## 1. Purpose

This document defines the scope of the Gaming Information Security Management
System (GISMS) for ${ORG_NAME}, as required by GLI-GSF-1 Section 2.2. It
establishes the boundaries of the Gaming Production Environment (GPE) and
identifies all Critical System Components (CSCs) subject to GLI-GSF controls.

## 2. Gaming Production Environment (GPE) Boundaries

### 2.1 Infrastructure Overview

| Component | In Scope | Details |
|-----------|----------|---------|
| Cloud Infrastructure | $([ "$GPE_CLOUD" == "true" ] && echo "Yes" || echo "No") | $([ "$GPE_CLOUD" == "true" ] && echo "Provider: ${GPE_CLOUD_PROVIDER}, Regions: ${GPE_CLOUD_REGIONS}" || echo "N/A") |
| On-Premises Infrastructure | $([ "$GPE_ONPREM" == "true" ] && echo "Yes" || echo "No") | $([ "$GPE_ONPREM" == "true" ] && echo "Location: ${GPE_DC_LOCATION}" || echo "N/A") |
| Hybrid (Land-based + Online) | $([ "$GPE_HYBRID" == "true" ] && echo "Yes" || echo "No") | $([ "$GPE_HYBRID" == "true" ] && echo "GLI-GSF-4 also applies" || echo "Online only") |

### 2.2 Network Segments

The following network segments are within the GPE boundary:

SCOPE_EOF

# Write network segments
IFS='|||' read -ra SEGMENTS <<< "$GPE_NETWORK_SEGMENTS"
for seg in "${SEGMENTS[@]}"; do
    [[ -n "$seg" ]] && echo "- ${seg}" >> "$SCOPE_FILE"
done

cat >> "$SCOPE_FILE" << SCOPE_EOF2

### 2.3 Exclusions

The following are explicitly out of scope for the GISMS:

- Corporate IT systems not connected to the GPE
- Marketing websites without player account functionality
- Development and staging environments (covered by separate SDLC controls)
- Third-party vendor internal infrastructure (governed by GLI-GSF-3 vendor controls)

## 3. Critical System Components (CSCs)

GLI-GSF-1 Section 1.3 requires identification and classification of all CSCs.
The following CSC categories are present in the GPE:

| # | CSC Category | Present | Risk Classification | OGIS Domain |
|---|-------------|---------|--------------------| ------------|
| 1 | RNG Systems | $([ "$CSC_RNG" == "true" ] && echo "Yes" || echo "No") | **Critical** | OGIS-1 |
| 2 | Game Logic Servers | $([ "$CSC_GAME_SERVERS" == "true" ] && echo "Yes" || echo "No") | **Critical** | OGIS-1, OGIS-3 |
| 3 | Payment Gateways | $([ "$CSC_PAYMENT" == "true" ] && echo "Yes" || echo "No") | **Critical** | OGIS-2 |
| 4 | Player Databases | $([ "$CSC_PLAYER_DB" == "true" ] && echo "Yes" || echo "No") | **High** | OGIS-2, OGIS-3 |
| 5 | Bonus Engine | $([ "$CSC_BONUS" == "true" ] && echo "Yes" || echo "No") | **High** | OGIS-3 |
| 6 | Sportsbook/Odds Engine | $([ "$CSC_SPORTSBOOK" == "true" ] && echo "Yes" || echo "No") | **Critical** | OGIS-1, OGIS-3 |
| 7 | Back-Office Systems | $([ "$CSC_BACKOFFICE" == "true" ] && echo "Yes" || echo "No") | **High** | OGIS-2 |
| 8 | CRM Systems | $([ "$CSC_CRM" == "true" ] && echo "Yes" || echo "No") | **Medium** | OGIS-2 |
| 9 | AML/Monitoring Systems | $([ "$CSC_AML" == "true" ] && echo "Yes" || echo "No") | **High** | OGIS-3 |
| 10 | CDN Infrastructure | $([ "$CSC_CDN" == "true" ] && echo "Yes" || echo "No") | **Medium** | OGIS-5 |
| 11 | Mobile Backends | $([ "$CSC_MOBILE" == "true" ] && echo "Yes" || echo "No") | **High** | OGIS-4 |
| 12 | RTC/WebSocket Servers | $([ "$CSC_RTC" == "true" ] && echo "Yes" || echo "No") | **High** | OGIS-4, OGIS-5 |

**Total CSC Categories in Scope: ${CSC_COUNT}**

> **Note:** RNG infrastructure is always classified as Critical risk per
> GLI-GSF-1. Individual CSC instances within each category must be documented
> in the CSC Inventory Register (see \`csc-inventory.py\`).

## 4. Applicable GLI-GSF Documents

| Document | Applicable | Rationale |
|----------|-----------|-----------|
| GLI-GSF-1 (Common Controls) | **Yes** | Foundation for all gaming enterprises |
| GLI-GSF-2 (GTS Assessment) | **Yes** | Technical security assessment requirements |
| GLI-GSF-3 (Vendor Risk) | $([ "$VENDOR_COUNT" -gt 0 ] && echo "**Yes**" || echo "No") | ${VENDOR_COUNT} third-party vendors identified |
| GLI-GSF-4 (Land-Based) | $([ "$GPE_HYBRID" == "true" ] && echo "**Yes**" || echo "No") | $([ "$GPE_HYBRID" == "true" ] && echo "Hybrid land-based + online operations" || echo "Online-only operations") |
| GLI-GSF-5 (Online/OGIS) | **Yes** | Online gaming platform in scope |

## 5. Compliance Timeline

| Milestone | Target Date |
|-----------|------------|
| GISMS Scope Approval | ${DATE_STAMP} |
| Operational Launch | ${LAUNCH_DATE} |
| Initial GLI-GSF Assessment | ${INITIAL_ASSESSMENT_TARGET} |
| Assessment Report Submission | Within 90 days of assessment |
| First Annual Assessment | Within 12 months of initial |
| Policy Review | ${POLICY_REVIEW_DATE} |

## 6. Document Control

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | ${DATE_STAMP} | ${GIS_OFFICER} | Initial scope definition |

---

*This document was generated by gisms-scope-generator.sh v${VERSION}*
*Generation timestamp: ${TIMESTAMP}*
*This document must be reviewed and approved by the GIS Officer and GIS Forum.*
SCOPE_EOF2

log_info "Scope document saved to: ${SCOPE_FILE}"

# ---- POLICY DOCUMENT ----
log_info "Generating GIS Policy Document..."

cat > "$POLICY_FILE" << POLICY_EOF
# Gaming Information Security (GIS) Policy

| Field | Value |
|-------|-------|
| **Organization** | ${ORG_NAME} |
| **Document ID** | GIS-POL-001 |
| **Version** | 1.0 |
| **Effective Date** | ${DATE_STAMP} |
| **Review Date** | ${POLICY_REVIEW_DATE} |
| **Classification** | CONFIDENTIAL |
| **Owner** | ${GIS_OFFICER}, ${GIS_OFFICER_TITLE} |
| **GLI-GSF Reference** | GLI-GSF-1, Section 2.2 |

## 1. Policy Statement

${ORG_NAME} (trading as "${ORG_TRADING}") is committed to protecting the
confidentiality, integrity, availability, and accountability of all information
assets within its Gaming Production Environment (GPE). This policy establishes
the Gaming Information Security Management System (GISMS) in accordance with
GLI-GSF-1 requirements and applicable regulatory obligations under the
${ORG_JURISDICTION} licensing framework.

## 2. Scope

This policy applies to:

- All Critical System Components (CSCs) identified in the GISMS Scope Document
- All personnel with access to the GPE (employees, contractors, vendors)
- All processes that create, store, transmit, or process gaming data
- All third-party vendors with access to the GPE (governed by GLI-GSF-3)

The GPE boundaries and CSC inventory are defined in the companion GISMS Scope
Document (GISMS-Scope-${SAFE_ORG}-${DATE_STAMP}.md).

## 3. Governance Structure

### 3.1 GIS Officer

| Field | Value |
|-------|-------|
| **Name** | ${GIS_OFFICER} |
| **Title** | ${GIS_OFFICER_TITLE} |
| **Reporting Line** | Board of Directors / CEO |

The GIS Officer is responsible for:
- Establishing, implementing, and maintaining the GISMS
- Reporting security posture to the GIS Forum and Board of Directors
- Coordinating with the ISF during GLI-GSF assessments
- Approving security exceptions with documented risk acceptance
- Ensuring incident response capability meets GLI-GSF-1 thresholds

### 3.2 Compliance Officer

| Field | Value |
|-------|-------|
| **Name** | ${COMPLIANCE_OFFICER} |
| **Reporting Line** | Board of Directors / CEO |

The Compliance Officer is responsible for:
- Regulatory liaison with ${ORG_JURISDICTION} licensing authority
- Ensuring GLI-GSF assessment reports are submitted within 90-day windows
- Coordinating vendor compliance under GLI-GSF-3
- Maintaining the compliance calendar and certification tracker

### 3.3 GIS Forum

| Field | Value |
|-------|-------|
| **Meeting Frequency** | ${GIS_FORUM_FREQ} (minimum 6x/year per GLI-GSF-1) |
| **Members** | ${GIS_FORUM_MEMBERS} |
| **Chair** | GIS Officer |

The GIS Forum is responsible for:
- Reviewing security metrics, incidents, and risk posture
- Approving changes to the GISMS scope and security policies
- Reviewing and accepting residual risks
- Approving the annual security budget and resource allocation
- Reviewing GTS assessment findings and remediation progress

**Mandatory agenda items per GLI-GSF-1:**
1. Security incident review (any incident since last meeting)
2. Risk register updates
3. Compliance status (assessment findings, remediation progress)
4. Policy change proposals
5. Vendor security updates (GLI-GSF-3)

## 4. Information Security Objectives

1. **Integrity** - Ensure all Critical Control Programs (RNG, game logic,
   payout calculation) maintain cryptographic integrity with 24-hour
   verification cycles (OGIS-1)
2. **Confidentiality** - Protect player data, financial records, and
   proprietary game logic from unauthorized disclosure
3. **Availability** - Maintain platform availability SLAs with documented
   DDoS protection and business continuity plans (OGIS-5)
4. **Accountability** - Maintain comprehensive audit trails with NTP-
   synchronized timestamps, retained for minimum five years

## 5. Risk Management

### 5.1 Risk Assessment

Risk assessments shall be conducted:
- Annually, as part of the GISMS review cycle
- Upon any significant change to the GPE
- When new CSCs are introduced or existing CSCs are materially modified
- When new threats or vulnerabilities are identified affecting gaming systems

Risk assessment methodology: CVSS v3.1 scoring with ISO 31010 risk treatment
framework, as documented in the Risk Assessment Procedure (see \`risk-assessment.py\`).

### 5.2 Risk Treatment

| Risk Level | CVSS Score | Treatment Timeline | Approval Required |
|-----------|-----------|-------------------|-------------------|
| Critical | 9.0-10.0 | 24 hours | GIS Officer + Board |
| High | 7.0-8.9 | 7 days | GIS Officer |
| Medium | 4.0-6.9 | 30 days | Security Team Lead |
| Low | 0.1-3.9 | Next quarterly cycle | Documented acceptance |

## 6. Access Control

- Multi-Factor Authentication (MFA) is mandatory for all administrative
  accounts with 100% coverage (OGIS-2)
- Role-Based Access Control (RBAC) with quarterly access reviews
- Segregation of duties enforced with automated conflict detection
- Vendor access requires approval workflow, time-limited tokens, and
  session recording (GLI-GSF-3)
- Emergency vendor access revocation within 5 minutes, tested monthly

## 7. Incident Management

A GIS incident is defined as any breach of confidentiality, integrity,
availability, or accountability of information within the GPE.

**Incident classification thresholds (GLI-GSF-1):**
- Any system outage exceeding 15 minutes
- Any unauthorized access to CSCs
- Any signature verification mismatch on Critical Control Programs
- Any data breach affecting player personal or financial data

**Notification requirements:**
- Critical incidents: Immediate notification to GIS Officer and regulatory body
- Significant incidents: Regulatory notification within 30 days
- All incidents: Documented with root cause analysis within 7 days

## 8. Record Retention

All security records, including but not limited to audit logs, incident
reports, access reviews, assessment results, meeting minutes, and policy
versions, shall be retained for a minimum of **five (5) years** in
accordance with GLI-GSF-1 requirements.

Records must be exportable in CSV, JSON, and XML formats for regulatory
and ISF access.

## 9. CIS Controls Adoption

${ORG_NAME} adopts CIS Controls v8.1 as the baseline security control
framework, mapped to GLI-GSF-1 Appendix A requirements (see
\`cis-controls-mapper.py\` for the complete mapping).

## 10. Compliance and Audit

### 10.1 Internal Audit

- Quarterly internal security assessments
- Annual policy review and GISMS scope validation
- Monthly vendor access revocation testing (GLI-GSF-3)

### 10.2 External Assessment (GLI-GSF-2 GTS)

- Initial assessment within 90 days of operational launch
- Annual GTS assessment by a qualified ISF
- Quarterly vulnerability scans (internal + external)
- Post-change assessments for critical GPE modifications

### 10.3 ISF Qualifications

The Independent Security Firm must hold:
- Current CISSP, CISA, OSCP, CEH, or GPEN certifications
- Minimum 5 years gaming industry experience
- Independence from the Gaming Enterprise

## 11. Policy Review

This policy shall be reviewed:
- At least annually
- After any significant security incident
- When regulatory requirements change
- When the GPE scope changes materially

**Next scheduled review: ${POLICY_REVIEW_DATE}**

## 12. Document Control

| Version | Date | Author | Approved By | Changes |
|---------|------|--------|-------------|---------|
| 1.0 | ${DATE_STAMP} | ${GIS_OFFICER} | GIS Forum | Initial policy |

## 13. Appendices

- **Appendix A:** GISMS Scope Document
- **Appendix B:** CSC Inventory Register (generated by \`csc-inventory.py\`)
- **Appendix C:** Risk Assessment Report (generated by \`risk-assessment.py\`)
- **Appendix D:** CIS Controls Mapping (generated by \`cis-controls-mapper.py\`)
- **Appendix E:** Vendor Risk Register (GLI-GSF-3)

---

*This document was generated by gisms-scope-generator.sh v${VERSION}*
*Generation timestamp: ${TIMESTAMP}*
*This policy must be approved by the GIS Forum and Board of Directors.*
POLICY_EOF

log_info "Policy document saved to: ${POLICY_FILE}"

echo ""
echo "============================================================"
echo "  Generation Complete"
echo "============================================================"
echo ""
echo "  Files created:"
echo "    - ${SCOPE_FILE}"
echo "    - ${POLICY_FILE}"
echo ""
echo "  Next steps:"
echo "    1. Review both documents with the GIS Officer"
echo "    2. Present to the GIS Forum for approval"
echo "    3. Run csc-inventory.py to populate the CSC register"
echo "    4. Run risk-assessment.py for initial risk assessment"
echo "    5. Store approved versions with 5-year retention"
echo ""
echo "============================================================"
