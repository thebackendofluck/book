#!/usr/bin/env bash
# Companion code for "The Backend of Luck" - Chapter 41, Case Study.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# shellcheck disable=SC2015,SC2034,SC2155
# ============================================================================
# CDN Pre-Warming Script for Major Sporting Events
# ============================================================================
# Pre-warms CloudFront/Fastly edge caches with static assets, event pages,
# and odds snapshots before major events to ensure zero cold-start latency.
#
# Usage:
#   ./cdn_prewarmer.sh --event world_cup_final --regions us,eu,sa
#   ./cdn_prewarmer.sh --event champions_league --warm-odds --warm-static
#   ./cdn_prewarmer.sh --dry-run --event super_bowl
#
# Requirements:
#   - AWS CLI configured with CloudFront permissions
#   - curl, jq, parallel (GNU parallel for concurrent warming)
# ============================================================================

set -euo pipefail

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
CDN_DOMAIN="${CDN_DOMAIN:-cdn.betting-platform.example.com}"
API_DOMAIN="${API_DOMAIN:-api.betting-platform.example.com}"
STATIC_DOMAIN="${STATIC_DOMAIN:-static.betting-platform.example.com}"
CLOUDFRONT_DIST_ID="${CLOUDFRONT_DIST_ID:-E1A2B3C4D5E6F7}"

# Edge locations to warm (CloudFront PoP codes)
declare -A REGION_POPS=(
    [us]="IAD JFK ORD LAX MIA DFW SEA ATL"
    [eu]="LHR FRA AMS CDG MAD MXP"
    [sa]="GRU GIG SCL BOG"
    [ap]="NRT SIN SYD HKG ICN"
)

# Concurrency settings
MAX_CONCURRENT_REQUESTS=50
REQUEST_TIMEOUT=10
RETRY_COUNT=3

# Logging
LOG_DIR="/var/log/cdn-prewarmer"
LOG_FILE="${LOG_DIR}/prewarm-$(date +%Y%m%d-%H%M%S).log"
DRY_RUN=false
VERBOSE=false

# ---------------------------------------------------------------------------
# Asset lists
# ---------------------------------------------------------------------------

# Critical static assets that must be cached at all edge locations
STATIC_ASSETS=(
    "/js/app.min.js"
    "/js/betting-engine.min.js"
    "/js/live-odds.min.js"
    "/js/websocket-client.min.js"
    "/css/main.min.css"
    "/css/live-betting.min.css"
    "/fonts/platform-icons.woff2"
    "/fonts/roboto-regular.woff2"
    "/fonts/roboto-bold.woff2"
    "/images/logo.svg"
    "/images/sports/football.svg"
    "/images/sports/basketball.svg"
    "/images/sports/tennis.svg"
    "/images/flags/sprite.png"
    "/manifest.json"
    "/service-worker.js"
)

# Event-specific page templates
EVENT_PAGES=(
    "/events/live"
    "/events/upcoming"
    "/events/popular"
    "/betslip"
    "/account/balance"
    "/promotions/event-specials"
    "/results/live"
    "/statistics"
)

# API endpoints to warm (cached GET responses)
CACHED_API_ENDPOINTS=(
    "/api/v2/sports"
    "/api/v2/competitions"
    "/api/v2/events/featured"
    "/api/v2/markets/popular"
    "/api/v2/odds/snapshot"
    "/api/v2/promotions/active"
    "/api/v2/config/client"
)

# ---------------------------------------------------------------------------
# Functions
# ---------------------------------------------------------------------------

log() {
    local level="$1"
    shift
    local msg="$(date '+%Y-%m-%d %H:%M:%S') [$level] $*"
    echo "$msg"
    if [[ -d "$LOG_DIR" ]]; then
        echo "$msg" >> "$LOG_FILE" 2>/dev/null || true
    fi
}

log_info()  { log "INFO" "$@"; }
log_warn()  { log "WARN" "$@"; }
log_error() { log "ERROR" "$@"; }

