#!/usr/bin/env bash
# Companion code for "The Backend of Luck" - Chapter 23, DevSecOps for iGaming.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# shellcheck disable=SC2001,SC2015
# =============================================================================
# SBOM Generation Pipeline for iGaming Platforms
# Generates Software Bill of Materials at build, registry, and runtime phases
# using Syft (Anchore) with CycloneDX and SPDX output formats.
#
# Usage:
#   ./sbom-pipeline.sh --phase build   --image acme-casino/payment-service:v2.1.0
#   ./sbom-pipeline.sh --phase registry --image registry.acme-casino.io/game-engine:latest
#   ./sbom-pipeline.sh --phase runtime  --container payment-service-pod-abc123
#   ./sbom-pipeline.sh --phase all      --image acme-casino/platform:v3.0.0
#
# Requirements:
#   - syft (>= 0.100.0)
#   - grype (for vulnerability correlation)
#   - cosign (for SBOM attestation signing)
#   - jq, curl
# =============================================================================
set -euo pipefail
IFS=$'\n\t'

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
SBOM_OUTPUT_DIR="${SBOM_OUTPUT_DIR:-/opt/acme-casino/sbom}"
SBOM_FORMAT="${SBOM_FORMAT:-cyclonedx-json}"  # cyclonedx-json | spdx-json | syft-json
SBOM_REGISTRY="${SBOM_REGISTRY:-registry.acme-casino.io}"
ATTESTATION_KEY="${ATTESTATION_KEY:-/etc/cosign/cosign.key}"
DEPENDENCY_TRACK_URL="${DEPENDENCY_TRACK_URL:-https://deptrack.acme-casino.io}"
DEPENDENCY_TRACK_API_KEY="${DEPENDENCY_TRACK_API_KEY:-}"
SLACK_WEBHOOK_URL="${SLACK_WEBHOOK_URL:-}"
LOG_FILE="${SBOM_OUTPUT_DIR}/sbom-pipeline.log"

# iGaming-critical service list — these get extra scrutiny
CRITICAL_SERVICES=(
    "payment-service"
    "wallet-service"
    "game-engine"
    "rng-service"
    "kyc-service"
    "bonus-engine"
    "anti-fraud-service"
    "responsible-gaming"
    "regulatory-reporting"
)

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
log() {
    local level="$1"; shift
    local ts
    ts="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
    printf '[%s] [%s] %s\n' "$ts" "$level" "$*" | tee -a "$LOG_FILE"
}
info()  { log "INFO"  "$@"; }
warn()  { log "WARN"  "$@"; }
error() { log "ERROR" "$@"; }
fatal() { log "FATAL" "$@"; exit 1; }

