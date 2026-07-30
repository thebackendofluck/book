#!/usr/bin/env bash
# Companion code for "The Backend of Luck" - Chapter 24, Security and Compliance.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# cross-border-session-validator.sh
#
# Validate player sessions across jurisdictional borders.
#
# This script simulates the session validation checks that a multi-jurisdictional
# operator backend must perform when:
#   1. A player registers / logs in
#   2. A game session starts
#   3. A session is renewed or extended
#   4. A deposit or withdrawal is requested
#
# It checks:
#   - Whether the operator holds the required license for the player's detected country
#   - Whether geo-IP and VPN signals are consistent
#   - Whether the player's session country is consistent with their registered country
#   - Whether EU/EEA cross-border access (no EU passporting for gambling) is correctly denied
#   - Whether self-exclusion registry checks have been triggered
#   - Whether applicable deposit limits and session limits are enforced
#
# Exit codes:
#   0 = session valid, all checks passed
#   1 = session denied (licensing, exclusion, or geo mismatch)
#   2 = session flagged for review (expired exclusion, near-border, suspicious geo)
#   3 = configuration error
#
# Usage:
#   ./cross-border-session-validator.sh \
#       --player-id P12345 \
#       --registered-country SE \
#       --detected-country SE \
#       --held-licenses "MGA SE DK" \
#       --vpn-detected false \
#       --session-event login
#
#   ./cross-border-session-validator.sh --demo
#   ./cross-border-session-validator.sh --help

set -euo pipefail

# ---------------------------------------------------------------------------
# Colour helpers
# ---------------------------------------------------------------------------
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
RESET='\033[0m'

log_info()    { printf "${BLUE}[INFO]${RESET}  %s\n" "$*"; }
log_ok()      { printf "${GREEN}[PASS]${RESET}  %s\n" "$*"; }
log_warn()    { printf "${YELLOW}[WARN]${RESET}  %s\n" "$*"; }
log_error()   { printf "${RED}[DENY]${RESET}  %s\n" "$*"; }
log_section() { printf "\n${BOLD}${CYAN}==> %s${RESET}\n" "$*"; }

# ---------------------------------------------------------------------------
# Jurisdiction data — which license code is required per country ISO code
# ---------------------------------------------------------------------------
# Format: COUNTRY_CODE=LICENSE_CODE
declare -A LICENSE_REQUIREMENT=(
    [GB]=UKGC
    [SE]=SE
    [DK]=DK
    [IT]=IT
    [ES]=ES
    [FR]=FR
    [DE]=DE
    [NL]=NL
    [PT]=PT
    [GR]=GR
    [RO]=RO
    [BE]=BE
    [FI]=MONOPOLY
    [NO]=MONOPOLY
    [MT]=MGA
    [GI]=GI
    [IM]=GSC
    [AU]=AU
    [NZ]=NZ
    [BR]=BR
    [CO]=CO
    [ZA]=ZA
    [NG]=NG
    [KE]=KE
    [US]=US_STATE  # State-by-state; simplified here
    [CA]=CA_PROVINCE
    [JP]=PROHIBITED
    [AE]=PROHIBITED
    [SA]=PROHIBITED
    [IL]=RESTRICTED
    [CN]=PROHIBITED
)

# Countries that require local license regardless of any offshore license held.
# This associative array is declared for reference and potential extension;
# the logic is enforced via LICENSE_REQUIREMENT and operator_holds_license().
# SC2034: intentionally kept as reference data — disable warning.
# shellcheck disable=SC2034
declare -A REQUIRES_LOCAL_LICENSE=(
    [GB]=true
    [SE]=true
    [DK]=true
    [IT]=true
    [ES]=true
    [FR]=true
    [DE]=true
    [NL]=true
    [PT]=true
    [GR]=true
    [RO]=true
    [BE]=true
    [FI]=true
    [NO]=true
)