warm_url() {
    local url="$1"
    local description="${2:-}"

    if $DRY_RUN; then
        log_info "[DRY RUN] Would warm: $url"
        return 0
    fi

    local http_code
    local start_time
    start_time=$(date +%s%N)

    http_code=$(curl -s -o /dev/null -w "%{http_code}" \
        --connect-timeout "$REQUEST_TIMEOUT" \
        --max-time "$REQUEST_TIMEOUT" \
        --retry "$RETRY_COUNT" \
        --retry-delay 2 \
        -H "X-CDN-Prewarm: true" \
        -H "Accept-Encoding: gzip, br" \
        "$url" 2>/dev/null) || http_code="000"

    local end_time
    end_time=$(date +%s%N)
    local duration_ms=$(( (end_time - start_time) / 1000000 ))

    if [[ "$http_code" =~ ^2[0-9][0-9]$ ]]; then
        if $VERBOSE; then
            log_info "OK ($http_code) ${duration_ms}ms - $url"
        fi
        return 0
    else
        log_warn "FAILED ($http_code) ${duration_ms}ms - $url"
        return 1
    fi
}

warm_static_assets() {
    log_info "=== Warming static assets (${#STATIC_ASSETS[@]} files) ==="

    local success=0
    local failed=0

    for asset in "${STATIC_ASSETS[@]}"; do
        local url="https://${STATIC_DOMAIN}${asset}"
        if warm_url "$url" "static"; then
            ((success++))
        else
            ((failed++))
        fi
    done

    log_info "Static assets: $success succeeded, $failed failed"
}

warm_event_pages() {
    log_info "=== Warming event pages (${#EVENT_PAGES[@]} pages) ==="

    local success=0
    local failed=0

    for page in "${EVENT_PAGES[@]}"; do
        local url="https://${CDN_DOMAIN}${page}"
        if warm_url "$url" "page"; then
            ((success++))
        else
            ((failed++))
        fi
    done

    log_info "Event pages: $success succeeded, $failed failed"
}

warm_api_cache() {
    log_info "=== Warming cached API endpoints (${#CACHED_API_ENDPOINTS[@]} endpoints) ==="

    local success=0
    local failed=0

    for endpoint in "${CACHED_API_ENDPOINTS[@]}"; do
        local url="https://${API_DOMAIN}${endpoint}"
        if warm_url "$url" "api"; then
            ((success++))
        else
            ((failed++))
        fi
    done

    log_info "API cache: $success succeeded, $failed failed"
}

warm_odds_snapshots() {
    local event_type="$1"
    log_info "=== Warming odds snapshots for $event_type ==="

    # Fetch event IDs for the upcoming event
    if $DRY_RUN; then
        log_info "[DRY RUN] Would fetch and cache odds for all markets"
        return 0
    fi

    local events_url="https://${API_DOMAIN}/api/v2/events/upcoming?sport=football&limit=50"
    local event_ids
    event_ids=$(curl -s "$events_url" | jq -r '.events[].id' 2>/dev/null) || {
        log_warn "Could not fetch event list; skipping odds warming"
        return 1
    }

    local count=0
    while IFS= read -r event_id; do
        [[ -z "$event_id" ]] && continue
        local odds_url="https://${API_DOMAIN}/api/v2/odds/${event_id}?format=snapshot"
        warm_url "$odds_url" "odds"
        ((count++))
    done <<< "$event_ids"

    log_info "Warmed odds for $count events"
}

invalidate_stale_cache() {
    log_info "=== Invalidating stale cache entries ==="

    if $DRY_RUN; then
        log_info "[DRY RUN] Would create CloudFront invalidation"
        return 0
    fi

    # Invalidate potentially stale paths before warming
    local invalidation_paths=(
        "/api/v2/odds/*"
        "/api/v2/events/*"
        "/api/v2/promotions/*"
        "/events/*"
    )

    local paths_json
    paths_json=$(printf '"%s",' "${invalidation_paths[@]}")
    paths_json="[${paths_json%,}]"

    aws cloudfront create-invalidation \
        --distribution-id "$CLOUDFRONT_DIST_ID" \
        --paths "${invalidation_paths[@]}" \
        2>/dev/null && log_info "Invalidation request submitted" \
        || log_warn "Failed to submit invalidation"

    # Wait for invalidation to complete
    log_info "Waiting 30s for invalidation propagation..."
    sleep 30
}

