#!/usr/bin/env bash
# Companion code for "The Backend of Luck" - Chapter 22, Internal Docker Registry.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# =============================================================================
# ClamAV Malware Scanner for Container Images — iGaming Registry
# =============================================================================
# Scans extracted container image layers for malware, trojans, cryptominers,
# and other malicious content. Required by GLI-33 for supply chain security.
#
# Prerequisites:
#   - clamav (clamd + clamscan + freshclam)
#   - skopeo (image layer extraction)
#   - jq, tar
#
# Usage:
#   ./clamav-scanner.sh scan <image>            # Scan image layers
#   ./clamav-scanner.sh update-sigs             # Update ClamAV signatures
#   ./clamav-scanner.sh scan-registry <project> # Scan all images in project
#   ./clamav-scanner.sh daemon-check            # Verify clamd is running
# =============================================================================
set -euo pipefail

HARBOR_URL="${HARBOR_URL:-https://registry.casino-platform.internal}"
HARBOR_USER="${HARBOR_USER:-admin}"
HARBOR_PASS="${HARBOR_PASS:?HARBOR_PASS must be set}"
WORK_DIR="${WORK_DIR:-/tmp/clamav-scan}"
REPORT_DIR="${REPORT_DIR:-/var/reports/clamav}"
CLAM_SOCKET="${CLAM_SOCKET:-/var/run/clamav/clamd.ctl}"
ALERT_WEBHOOK="${ALERT_WEBHOOK:-}"

# Custom signature paths for gambling-specific threats
CUSTOM_SIGS_DIR="/var/lib/clamav/custom"

mkdir -p "${WORK_DIR}" "${REPORT_DIR}" "${CUSTOM_SIGS_DIR}"

log() { echo "[$(date +'%Y-%m-%d %H:%M:%S')] [clamav] $*"; }
error() { echo "[$(date +'%Y-%m-%d %H:%M:%S')] [clamav] ERROR: $*" >&2; }

send_alert() {
    local image="$1" message="$2"
    if [[ -n "${ALERT_WEBHOOK}" ]]; then
        curl -sf -X POST "${ALERT_WEBHOOK}" \
            -H "Content-Type: application/json" \
            -d "{
                \"text\": \":biohazard_sign: *MALWARE DETECTED*\n*Image:* \`${image}\`\n*Details:* ${message}\"
            }" || true
    fi
}

# --- Verify ClamAV Daemon ----------------------------------------------------
cmd_daemon_check() {
    log "Checking ClamAV daemon status..."

    # Check if clamd is running
    if [[ -S "${CLAM_SOCKET}" ]]; then
        local version
        version=$(clamdscan --version 2>/dev/null || echo "unknown")
        log "ClamAV daemon running: ${version}"
    else
        log "ClamAV daemon not running, using clamscan (slower)"
    fi

    # Check signature freshness
    local sig_date
    sig_date=$(sigtool --info /var/lib/clamav/daily.cvd 2>/dev/null | grep "Build time" || echo "unknown")
    log "Signature database: ${sig_date}"

    # Count signatures
    local sig_count
    sig_count=$(sigtool --info /var/lib/clamav/daily.cvd 2>/dev/null | grep "Signatures" | awk '{print $2}' || echo "unknown")
    log "Total signatures: ${sig_count}"
}

# --- Update Signatures -------------------------------------------------------
cmd_update_sigs() {
    log "Updating ClamAV virus signatures..."
    freshclam --verbose 2>&1 | tail -5

    # Deploy custom gambling-industry signatures
    log "Deploying custom iGaming threat signatures..."
    cat > "${CUSTOM_SIGS_DIR}/igaming-threats.ndb" << 'SIGS'
# Custom signatures for gambling industry threats
# Format: SignatureName:TargetType:Offset:HexSignature
#
# Cryptominer detection patterns
CryptoMiner.XMRig.Config:0:*:786d7269672e636f6e66
CryptoMiner.Stratum.Protocol:0:*:7374726174756d2b746370
CryptoMiner.CoinHive:0:*:436f696e486976652e416e6f6e796d6f7573
#
# Reverse shell patterns
ReverseShell.Bash.TCP:0:*:2f6465762f7463702f
ReverseShell.Netcat.Exec:0:*:6e63202d65202f62696e2f
#
# Credential harvesting patterns common in gambling attacks
CredHarvest.DBDump:0:*:6d7973716c64756d70202d2d616c6c
CredHarvest.ShadowRead:0:*:2f6574632f736861646f77
SIGS

    # Reload signatures if daemon is running
    if [[ -S "${CLAM_SOCKET}" ]]; then
        clamdscan --reload 2>/dev/null || true
        log "ClamAV daemon reloaded with updated signatures"
    fi

    log "Signature update complete"
}

