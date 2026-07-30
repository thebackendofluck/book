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

# =============================================================================
# pqc-inventory-scan.sh — Cryptographic inventory scanner for iGaming platforms
# Chapter 24g: Post-Quantum Cryptography for iGaming
#
# Purpose:
#   Generates a complete inventory of all cryptographic assets on this host,
#   classifies them by quantum vulnerability, and outputs a prioritised CSV
#   report to aid PQC migration planning.
#
# What it scans:
#   - All listening TLS/SSL ports (via ss + openssl s_client)
#   - Certificate details: algorithm, key size, expiry, issuer, SANs
#   - JWT signing keys in environment variables and common config files
#   - RSA/ECDSA private key files on disk
#
# Usage:
#   ./pqc-inventory-scan.sh [--json] [--output FILE] [--help]
#
# Options:
#   --json         Output JSON instead of CSV (useful for CI/CD pipelines)
#   --output FILE  Write report to FILE (default: pqc-inventory-<timestamp>.csv)
#   --help         Show this help text
#
# Dependencies:
#   - ss (iproute2) or netstat
#   - openssl
#   - awk, sed, grep (standard POSIX tools)
#
# Deeper analysis: https://github.com/gustcol/post-quantum-check
# =============================================================================

set -euo pipefail

# ---------------------------------------------------------------------------
# Colour codes (suppressed when piping or --json is passed)
# ---------------------------------------------------------------------------
if [[ -t 1 ]]; then
    RED='\033[0;31m'
    YELLOW='\033[1;33m'
    GREEN='\033[0;32m'
    CYAN='\033[0;36m'
    BOLD='\033[1m'
    RESET='\033[0m'
else
    RED='' YELLOW='' GREEN='' CYAN='' BOLD='' RESET=''
fi

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------
OUTPUT_JSON=false
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
OUTPUT_FILE="pqc-inventory-${TIMESTAMP}.csv"
CONNECT_TIMEOUT=5
TMP_DIR=$(mktemp -d)
trap 'rm -rf "$TMP_DIR"' EXIT

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------
while [[ $# -gt 0 ]]; do
    case "$1" in
        --json)
            OUTPUT_JSON=true
            OUTPUT_FILE="pqc-inventory-${TIMESTAMP}.json"
            shift ;;
        --output)
            OUTPUT_FILE="$2"
            shift 2 ;;
        --help|-h)
            sed -n '2,20p' "$0" | sed 's/^# //'
            exit 0 ;;
        *)
            echo "Unknown option: $1" >&2
            exit 1 ;;
    esac
done

# ---------------------------------------------------------------------------
# Logging helpers
# ---------------------------------------------------------------------------
log()  { echo -e "${CYAN}[INFO]${RESET}  $*" >&2; }
warn() { echo -e "${YELLOW}[WARN]${RESET}  $*" >&2; }
err()  { echo -e "${RED}[ERROR]${RESET} $*" >&2; }
ok()   { echo -e "${GREEN}[OK]${RESET}    $*" >&2; }

# ---------------------------------------------------------------------------
# Result accumulator
# FINDINGS is a bash array; each element is a pipe-delimited record:
#   system|algorithm|key_size|purpose|pqc_vulnerable|priority|detail
# ---------------------------------------------------------------------------
declare -a FINDINGS=()

add_finding() {
    local system="$1" algo="$2" key_size="$3" purpose="$4" \
          vulnerable="$5" priority="$6" detail="${7:-}"
    FINDINGS+=("${system}|${algo}|${key_size}|${purpose}|${vulnerable}|${priority}|${detail}")
}

# ---------------------------------------------------------------------------
# Determine quantum vulnerability
# RSA, ECDSA, DSA, DH all broken by Shor's algorithm.
# EdDSA (Ed25519, Ed448) is broken by Shor's as well — same threat.
# PQC algorithms (MLDSA, Kyber, Dilithium, Falcon, SPHINCS+) are safe.
# ---------------------------------------------------------------------------
classify_algorithm() {
    local algo="${1^^}"  # uppercase
    case "$algo" in
        *RSA*)                              echo "true"  ;;
        *ECDSA*|*EC*|*ECKEY*)              echo "true"  ;;
        *EDDSA*|*ED25519*|*ED448*)         echo "true"  ;;
        *DSA*|*DH*)                        echo "true"  ;;
        *MLDSA*|*MLKEM*|*KYBER*|*DILITHIUM*|*FALCON*|*SPHINCS*|*CRYSTALS*)
                                            echo "false" ;;
        *)                                  echo "unknown" ;;
    esac
}