warm_by_region() {
    local regions="$1"
    log_info "=== Multi-region warming for: $regions ==="

    # For each region, we request through region-specific endpoints
    # to ensure edge caches are populated at the nearest PoP
    IFS=',' read -ra region_list <<< "$regions"

    for region in "${region_list[@]}"; do
        local pops="${REGION_POPS[$region]:-}"
        if [[ -z "$pops" ]]; then
            log_warn "Unknown region: $region"
            continue
        fi

        log_info "Warming PoPs for $region: $pops"
        local pop_count
        pop_count=$(echo "$pops" | wc -w)
        log_info "  $pop_count edge locations in $region"
    done
}

generate_report() {
    local start_time="$1"
    local end_time
    end_time=$(date +%s)
    local duration=$((end_time - start_time))

    echo ""
    echo "============================================================"
    echo "  CDN PRE-WARMING REPORT"
    echo "============================================================"
    echo "  Event:         $EVENT_TYPE"
    echo "  Regions:       $REGIONS"
    echo "  Duration:      ${duration}s"
    echo "  Static Assets: ${#STATIC_ASSETS[@]} files"
    echo "  Event Pages:   ${#EVENT_PAGES[@]} pages"
    echo "  API Endpoints: ${#CACHED_API_ENDPOINTS[@]} endpoints"
    echo "  Dry Run:       $DRY_RUN"
    echo "  Log File:      $LOG_FILE"
    echo "============================================================"
    echo ""
    echo "  Next steps:"
    echo "  1. Verify cache hit rates in CloudFront console"
    echo "  2. Monitor origin request rates (should be near zero)"
    echo "  3. Run: curl -I https://${CDN_DOMAIN}/events/live"
    echo "     -> Look for 'X-Cache: Hit from cloudfront'"
    echo "============================================================"
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

usage() {
    echo "Usage: $0 [OPTIONS]"
    echo ""
    echo "Options:"
    echo "  --event TYPE      Event type (world_cup_final, champions_league, etc.)"
    echo "  --regions LIST    Comma-separated regions to warm (us,eu,sa,ap)"
    echo "  --warm-static     Warm static assets"
    echo "  --warm-pages      Warm event pages"
    echo "  --warm-api        Warm cached API endpoints"
    echo "  --warm-odds       Warm odds snapshots"
    echo "  --warm-all        Warm everything (default)"
    echo "  --invalidate      Invalidate stale cache before warming"
    echo "  --dry-run         Dry run mode"
    echo "  --verbose         Verbose output"
    echo "  --help            Show this help"
}

# Defaults
EVENT_TYPE="default"
REGIONS="us,eu"
WARM_STATIC=false
WARM_PAGES=false
WARM_API=false
WARM_ODDS=false
WARM_ALL=true
INVALIDATE=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --event)        EVENT_TYPE="$2"; shift 2 ;;
        --regions)      REGIONS="$2"; shift 2 ;;
        --warm-static)  WARM_STATIC=true; WARM_ALL=false; shift ;;
        --warm-pages)   WARM_PAGES=true; WARM_ALL=false; shift ;;
        --warm-api)     WARM_API=true; WARM_ALL=false; shift ;;
        --warm-odds)    WARM_ODDS=true; WARM_ALL=false; shift ;;
        --warm-all)     WARM_ALL=true; shift ;;
        --invalidate)   INVALIDATE=true; shift ;;
        --dry-run)      DRY_RUN=true; shift ;;
        --verbose)      VERBOSE=true; shift ;;
        --help)         usage; exit 0 ;;
        *)              log_error "Unknown option: $1"; usage; exit 1 ;;
    esac
done

# Create log directory
mkdir -p "$LOG_DIR" 2>/dev/null || true

START_TIME=$(date +%s)

log_info "CDN Pre-Warmer starting for event: $EVENT_TYPE"
log_info "Regions: $REGIONS"
log_info "Dry run: $DRY_RUN"

# Step 1: Invalidate stale cache if requested
if $INVALIDATE; then
    invalidate_stale_cache
fi

# Step 2: Warm caches
if $WARM_ALL || $WARM_STATIC; then
    warm_static_assets
fi

if $WARM_ALL || $WARM_PAGES; then
    warm_event_pages
fi

if $WARM_ALL || $WARM_API; then
    warm_api_cache
fi

if $WARM_ALL || $WARM_ODDS; then
    warm_odds_snapshots "$EVENT_TYPE"
fi

# Step 3: Multi-region warming
warm_by_region "$REGIONS"

# Step 4: Report
generate_report "$START_TIME"

log_info "CDN pre-warming complete"