# Countries where EU passporting is NOT accepted (gambling exempt from Services Directive)
# In practice: ALL EU/EEA countries — listed here for explicit clarity
declare -A NO_EU_PASSPORT=(
    [GB]=true [SE]=true [DK]=true [IT]=true [ES]=true [FR]=true [DE]=true
    [NL]=true [PT]=true [GR]=true [RO]=true [BE]=true [FI]=true [NO]=true
    [AT]=true [PL]=true [CZ]=true [SK]=true [HU]=true [BG]=true [HR]=true
    [SI]=true [EE]=true [LV]=true [LT]=true [LU]=true [IE]=true [CY]=true
    [MT]=true
)

# Countries where the operator is completely prohibited from operating
declare -A PROHIBITED_COUNTRIES=(
    [JP]=true
    [AE]=true
    [SA]=true
    [CN]=true
    [KP]=true
    [IR]=true
    [AF]=true
    [YE]=true
)

# Self-exclusion registries per country
declare -A SELF_EXCLUSION_REGISTRY=(
    [GB]=GAMSTOP
    [SE]=Spelpaus
    [DK]=ROFUS
    [DE]=OASIS
    [NL]=Cruks
    [BE]=EPIS
    [ES]=RGIAJ
    [FR]=AUMS
    [MT]=ReSPONSe
)

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------
PLAYER_ID=""
REGISTERED_COUNTRY=""
DETECTED_COUNTRY=""
HELD_LICENSES=""
VPN_DETECTED="false"
SESSION_EVENT="login"
VERBOSE=false
RUN_DEMO=false
OUTPUT_JSON=false

# ---------------------------------------------------------------------------
# Usage
# ---------------------------------------------------------------------------
usage() {
    cat <<EOF
Usage: $0 [OPTIONS]

Cross-border player session validator for multi-jurisdictional iGaming operators.

Options:
  --player-id ID            Player identifier
  --registered-country CC   ISO 3166-1 alpha-2 country code from player's registration
  --detected-country CC     ISO 3166-1 alpha-2 country code from current geo-IP lookup
  --held-licenses "A B C"   Space-separated list of operator's license codes (e.g. "MGA SE UKGC")
  --vpn-detected true|false Whether VPN/proxy detected for current session (default: false)
  --session-event EVENT     login|deposit|game_start|session_renewal (default: login)
  --json                    Output final result as JSON
  --verbose                 Show detailed check output
  --demo                    Run demonstration scenarios
  --help                    Show this help message

License codes:  UKGC MGA GI GSC SE DK IT ES FR DE NL PT GR RO BE AU NZ BR CO ZA NG KE

Example:
  $0 --player-id P001 \\
     --registered-country SE \\
     --detected-country SE \\
     --held-licenses "MGA SE DK UKGC" \\
     --vpn-detected false \\
     --session-event login

EOF
    exit 0
}

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------
while [[ $# -gt 0 ]]; do
    case "$1" in
        --player-id)          PLAYER_ID="$2";            shift 2 ;;
        --registered-country) REGISTERED_COUNTRY="${2^^}"; shift 2 ;;
        --detected-country)   DETECTED_COUNTRY="${2^^}";   shift 2 ;;
        --held-licenses)      HELD_LICENSES="$2";         shift 2 ;;
        --vpn-detected)       VPN_DETECTED="${2,,}";      shift 2 ;;
        --session-event)      SESSION_EVENT="$2";         shift 2 ;;
        --json)               OUTPUT_JSON=true;           shift ;;
        --verbose)            VERBOSE=true; export VERBOSE; shift ;;
        --demo)               RUN_DEMO=true;              shift ;;
        --help|-h)            usage ;;
        *)
            printf "${RED}Unknown argument: %s${RESET}\n" "$1" >&2
            exit 3
            ;;
    esac
done