# --- Extract and Scan Image ---------------------------------------------------
cmd_scan() {
    local image="$1"
    local timestamp
    timestamp=$(date +'%Y%m%d_%H%M%S')
    local safe_name
    safe_name=$(echo "${image}" | tr '/:' '__')
    local scan_dir="${WORK_DIR}/${safe_name}_${timestamp}"
    local report_file="${REPORT_DIR}/${safe_name}_${timestamp}.json"

    mkdir -p "${scan_dir}"

    log "Scanning image for malware: ${image}"

    # Step 1: Pull image layers using skopeo (no Docker daemon needed)
    log "  Extracting image layers..."
    skopeo copy \
        --src-creds "${HARBOR_USER}:${HARBOR_PASS}" \
        "docker://${image}" \
        "dir:${scan_dir}/image" \
        2>/dev/null

    # Step 2: Extract all layers
    local layer_dir="${scan_dir}/layers"
    mkdir -p "${layer_dir}"

    local layer_count=0
    for layer in "${scan_dir}"/image/*.tar.gz "${scan_dir}"/image/*.tar; do
        [[ -f "${layer}" ]] || continue
        layer_count=$((layer_count + 1))
        local layer_extract="${layer_dir}/layer_${layer_count}"
        mkdir -p "${layer_extract}"
        tar xf "${layer}" -C "${layer_extract}" 2>/dev/null || true
    done

    log "  Extracted ${layer_count} layers"

    # Step 3: Run ClamAV scan
    local clam_report="${scan_dir}/clam_output.txt"
    local scan_cmd="clamscan"
    local scan_opts=("--recursive" "--infected" "--detect-pua=yes"
                     "--detect-structured=yes" "--structured-ssn-format=2"
                     "--max-filesize=100M" "--max-scansize=500M"
                     "--max-recursion=30")

    # Use daemon if available (much faster)
    if [[ -S "${CLAM_SOCKET}" ]]; then
        scan_cmd="clamdscan"
        scan_opts=("--multiscan" "--infected" "--fdpass")
    fi

    log "  Running malware scan (${scan_cmd})..."
    ${scan_cmd} "${scan_opts[@]}" "${layer_dir}" > "${clam_report}" 2>&1 || true

    # Step 4: Parse results
    local infected_count
    infected_count=$(grep -c "FOUND$" "${clam_report}" 2>/dev/null || echo 0)
    local scanned_count
    scanned_count=$(grep "Scanned files:" "${clam_report}" | awk '{print $3}' || echo "unknown")

    # Build JSON report
    local infected_files="[]"
    if [[ "${infected_count}" -gt 0 ]]; then
        infected_files=$(grep "FOUND$" "${clam_report}" | \
            jq -R -s 'split("\n") | map(select(. != "")) | map(split(": ") | {file: .[0], threat: .[1]})' \
            2>/dev/null || echo "[]")
    fi

    jq -n \
        --arg image "${image}" \
        --arg timestamp "$(date -u +'%Y-%m-%dT%H:%M:%SZ')" \
        --argjson infected "${infected_count}" \
        --arg scanned "${scanned_count}" \
        --argjson findings "${infected_files}" \
        --arg scanner_version "$(clamscan --version 2>/dev/null | head -1)" \
        '{
            image: $image,
            scan_timestamp: $timestamp,
            scanner: "ClamAV",
            scanner_version: $scanner_version,
            files_scanned: $scanned,
            malware_detected: $infected,
            passed: ($infected == 0),
            findings: $findings,
            compliance: {
                standard: "GLI-33",
                requirement: "Supply chain malware scanning",
                status: (if $infected == 0 then "COMPLIANT" else "NON-COMPLIANT" end)
            }
        }' > "${report_file}"

    # Step 5: Report results
    if [[ "${infected_count}" -gt 0 ]]; then
        error "MALWARE DETECTED in ${image}: ${infected_count} infected files"
        grep "FOUND$" "${clam_report}" | while IFS= read -r line; do
            error "  ${line}"
        done
        send_alert "${image}" "${infected_count} malware/threat(s) detected — image QUARANTINED"

        # Tag image as quarantined in Harbor
        log "  Marking image as quarantined in Harbor..."
        local repo_name tag
        repo_name=$(echo "${image}" | sed 's|.*//||' | cut -d: -f1)
        tag=$(echo "${image}" | cut -d: -f2)
        curl -sk -u "${HARBOR_USER}:${HARBOR_PASS}" \
            -X POST "${HARBOR_URL}/api/v2.0/projects/${repo_name%%/*}/repositories/${repo_name#*/}/artifacts/${tag}/labels" \
            -H "Content-Type: application/json" \
            -d '{"name": "quarantined", "description": "Malware detected by ClamAV"}' \
            2>/dev/null || true
    else
        log "CLEAN: No malware detected in ${image} (${scanned_count} files scanned)"
    fi

    # Cleanup extracted layers
    rm -rf "${scan_dir}"

    log "Report: ${report_file}"
    [[ "${infected_count}" -gt 0 ]] && return 1
    return 0
}