# ---------------------------------------------------------------------------
# Dependency checks
# ---------------------------------------------------------------------------
check_dependencies() {
    local missing=()
    for cmd in syft grype cosign jq curl; do
        if ! command -v "$cmd" &>/dev/null; then
            missing+=("$cmd")
        fi
    done
    if [[ ${#missing[@]} -gt 0 ]]; then
        fatal "Missing required tools: ${missing[*]}"
    fi
    info "All dependencies verified"
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
is_critical_service() {
    local image_name="$1"
    for svc in "${CRITICAL_SERVICES[@]}"; do
        if [[ "$image_name" == *"$svc"* ]]; then
            return 0
        fi
    done
    return 1
}

ensure_output_dir() {
    mkdir -p "$SBOM_OUTPUT_DIR"/{build,registry,runtime,attestations}
}

notify_slack() {
    local message="$1"
    if [[ -n "$SLACK_WEBHOOK_URL" ]]; then
        curl -sS -X POST "$SLACK_WEBHOOK_URL" \
            -H 'Content-Type: application/json' \
            -d "{\"text\": \"$message\"}" \
            >/dev/null 2>&1 || warn "Slack notification failed"
    fi
}

upload_to_dependency_track() {
    local sbom_file="$1"
    local project_name="$2"
    local project_version="$3"

    if [[ -z "$DEPENDENCY_TRACK_API_KEY" ]]; then
        warn "DEPENDENCY_TRACK_API_KEY not set — skipping upload"
        return 0
    fi

    local encoded_sbom
    encoded_sbom="$(base64 -w0 "$sbom_file")"

    local response
    response=$(curl -sS -X PUT "${DEPENDENCY_TRACK_URL}/api/v1/bom" \
        -H "X-Api-Key: ${DEPENDENCY_TRACK_API_KEY}" \
        -H "Content-Type: application/json" \
        -d "{
            \"projectName\": \"${project_name}\",
            \"projectVersion\": \"${project_version}\",
            \"autoCreate\": true,
            \"bom\": \"${encoded_sbom}\"
        }")

    if echo "$response" | jq -e '.token' >/dev/null 2>&1; then
        info "Uploaded SBOM to Dependency-Track: $(echo "$response" | jq -r '.token')"
    else
        warn "Dependency-Track upload may have failed: $response"
    fi
}

# ---------------------------------------------------------------------------
# Phase 1: Build-time SBOM
# ---------------------------------------------------------------------------
generate_build_sbom() {
    local image="$1"
    local tag
    tag="$(echo "$image" | tr '/:' '-')"
    local timestamp
    timestamp="$(date -u +%Y%m%d-%H%M%S)"
    local output_file="${SBOM_OUTPUT_DIR}/build/${tag}-${timestamp}.${SBOM_FORMAT##*-}"

    info "Phase: BUILD — Generating SBOM for image: $image"

    # Generate SBOM from the Docker image or directory
    if [[ -d "$image" ]]; then
        syft dir:"$image" \
            -o "$SBOM_FORMAT" \
            --file "$output_file" \
            --name "$tag" \
            2>>"$LOG_FILE"
    else
        syft "$image" \
            -o "$SBOM_FORMAT" \
            --file "$output_file" \
            2>>"$LOG_FILE"
    fi

    if [[ ! -f "$output_file" ]]; then
        fatal "SBOM generation failed for $image"
    fi

    local component_count
    component_count=$(jq '.components | length' "$output_file" 2>/dev/null || echo "unknown")
    info "Build SBOM generated: $output_file ($component_count components)"

    # For critical services, also run vulnerability correlation
    if is_critical_service "$image"; then
        info "Critical service detected — running immediate vulnerability correlation"
        local vuln_file="${SBOM_OUTPUT_DIR}/build/${tag}-${timestamp}-vulns.json"
        grype sbom:"$output_file" -o json --file "$vuln_file" 2>>"$LOG_FILE"

        local crit_count high_count
        crit_count=$(jq '[.matches[] | select(.vulnerability.severity == "Critical")] | length' "$vuln_file" 2>/dev/null || echo 0)
        high_count=$(jq '[.matches[] | select(.vulnerability.severity == "High")] | length' "$vuln_file" 2>/dev/null || echo 0)

        if [[ "$crit_count" -gt 0 ]]; then
            warn "CRITICAL: $crit_count critical vulnerabilities found in $image"
            notify_slack ":rotating_light: *CRITICAL* — $crit_count critical vulns in \`$image\` (build phase)"
        fi

        info "Vulnerability correlation complete: Critical=$crit_count, High=$high_count"
    fi

    # Sign SBOM with cosign attestation
    sign_sbom "$image" "$output_file"

    # Upload to Dependency-Track
    local project_name project_version
    project_name="$(echo "$image" | cut -d: -f1 | tr '/' '-')"
    project_version="$(echo "$image" | cut -d: -f2)"
    upload_to_dependency_track "$output_file" "$project_name" "${project_version:-latest}"

    echo "$output_file"
}

# ---------------------------------------------------------------------------
# Phase 2: Registry-time SBOM
# ---------------------------------------------------------------------------
generate_registry_sbom() {
    local image="$1"
    local tag
    tag="$(echo "$image" | tr '/:' '-')"
    local timestamp
    timestamp="$(date -u +%Y%m%d-%H%M%S)"
    local output_file="${SBOM_OUTPUT_DIR}/registry/${tag}-${timestamp}.${SBOM_FORMAT##*-}"

    info "Phase: REGISTRY — Generating SBOM for registry image: $image"

    # Pull image digest for deterministic reference
    local digest
    digest=$(docker inspect --format='{{index .RepoDigests 0}}' "$image" 2>/dev/null || echo "$image")
    info "Image digest: $digest"

    syft registry:"$image" \
        -o "$SBOM_FORMAT" \
        --file "$output_file" \
        2>>"$LOG_FILE" || {
        # Fallback: pull and scan locally
        warn "Registry scan failed, falling back to local scan"
        docker pull "$image" 2>>"$LOG_FILE"
        syft "$image" \
            -o "$SBOM_FORMAT" \
            --file "$output_file" \
            2>>"$LOG_FILE"
    }

    if [[ ! -f "$output_file" ]]; then
        fatal "Registry SBOM generation failed for $image"
    fi

    local component_count
    component_count=$(jq '.components | length' "$output_file" 2>/dev/null || echo "unknown")
    info "Registry SBOM generated: $output_file ($component_count components)"

    # Compare with previous build SBOM to detect drift
    detect_sbom_drift "$tag" "$output_file"

    echo "$output_file"
}

# ---------------------------------------------------------------------------
# Phase 3: Runtime SBOM
# ---------------------------------------------------------------------------
generate_runtime_sbom() {
    local container="$1"
    local timestamp
    timestamp="$(date -u +%Y%m%d-%H%M%S)"
    local output_file="${SBOM_OUTPUT_DIR}/runtime/${container}-${timestamp}.${SBOM_FORMAT##*-}"

    info "Phase: RUNTIME — Generating SBOM for running container: $container"

    # Check if we're running in Kubernetes or plain Docker
    if command -v kubectl &>/dev/null && kubectl get pod "$container" &>/dev/null 2>&1; then
        info "Kubernetes environment detected"
        local pod_image
        pod_image=$(kubectl get pod "$container" -o jsonpath='{.spec.containers[0].image}')
        info "Pod image: $pod_image"

        # Export filesystem from running pod
        local tmp_export="/tmp/runtime-export-${container}"
        mkdir -p "$tmp_export"
        kubectl cp "${container}:/" "$tmp_export" --retries=3 2>>"$LOG_FILE" || {
            warn "Full filesystem copy failed, scanning image reference instead"
            syft "$pod_image" \
                -o "$SBOM_FORMAT" \
                --file "$output_file" \
                2>>"$LOG_FILE"
        }

        if [[ -d "$tmp_export" && ! -f "$output_file" ]]; then
            syft dir:"$tmp_export" \
                -o "$SBOM_FORMAT" \
                --file "$output_file" \
                2>>"$LOG_FILE"
            rm -rf "$tmp_export"
        fi
    else
        # Plain Docker
        local container_id
        container_id=$(docker ps -q -f "name=$container" | head -1)
        if [[ -z "$container_id" ]]; then
            fatal "Container '$container' not found or not running"
        fi

        # Export running container filesystem
        local tmp_tar="/tmp/runtime-${container}-${timestamp}.tar"
        docker export "$container_id" > "$tmp_tar" 2>>"$LOG_FILE"
        syft "$tmp_tar" \
            -o "$SBOM_FORMAT" \
            --file "$output_file" \
            2>>"$LOG_FILE"
        rm -f "$tmp_tar"
    fi

    if [[ ! -f "$output_file" ]]; then
        fatal "Runtime SBOM generation failed for $container"
    fi

    local component_count
    component_count=$(jq '.components | length' "$output_file" 2>/dev/null || echo "unknown")
    info "Runtime SBOM generated: $output_file ($component_count components)"

    # Check for packages not present in the build SBOM (runtime drift)
    detect_runtime_drift "$container" "$output_file"

    echo "$output_file"
}

# ---------------------------------------------------------------------------
# SBOM Drift Detection
# ---------------------------------------------------------------------------
detect_sbom_drift() {
    local tag="$1"
    local current_sbom="$2"

    # Find the most recent previous SBOM for comparison
    local previous_sbom
    previous_sbom=$(find "${SBOM_OUTPUT_DIR}/build" -name "${tag}-*" -type f | sort -r | sed -n '2p')

    if [[ -z "$previous_sbom" ]]; then
        info "No previous SBOM found for drift comparison"
        return 0
    fi

    info "Comparing with previous SBOM: $previous_sbom"

    local prev_pkgs curr_pkgs
    prev_pkgs=$(jq -r '.components[].purl // .components[].name' "$previous_sbom" 2>/dev/null | sort)
    curr_pkgs=$(jq -r '.components[].purl // .components[].name' "$current_sbom" 2>/dev/null | sort)

    local added removed
    added=$(comm -13 <(echo "$prev_pkgs") <(echo "$curr_pkgs") | wc -l)
    removed=$(comm -23 <(echo "$prev_pkgs") <(echo "$curr_pkgs") | wc -l)

    if [[ "$added" -gt 0 || "$removed" -gt 0 ]]; then
        warn "SBOM drift detected: +${added} added, -${removed} removed components"
        notify_slack ":warning: SBOM drift for \`$tag\`: +${added}/-${removed} components"
    else
        info "No SBOM drift detected"
    fi
}

detect_runtime_drift() {
    local container="$1"
    local runtime_sbom="$2"

    # Find the latest build SBOM for the same service
    local service_name
    service_name=$(echo "$container" | sed 's/-[a-z0-9]*$//')
    local build_sbom
    build_sbom=$(find "${SBOM_OUTPUT_DIR}/build" -name "*${service_name}*" -type f | sort -r | head -1)

    if [[ -z "$build_sbom" ]]; then
        warn "No build SBOM found for runtime drift comparison of $container"
        return 0
    fi

    local build_pkgs runtime_pkgs
    build_pkgs=$(jq -r '.components[].purl // .components[].name' "$build_sbom" 2>/dev/null | sort)
    runtime_pkgs=$(jq -r '.components[].purl // .components[].name' "$runtime_sbom" 2>/dev/null | sort)

    local unexpected
    unexpected=$(comm -13 <(echo "$build_pkgs") <(echo "$runtime_pkgs"))

    if [[ -n "$unexpected" ]]; then
        local count
        count=$(echo "$unexpected" | wc -l)
        error "RUNTIME DRIFT: $count packages found at runtime that are NOT in build SBOM"
        echo "$unexpected" | head -20 | while read -r pkg; do
            error "  Unexpected package: $pkg"
        done
        notify_slack ":rotating_light: *RUNTIME DRIFT* — $count unexpected packages in \`$container\`"
    else
        info "No runtime drift detected for $container"
    fi
}

# ---------------------------------------------------------------------------
# SBOM Attestation (cosign)
# ---------------------------------------------------------------------------
sign_sbom() {
    local image="$1"
    local sbom_file="$2"

    if [[ ! -f "$ATTESTATION_KEY" ]]; then
        warn "Cosign key not found at $ATTESTATION_KEY — skipping attestation"
        return 0
    fi

    info "Signing SBOM attestation for $image"
    cosign attest \
        --key "$ATTESTATION_KEY" \
        --predicate "$sbom_file" \
        --type cyclonedx \
        "$image" \
        2>>"$LOG_FILE" && info "SBOM attestation signed successfully" \
        || warn "SBOM attestation signing failed"
}

# ---------------------------------------------------------------------------
# Scheduled full-platform scan
# ---------------------------------------------------------------------------
scan_all_platform_images() {
    info "=== Full Platform SBOM Scan ==="
    local images
    images=$(docker images --format '{{.Repository}}:{{.Tag}}' | grep -E "acme-casino|game-engine|payment|wallet|rng|kyc|bonus|anti-fraud")

    if [[ -z "$images" ]]; then
        warn "No acme-casino images found locally"
        return 0
    fi

    local total=0 failed=0
    while IFS= read -r img; do
        info "Scanning: $img"
        if generate_build_sbom "$img" >/dev/null 2>&1; then
            ((total++))
        else
            ((failed++))
            warn "Failed to generate SBOM for $img"
        fi
    done <<< "$images"

    info "Platform scan complete: $total succeeded, $failed failed"
    notify_slack ":white_check_mark: Platform SBOM scan complete: ${total} images scanned, ${failed} failures"
}

# ---------------------------------------------------------------------------
# CLI argument parsing
# ---------------------------------------------------------------------------
usage() {
    cat <<USAGE
Usage: $0 --phase <build|registry|runtime|all|platform> [OPTIONS]

Options:
  --phase      Phase to execute: build, registry, runtime, all, platform
  --image      Container image (required for build/registry/all)
  --container  Running container name (required for runtime)
  --format     SBOM format: cyclonedx-json, spdx-json, syft-json (default: cyclonedx-json)
  --output     Output directory (default: /opt/acme-casino/sbom)
  --help       Show this help

Examples:
  $0 --phase build --image acme-casino/payment-service:v2.1.0
  $0 --phase registry --image registry.acme-casino.io/game-engine:latest
  $0 --phase runtime --container payment-service-pod-abc123
  $0 --phase all --image acme-casino/platform:v3.0.0
  $0 --phase platform  # Scan all platform images
USAGE
    exit 0
}

main() {
    local phase="" image="" container=""

    while [[ $# -gt 0 ]]; do
        case "$1" in
            --phase)     phase="$2";           shift 2;;
            --image)     image="$2";           shift 2;;
            --container) container="$2";       shift 2;;
            --format)    SBOM_FORMAT="$2";     shift 2;;
            --output)    SBOM_OUTPUT_DIR="$2"; shift 2;;
            --help|-h)   usage;;
            *)           fatal "Unknown argument: $1";;
        esac
    done

    [[ -z "$phase" ]] && usage

    check_dependencies
    ensure_output_dir

    case "$phase" in
        build)
            [[ -z "$image" ]] && fatal "--image required for build phase"
            generate_build_sbom "$image"
            ;;
        registry)
            [[ -z "$image" ]] && fatal "--image required for registry phase"
            generate_registry_sbom "$image"
            ;;
        runtime)
            [[ -z "$container" ]] && fatal "--container required for runtime phase"
            generate_runtime_sbom "$container"
            ;;
        all)
            [[ -z "$image" ]] && fatal "--image required for all phases"
            generate_build_sbom "$image"
            generate_registry_sbom "$image"
            ;;
        platform)
            scan_all_platform_images
            ;;
        *)
            fatal "Unknown phase: $phase"
            ;;
    esac

    info "SBOM pipeline completed successfully"
}

main "$@"