# ---------------------------------------------------------------------------
# Helper: check if operator holds required license for a country
# ---------------------------------------------------------------------------
operator_holds_license() {
    local country="$1"
    local required_code="${LICENSE_REQUIREMENT[$country]:-UNKNOWN}"

    if [[ "$required_code" == "UNKNOWN" ]]; then
        # No entry = unregulated / use offshore license permissible
        echo "PERMISSIBLE_OFFSHORE"
        return 0
    fi

    if [[ "$required_code" == "PROHIBITED" ]]; then
        echo "PROHIBITED"
        return 1
    fi

    if [[ "$required_code" == "MONOPOLY" ]]; then
        echo "MONOPOLY"
        return 1
    fi

    # Check if held licenses include the required code
    for held in $HELD_LICENSES; do
        if [[ "${held^^}" == "${required_code^^}" ]]; then
            echo "LICENSED"
            return 0
        fi
    done

    echo "MISSING_${required_code}"
    return 1
}

# ---------------------------------------------------------------------------
# Helper: check EU passporting
# ---------------------------------------------------------------------------
check_eu_passport() {
    local country="$1"
    if [[ -n "${NO_EU_PASSPORT[$country]+_}" ]]; then
        echo "NO_PASSPORT"
        return 1
    fi
    echo "PASSPORT_OK"
    return 0
}