priority_from_purpose() {
    local purpose="${1,,}"
    case "$purpose" in
        *payment*|*financial*|*transaction*)  echo "CRITICAL" ;;
        *player*|*session*|*auth*)            echo "HIGH"     ;;
        *api*|*tls*|*web*)                    echo "HIGH"     ;;
        *internal*|*service-to-service*)      echo "MEDIUM"   ;;
        *log*|*monitor*|*metrics*)            echo "LOW"      ;;
        *)                                     echo "MEDIUM"   ;;
    esac
}

# ---------------------------------------------------------------------------
# Section 1: Scan all listening TLS ports
# ---------------------------------------------------------------------------
scan_tls_ports() {
    log "Scanning listening TLS ports..."

    # Build list of listening TCP ports
    local ports=()
    if command -v ss &>/dev/null; then
        mapfile -t ports < <(ss -tlnp 2>/dev/null | awk 'NR>1 {split($4,a,":"); print a[length(a)]}' | sort -un)
    elif command -v netstat &>/dev/null; then
        mapfile -t ports < <(netstat -tlnp 2>/dev/null | awk 'NR>2 {split($4,a,":"); print a[length(a)]}' | sort -un)
    else
        warn "Neither ss nor netstat found; skipping port scan"
        return
    fi

    local common_tls_ports=(443 8443 4443 993 995 465 636 5061 8080 8444)
    # Merge discovered ports with well-known TLS ports
    for p in "${common_tls_ports[@]}"; do
        ports+=("$p")
    done
    # Deduplicate
    mapfile -t ports < <(printf '%s\n' "${ports[@]}" | sort -un)

    for port in "${ports[@]}"; do
        [[ -z "$port" ]] && continue

        local cert_file="${TMP_DIR}/cert_${port}.pem"
        local conn_result

        # Attempt TLS connection; capture certificate
        conn_result=$(echo "" | timeout "$CONNECT_TIMEOUT" openssl s_client \
            -connect "localhost:${port}" \
            -showcerts \
            -servername "localhost" \
            2>/dev/null || true)

        # Check if we got a certificate
        if ! echo "$conn_result" | grep -q "BEGIN CERTIFICATE"; then
            continue
        fi

        # Extract the leaf (first) certificate
        echo "$conn_result" | openssl x509 -outform PEM > "$cert_file" 2>/dev/null || continue

        local subject algo key_size expiry issuer san
        subject=$(openssl x509 -in "$cert_file" -noout -subject 2>/dev/null | sed 's/subject=//')
        issuer=$(openssl x509 -in "$cert_file" -noout -issuer 2>/dev/null | sed 's/issuer=//')
        expiry=$(openssl x509 -in "$cert_file" -noout -enddate 2>/dev/null | sed 's/notAfter=//')
        san=$(openssl x509 -in "$cert_file" -noout -ext subjectAltName 2>/dev/null | grep -v "X509v3" | tr -d ' \n' || echo "")

        # Determine algorithm and key size from the public key
        local pubkey_info
        pubkey_info=$(openssl x509 -in "$cert_file" -noout -text 2>/dev/null | grep -A2 "Public Key Algorithm" || echo "")

        if echo "$pubkey_info" | grep -qi "rsaEncryption\|rsassa"; then
            algo="RSA"
            key_size=$(openssl x509 -in "$cert_file" -noout -text 2>/dev/null | grep "Public-Key:" | grep -oP '\d+' | head -1 || echo "unknown")
        elif echo "$pubkey_info" | grep -qi "id-ecPublicKey\|ecdsa"; then
            algo="ECDSA"
            key_size=$(openssl x509 -in "$cert_file" -noout -text 2>/dev/null | grep "ASN1 OID:" | awk '{print $NF}' | head -1 || echo "unknown")
        elif echo "$pubkey_info" | grep -qi "ED25519"; then
            algo="EdDSA-Ed25519"
            key_size="256"
        elif echo "$pubkey_info" | grep -qi "dilithium\|mldsa"; then
            algo="ML-DSA"
            key_size=$(echo "$pubkey_info" | grep -oP '\d+' | head -1 || echo "unknown")
        else
            algo=$(echo "$pubkey_info" | grep "Public Key Algorithm" | awk -F': ' '{print $2}' | head -1 | tr -d ' ' || echo "unknown")
            key_size="unknown"
        fi

        local vulnerable
        vulnerable=$(classify_algorithm "$algo")

        local days_to_expiry
        days_to_expiry=$(( ( $(date -d "$expiry" +%s 2>/dev/null || date -j -f "%b %d %T %Y %Z" "$expiry" +%s 2>/dev/null || echo 0) - $(date +%s) ) / 86400 ))

        local purpose="tls-endpoint"
        [[ "$port" == "443" || "$port" == "8443" ]] && purpose="tls-web"
        [[ "$port" == "465" || "$port" == "993" ]] && purpose="tls-mail"

        local priority
        priority=$(priority_from_purpose "$purpose")

        local detail="port=${port} expiry='${expiry}' days_left=${days_to_expiry} issuer='${issuer}'"
        [[ -n "$san" ]] && detail+=" san='${san}'"

        if [[ "$vulnerable" == "true" ]]; then
            warn "Port ${port}: ${algo} ${key_size} — QUANTUM VULNERABLE (${days_to_expiry} days until expiry)"
        else
            ok "Port ${port}: ${algo} — appears PQC-safe"
        fi

        add_finding "localhost:${port}" "$algo" "$key_size" "$purpose" "$vulnerable" "$priority" "$detail"
    done
}