# --- Scan All Images in Project -----------------------------------------------
cmd_scan_registry() {
    local project="$1"
    local total=0 clean=0 infected=0

    log "Scanning all images in project: ${project}"

    # List all repositories
    local repos
    repos=$(curl -sk -u "${HARBOR_USER}:${HARBOR_PASS}" \
        "${HARBOR_URL}/api/v2.0/projects/${project}/repositories?page_size=100" \
        2>/dev/null | jq -r '.[].name' 2>/dev/null)

    while IFS= read -r repo; do
        [[ -z "${repo}" ]] && continue

        # Get latest artifact
        local tag
        tag=$(curl -sk -u "${HARBOR_USER}:${HARBOR_PASS}" \
            "${HARBOR_URL}/api/v2.0/projects/${project}/repositories/${repo##*/}/artifacts?page=1&page_size=1&with_tag=true" \
            2>/dev/null | jq -r '.[0].tags[0].name // "latest"' 2>/dev/null)

        local full_image="${HARBOR_URL#https://}/${repo}:${tag}"
        total=$((total + 1))

        if cmd_scan "${full_image}" 2>/dev/null; then
            clean=$((clean + 1))
        else
            infected=$((infected + 1))
        fi
    done <<< "${repos}"

    log "Registry scan complete: ${total} images, ${clean} clean, ${infected} infected"
    [[ "${infected}" -gt 0 ]] && return 1
    return 0
}

# --- Main ---------------------------------------------------------------------
main() {
    local cmd="${1:-help}"
    shift || true

    case "${cmd}" in
        scan)
            [[ $# -lt 1 ]] && { error "Usage: $0 scan <image>"; exit 1; }
            cmd_scan "$1"
            ;;
        update-sigs)
            cmd_update_sigs
            ;;
        scan-registry)
            [[ $# -lt 1 ]] && { error "Usage: $0 scan-registry <project>"; exit 1; }
            cmd_scan_registry "$1"
            ;;
        daemon-check)
            cmd_daemon_check
            ;;
        *)
            echo "Usage: $0 {scan|update-sigs|scan-registry|daemon-check} [args]"
            echo ""
            echo "Commands:"
            echo "  scan <image>             Scan container image layers for malware"
            echo "  update-sigs              Update ClamAV + custom iGaming signatures"
            echo "  scan-registry <project>  Scan all images in a Harbor project"
            echo "  daemon-check             Verify ClamAV daemon status"
            exit 1
            ;;
    esac
}

main "$@"