# ---------------------------------------------------------------------------
# Session validation function
# ---------------------------------------------------------------------------
validate_session() {
    local player_id="$1"
    local registered_country="$2"
    local detected_country="$3"
    local held_licenses="$4"
    local vpn_detected="$5"
    local session_event="$6"

    local checks_passed=0
    local checks_failed=0
    local checks_warned=0
    local final_status="ALLOW"
    local denial_reason=""

    log_section "Session Validation: Player ${player_id} | Event: ${session_event}"
    log_info "Registered country:  ${registered_country}"
    log_info "Detected country:    ${detected_country}"
    log_info "Operator licenses:   ${held_licenses}"
    log_info "VPN detected:        ${vpn_detected}"

    # ------------------------------------------------------------------
    # CHECK 1: Prohibited country
    # ------------------------------------------------------------------
    log_section "CHECK 1: Prohibited jurisdiction"
    if [[ -n "${PROHIBITED_COUNTRIES[$detected_country]+_}" ]]; then
        log_error "Detected country ${detected_country} is completely PROHIBITED for online gambling."
        log_error "Session must be terminated. No licensing possible in this jurisdiction."
        final_status="DENY"
        denial_reason="Prohibited jurisdiction: ${detected_country}"
        ((checks_failed++))
    else
        log_ok "Detected country ${detected_country} is not in prohibited list."
        ((checks_passed++))
    fi

    # ------------------------------------------------------------------
    # CHECK 2: Licensing for detected (current) country
    # ------------------------------------------------------------------
    log_section "CHECK 2: Operator license for detected country (${detected_country})"
    license_status=$(operator_holds_license "$detected_country" || true)

    case "$license_status" in
        LICENSED)
            log_ok "Operator holds required license for ${detected_country}."
            ((checks_passed++))
            ;;
        PERMISSIBLE_OFFSHORE)
            log_ok "No local licensing requirement found for ${detected_country}. Offshore license permissible."
            ((checks_passed++))
            ;;
        MONOPOLY)
            log_error "${detected_country} operates a state gambling monopoly. Private operators are not permitted."
            log_error "Serving players in ${detected_country} without a state concession is illegal."
            if [[ "$final_status" != "DENY" ]]; then
                final_status="DENY"
                denial_reason="State monopoly jurisdiction: ${detected_country}"
            fi
            ((checks_failed++))
            ;;
        PROHIBITED)
            log_error "${detected_country} prohibits online gambling. No licensing available."
            if [[ "$final_status" != "DENY" ]]; then
                final_status="DENY"
                denial_reason="Prohibited jurisdiction: ${detected_country}"
            fi
            ((checks_failed++))
            ;;
        MISSING_*)
            required="${license_status#MISSING_}"
            log_error "Operator does NOT hold required ${required} license for ${detected_country}."
            log_error "EU passporting does NOT apply to gambling (excluded from Services Directive)."
            log_error "Serving ${detected_country} players without ${required} license = regulatory violation."
            if [[ "$final_status" != "DENY" ]]; then
                final_status="DENY"
                denial_reason="Missing license: ${required} required for ${detected_country}"
            fi
            ((checks_failed++))
            ;;
    esac

    # ------------------------------------------------------------------
    # CHECK 3: EU passporting — explicitly confirm it is not accepted
    # ------------------------------------------------------------------
    log_section "CHECK 3: EU passporting check for ${detected_country}"
    passport_result=$(check_eu_passport "$detected_country" || true)
    if [[ "$passport_result" == "NO_PASSPORT" ]]; then
        if [[ "$license_status" == "LICENSED" ]]; then
            log_ok "EU passport not applicable — operator holds correct local license. Check passed."
            ((checks_passed++))
        elif [[ "$license_status" == "PERMISSIBLE_OFFSHORE" ]]; then
            log_ok "EU passport check not required for ${detected_country} — no local licensing mandate."
            ((checks_passed++))
        else
            log_warn "EU passporting is NOT accepted for gambling in ${detected_country}."
            log_warn "An MGA, GI, or GSC license does not confer the right to serve ${detected_country} players."
            log_warn "Local ${license_status#MISSING_} license is mandatory regardless of any EU license held."
            ((checks_warned++))
        fi
    else
        log_ok "Passporting check passed for ${detected_country}."
        ((checks_passed++))
    fi

    # ------------------------------------------------------------------
    # CHECK 4: Country consistency (registered vs detected)
    # ------------------------------------------------------------------
    log_section "CHECK 4: Geo consistency (registered=${registered_country} detected=${detected_country})"
    if [[ "$registered_country" == "$detected_country" ]]; then
        log_ok "Player's registered country matches detected country."
        ((checks_passed++))
    else
        # Check if operator is licensed in BOTH countries
        reg_status=$(operator_holds_license "$registered_country" || true)
        det_status=$(operator_holds_license "$detected_country" || true)

        if [[ "$reg_status" == "LICENSED" || "$reg_status" == "PERMISSIBLE_OFFSHORE" ]] && \
           [[ "$det_status" == "LICENSED" || "$det_status" == "PERMISSIBLE_OFFSHORE" ]]; then
            log_warn "Country mismatch: registered in ${registered_country}, accessing from ${detected_country}."
            log_warn "Operator is licensed in both. Flag for KYC review if pattern persists."
            log_warn "Verify: legitimate travel vs. account sharing vs. geo-bypass."
            ((checks_warned++))
        else
            log_error "Country mismatch: registered in ${registered_country}, accessing from ${detected_country}."
            log_error "Operator is not licensed to serve one or both of these countries."
            if [[ "$final_status" != "DENY" ]]; then
                final_status="DENY"
                denial_reason="Country mismatch with unlicensed jurisdiction: reg=${registered_country} detected=${detected_country}"
            fi
            ((checks_failed++))
        fi
    fi

    # ------------------------------------------------------------------
    # CHECK 5: VPN / proxy detection
    # ------------------------------------------------------------------
    log_section "CHECK 5: VPN/proxy detection"
    if [[ "$vpn_detected" == "true" ]]; then
        log_warn "VPN or proxy detected for player ${player_id}."
        log_warn "Per UKGC RTS 13 and equivalent EU requirements, VPN use may indicate geo-bypass attempt."
        log_warn "Action: challenge player with additional geo-verification. Flag session for compliance review."
        log_warn "If geo-bypass confirmed: terminate session and file suspicious activity report."
        if [[ "$final_status" == "ALLOW" ]]; then
            final_status="FLAG"
        fi
        ((checks_warned++))
    else
        log_ok "No VPN or proxy detected."
        ((checks_passed++))
    fi

    # ------------------------------------------------------------------
    # CHECK 6: Self-exclusion registry availability
    # ------------------------------------------------------------------
    log_section "CHECK 6: Self-exclusion registry check requirement"
    if [[ -n "${SELF_EXCLUSION_REGISTRY[$detected_country]+_}" ]]; then
        registry="${SELF_EXCLUSION_REGISTRY[$detected_country]}"
        log_warn "Country ${detected_country} requires mandatory ${registry} self-exclusion check."
        log_warn "This script does NOT perform live registry lookups."
        log_warn "Backend MUST call ${registry} API before allowing session for ${session_event}."
        log_warn "Refer to: self-exclusion-registry.py for registry check simulation."
        ((checks_warned++))
    else
        log_ok "No mandatory self-exclusion registry check required for ${detected_country} in this configuration."
        ((checks_passed++))
    fi

    # ------------------------------------------------------------------
    # CHECK 7: Session-event specific rules
    # ------------------------------------------------------------------
    log_section "CHECK 7: Session-event specific compliance (${session_event})"
    case "$session_event" in
        login)
            log_info "Login event: GAMSTOP/registry check at every login required (UKGC, GGL, KSA)."
            log_info "Identity verification must be current and not expired."
            ((checks_passed++))
            ;;
        deposit)
            log_info "Deposit event: AML transaction monitoring required."
            log_info "Deposit limits (DE: €1,000/month; NL: €700/month first 30 days) must be enforced."
            log_info "Source of funds check may be required for deposits above jurisdiction threshold."
            if [[ "$detected_country" == "DE" ]]; then
                log_warn "Germany LUGAS: deposit counts toward cross-operator monthly €1,000 limit."
                log_warn "LUGAS API call required to verify player has not exceeded cross-operator limit."
                ((checks_warned++))
            else
                ((checks_passed++))
            fi
            ;;
        game_start)
            log_info "Game start: session time limits must be configured."
            if [[ "$detected_country" == "DE" ]]; then
                log_warn "Germany: max €1/spin, min 5-second spin interval, no autoplay on slots."
                ((checks_warned++))
            else
                ((checks_passed++))
            fi
            ;;
        session_renewal)
            log_info "Session renewal: geo-lease re-verification required."
            log_info "Renewed geo-check must confirm player is still in licensed jurisdiction."
            log_info "GAMSTOP/registry re-check not required at every renewal — check policy per jurisdiction."
            ((checks_passed++))
            ;;
        *)
            log_warn "Unknown session event: ${session_event}. Manual compliance review required."
            ((checks_warned++))
            ;;
    esac

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------
    log_section "Validation Summary"
    printf "${BOLD}Player:${RESET}       %s\n" "$player_id"
    printf "${BOLD}Event:${RESET}        %s\n" "$session_event"
    printf "${BOLD}Countries:${RESET}    registered=%s  detected=%s\n" "$registered_country" "$detected_country"
    printf "${BOLD}Checks:${RESET}       passed=%d  warned=%d  failed=%d\n" \
        "$checks_passed" "$checks_warned" "$checks_failed"

    case "$final_status" in
        ALLOW)
            printf '%b\n' "${GREEN}${BOLD}FINAL DECISION: SESSION ALLOWED${RESET}"
            ;;
        FLAG)
            printf '%b\n' "${YELLOW}${BOLD}FINAL DECISION: SESSION FLAGGED FOR REVIEW${RESET}"
            log_warn "Allow session but flag for compliance team investigation."
            ;;
        DENY)
            printf '%b\n' "${RED}${BOLD}FINAL DECISION: SESSION DENIED${RESET}"
            printf '%b%s%b\n' "${RED}" "Reason: ${denial_reason}" "${RESET}"
            ;;
    esac

    # JSON output if requested
    if [[ "$OUTPUT_JSON" == "true" ]]; then
        printf '\n{"player_id":"%s","event":"%s","registered":"%s","detected":"%s","vpn":%s,"decision":"%s","checks_passed":%d,"checks_warned":%d,"checks_failed":%d,"denial_reason":"%s"}\n' \
            "$player_id" "$session_event" "$registered_country" "$detected_country" \
            "$vpn_detected" "$final_status" "$checks_passed" "$checks_warned" \
            "$checks_failed" "${denial_reason:-none}"
    fi

    case "$final_status" in
        ALLOW) return 0 ;;
        FLAG)  return 2 ;;
        DENY)  return 1 ;;
    esac
}