# ---------------------------------------------------------------------------
# Section 2: Scan JWT signing keys in environment variables
# ---------------------------------------------------------------------------
scan_jwt_env() {
    log "Scanning environment variables for JWT signing key indicators..."

    local jwt_vars=("JWT_SECRET" "JWT_PRIVATE_KEY" "TOKEN_SECRET" "AUTH_SECRET"
                    "ACCESS_TOKEN_SECRET" "REFRESH_TOKEN_SECRET" "SIGNING_KEY"
                    "JWT_ALGORITHM" "TOKEN_ALGORITHM")

    for var in "${jwt_vars[@]}"; do
        local value="${!var:-}"
        [[ -z "$value" ]] && continue

        local algo="unknown" key_size="unknown"

        # Detect algorithm from PEM header or value pattern
        if echo "$value" | grep -q "BEGIN RSA PRIVATE KEY\|BEGIN RSA"; then
            algo="RSA"
            key_size=$(echo "$value" | openssl rsa -text -noout 2>/dev/null | grep "Private-Key:" | grep -oP '\d+' || echo "unknown")
        elif echo "$value" | grep -q "BEGIN EC PRIVATE KEY\|BEGIN EC"; then
            algo="ECDSA"
            key_size=$(echo "$value" | openssl ec -text -noout 2>/dev/null | grep "NIST CURVE:" | awk '{print $NF}' || echo "unknown")
        elif [[ ${#value} -eq 32 || ${#value} -eq 64 ]]; then
            # Looks like a symmetric HMAC secret
            algo="HMAC"
            key_size=$(( ${#value} * 8 ))
        elif [[ "$value" =~ ^HS(256|384|512)$ || "$value" =~ ^RS(256|384|512)$ || "$value" =~ ^ES(256|384|512)$ ]]; then
            # It's the algorithm name itself
            algo="$value"
        fi

        local vulnerable
        vulnerable=$(classify_algorithm "$algo")

        local priority
        priority=$(priority_from_purpose "auth")

        warn "Found JWT key in env var ${var}: algo=${algo} key_size=${key_size}"
        add_finding "env:${var}" "$algo" "$key_size" "jwt-signing" "$vulnerable" "$priority" "found_in_environment_variable"
    done
}

# ---------------------------------------------------------------------------
# Section 3: Scan config files for JWT/signing configuration
# ---------------------------------------------------------------------------
scan_jwt_config_files() {
    log "Scanning config files for JWT and signing key configuration..."

    local search_dirs=("/etc" "/opt" "/app" "/srv" "/home" "/usr/local/etc")
    local config_patterns=("*.yml" "*.yaml" "*.json" "*.env" "*.conf" "*.cfg" "*.toml" "*.ini")
    local jwt_patterns=("jwt" "signing_key" "private_key" "secret_key" "token_secret")

    for dir in "${search_dirs[@]}"; do
        [[ -d "$dir" ]] || continue

        for pattern in "${config_patterns[@]}"; do
            while IFS= read -r -d '' file; do
                [[ -r "$file" ]] || continue
                [[ $(wc -c < "$file") -gt 1048576 ]] && continue  # skip files > 1 MB

                for jwt_pat in "${jwt_patterns[@]}"; do
                    if grep -qi "$jwt_pat" "$file" 2>/dev/null; then
                        # Check for algorithm specification
                        local algo_line
                        algo_line=$(grep -i "algorithm\|alg" "$file" 2>/dev/null | head -1 || echo "")

                        local algo="unknown"
                        if echo "$algo_line" | grep -qi "RS256\|RS384\|RS512\|RSA"; then
                            algo="RSA"
                        elif echo "$algo_line" | grep -qi "ES256\|ES384\|ES512\|ECDSA"; then
                            algo="ECDSA"
                        elif echo "$algo_line" | grep -qi "HS256\|HS384\|HS512\|HMAC"; then
                            algo="HMAC"
                        fi

                        local vulnerable
                        vulnerable=$(classify_algorithm "$algo")
                        local priority
                        priority=$(priority_from_purpose "auth")

                        warn "JWT config found in ${file} (pattern: ${jwt_pat}, algo: ${algo})"
                        add_finding "$file" "$algo" "unknown" "jwt-config" "$vulnerable" "$priority" "config_file_reference"
                        break  # only report each file once per JWT pattern type
                    fi
                done
            done < <(find "$dir" -maxdepth 4 -name "$pattern" -print0 2>/dev/null)
        done
    done
}

# ---------------------------------------------------------------------------
# Section 4: Scan for RSA/ECDSA private key files on disk
# ---------------------------------------------------------------------------
scan_private_keys() {
    log "Scanning disk for RSA/ECDSA private key files..."

    local search_dirs=("/etc/ssl" "/etc/nginx" "/etc/haproxy" "/etc/apache2"
                       "/opt" "/srv" "/app" "/home" "/root")
    local key_headers=("BEGIN RSA PRIVATE KEY" "BEGIN EC PRIVATE KEY"
                       "BEGIN PRIVATE KEY" "BEGIN ENCRYPTED PRIVATE KEY"
                       "BEGIN OPENSSH PRIVATE KEY")

    for dir in "${search_dirs[@]}"; do
        [[ -d "$dir" ]] || continue

        while IFS= read -r -d '' file; do
            [[ -r "$file" ]] || continue
            [[ $(wc -c < "$file" 2>/dev/null || echo 0) -gt 102400 ]] && continue  # skip > 100 KB

            local found_header=""
            for header in "${key_headers[@]}"; do
                if grep -q "$header" "$file" 2>/dev/null; then
                    found_header="$header"
                    break
                fi
            done

            [[ -z "$found_header" ]] && continue

            local algo key_size
            if echo "$found_header" | grep -q "RSA"; then
                algo="RSA"
                key_size=$(openssl rsa -in "$file" -text -noout 2>/dev/null | grep "Private-Key:" | grep -oP '\d+' | head -1 || echo "unknown")
            elif echo "$found_header" | grep -q "EC"; then
                algo="ECDSA"
                key_size=$(openssl ec -in "$file" -text -noout 2>/dev/null | grep "ASN1 OID:\|NIST CURVE:" | awk '{print $NF}' | head -1 || echo "unknown")
            else
                # Generic PKCS#8 — try to identify
                algo=$(openssl pkey -in "$file" -text -noout 2>/dev/null | grep "Key:" | head -1 | awk '{print $1}' || echo "unknown")
                key_size="unknown"
            fi

            local vulnerable
            vulnerable=$(classify_algorithm "$algo")
            local priority
            priority=$(priority_from_purpose "tls")

            if [[ "$vulnerable" == "true" ]]; then
                warn "Private key: ${file} (${algo} ${key_size}) — QUANTUM VULNERABLE"
            else
                ok "Private key: ${file} (${algo}) — PQC-safe"
            fi

            add_finding "$file" "$algo" "$key_size" "private-key" "$vulnerable" "$priority" "key_file_on_disk"
        done < <(find "$dir" -maxdepth 6 \( -name "*.pem" -o -name "*.key" -o -name "*.p12" -o -name "*.der" \) -print0 2>/dev/null)
    done
}

# ---------------------------------------------------------------------------
# Section 5: Write CSV report
# ---------------------------------------------------------------------------
write_csv_report() {
    log "Writing CSV report to ${OUTPUT_FILE}..."
    {
        echo "system,algorithm,key_size,purpose,pqc_vulnerable,priority,detail"
        for finding in "${FINDINGS[@]}"; do
            IFS='|' read -r system algo key_size purpose vulnerable priority detail <<< "$finding"
            # Quote fields that may contain commas
            printf '"%s","%s","%s","%s","%s","%s","%s"\n' \
                "$system" "$algo" "$key_size" "$purpose" "$vulnerable" "$priority" "$detail"
        done
    } > "$OUTPUT_FILE"
    ok "Report written: ${OUTPUT_FILE}"
}

# ---------------------------------------------------------------------------
# Section 6: Write JSON report
# ---------------------------------------------------------------------------
write_json_report() {
    log "Writing JSON report to ${OUTPUT_FILE}..."
    {
        echo "{"
        echo "  \"scan_timestamp\": \"$(date -u +%Y-%m-%dT%H:%M:%SZ)\","
        echo "  \"hostname\": \"$(hostname -f 2>/dev/null || hostname)\","
        echo "  \"scanner\": \"pqc-inventory-scan.sh\","
        echo "  \"deeper_analysis\": \"https://github.com/gustcol/post-quantum-check\","
        echo "  \"findings\": ["

        local count=0
        local total=${#FINDINGS[@]}
        for finding in "${FINDINGS[@]}"; do
            count=$(( count + 1 ))
            IFS='|' read -r system algo key_size purpose vulnerable priority detail <<< "$finding"
            local comma=""
            [[ $count -lt $total ]] && comma=","
            printf '    {"system":"%s","algorithm":"%s","key_size":"%s","purpose":"%s","pqc_vulnerable":%s,"priority":"%s","detail":"%s"}%s\n' \
                "$system" "$algo" "$key_size" "$purpose" "$vulnerable" "$priority" "$detail" "$comma"
        done

        echo "  ]"
        echo "}"
    } > "$OUTPUT_FILE"
    ok "JSON report written: ${OUTPUT_FILE}"
}

# ---------------------------------------------------------------------------
# Section 7: Summary
# ---------------------------------------------------------------------------
print_summary() {
    local total=${#FINDINGS[@]}
    local vuln_count=0
    local critical_count=0

    for finding in "${FINDINGS[@]}"; do
        IFS='|' read -r _ _ _ _ vulnerable priority _ <<< "$finding"
        [[ "$vulnerable" == "true" ]] && vuln_count=$(( vuln_count + 1 ))
        [[ "$priority" == "CRITICAL" && "$vulnerable" == "true" ]] && critical_count=$(( critical_count + 1 ))
    done

    echo ""
    echo -e "${BOLD}============================================================${RESET}"
    echo -e "${BOLD} PQC Cryptographic Inventory Summary${RESET}"
    echo -e "${BOLD}============================================================${RESET}"
    echo -e " Total assets scanned : ${BOLD}${total}${RESET}"
    echo -e " Quantum-vulnerable   : ${RED}${BOLD}${vuln_count}${RESET}"
    echo -e " Critical priority    : ${RED}${BOLD}${critical_count}${RESET}"
    echo -e " Report file          : ${CYAN}${OUTPUT_FILE}${RESET}"
    echo ""
    echo -e " For deeper analysis run:"
    echo -e "   ${CYAN}pip install post-quantum-check && pqcheck --scan .${RESET}"
    echo -e " GitHub: ${CYAN}https://github.com/gustcol/post-quantum-check${RESET}"
    echo -e "${BOLD}============================================================${RESET}"
    echo ""

    # Exit code for CI/CD: non-zero if any CRITICAL vulnerable asset found
    if [[ $critical_count -gt 0 ]]; then
        return 2
    elif [[ $vuln_count -gt 0 ]]; then
        return 1
    fi
    return 0
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
main() {
    echo ""
    echo -e "${BOLD}PQC Cryptographic Inventory Scanner${RESET}"
    echo -e "Host: $(hostname -f 2>/dev/null || hostname)  |  Date: $(date -u)"
    echo -e "Report: ${OUTPUT_FILE}"
    echo ""

    scan_tls_ports
    scan_jwt_env
    scan_jwt_config_files
    scan_private_keys

    if [[ "$OUTPUT_JSON" == "true" ]]; then
        write_json_report
    else
        write_csv_report
    fi

    print_summary
}

main "$@"