# ---------------------------------------------------------------------------
# Demo scenarios
# ---------------------------------------------------------------------------
run_demo() {
    printf '\n%b\n' "${BOLD}${CYAN}====================================================${RESET}"
    printf '%b\n' "${BOLD}${CYAN}  Cross-Border Session Validator — Demo Scenarios  ${RESET}"
    printf '%b\n' "${BOLD}${CYAN}====================================================${RESET}"

    echo ""
    echo "SCENARIO 1: Swedish player accessing MGA-only operator (no SE license)"
    echo "  Expected: DENY — Swedish player requires Spelinspektionen license"
    echo ""
    validate_session "P_SE_001" "SE" "SE" "MGA GI" "false" "login" || true

    echo ""
    echo "===================================================================="
    echo "SCENARIO 2: UK player accessing UKGC-licensed operator"
    echo "  Expected: ALLOW — operator holds UKGC license"
    echo ""
    validate_session "P_GB_001" "GB" "GB" "MGA UKGC SE" "false" "login" || true

    echo ""
    echo "===================================================================="
    echo "SCENARIO 3: German player — operator holds GGL license"
    echo "  Expected: ALLOW with warnings (OASIS check required, LUGAS deposit tracking)"
    echo ""
    validate_session "P_DE_001" "DE" "DE" "MGA DE UKGC" "false" "deposit" || true

    echo ""
    echo "===================================================================="
    echo "SCENARIO 4: Player accessing from Norway (state monopoly)"
    echo "  Expected: DENY — Norway state monopoly"
    echo ""
    validate_session "P_NO_001" "NO" "NO" "MGA UKGC SE DK IT DE NL" "false" "login" || true

    echo ""
    echo "===================================================================="
    echo "SCENARIO 5: Player with VPN detected — registered SE, accessing from SE"
    echo "  Expected: FLAG — VPN triggers compliance review"
    echo ""
    validate_session "P_SE_002" "SE" "SE" "MGA SE UKGC" "true" "game_start" || true

    echo ""
    echo "===================================================================="
    echo "SCENARIO 6: Country mismatch — registered DE, accessing from NL"
    echo "  Operator holds both DE and NL license"
    echo "  Expected: ALLOW with country mismatch warning"
    echo ""
    validate_session "P_DE_002" "DE" "NL" "MGA DE NL UKGC" "false" "login" || true

    echo ""
    echo "===================================================================="
    echo "SCENARIO 7: French player — operator has MGA but no FR license"
    echo "  Expected: DENY — France requires ANJ license; EU passport not accepted"
    echo ""
    validate_session "P_FR_001" "FR" "FR" "MGA UKGC GI" "false" "login" || true

    echo ""
    return 0
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
if [[ "$RUN_DEMO" == "true" ]]; then
    run_demo
    exit 0
fi

if [[ -z "$PLAYER_ID" ]] || [[ -z "$REGISTERED_COUNTRY" ]] || \
   [[ -z "$DETECTED_COUNTRY" ]] || [[ -z "$HELD_LICENSES" ]]; then
    printf '%bERROR: --player-id, --registered-country, --detected-country, and --held-licenses are required.\n%b\n' \
        "${RED}" "${RESET}" >&2
    printf 'Use --demo to run example scenarios, or --help for usage.\n' >&2
    exit 3
fi

validate_session \
    "$PLAYER_ID" \
    "$REGISTERED_COUNTRY" \
    "$DETECTED_COUNTRY" \
    "$HELD_LICENSES" \
    "$VPN_DETECTED" \
    "$SESSION_EVENT"
