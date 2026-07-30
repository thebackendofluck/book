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

# deploy.sh — Refresh threat data and push to all three platforms.
#
# Usage:
#   ./deploy.sh [--dry-run] [--skip-consolidate] [--platforms redis,dynamodb,cloudflare_kv,aws_waf]
#
# Cron example (daily at 03:15 UTC, non-root):
#   15 3 * * * /opt/ip-detection/unified/deploy.sh >> /var/log/ip-sync-deploy.log 2>&1
#
# Environment variables (set via .env or system environment):
#   REDIS_URL
#   AWS_REGION, DYNAMODB_TABLE, WAF_IP_SET_ID, WAF_IP_SET_NAME, WAF_SCOPE
#   CF_ACCOUNT_ID, CF_API_TOKEN, CF_KV_NAMESPACE_ID
#   SYNC_TTL_HOURS       (default: 168 = 7 days)
#   SYNC_CATEGORIES      (default: tor,vpn,proxy,bot,abuse)
#   PYTHON               (default: python3)
#
# Exit codes:
#   0  All platforms succeeded
#   1  One or more platforms failed (partial success)
#   2  Fatal error (consolidator failed, misconfiguration)

set -euo pipefail

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../../.." && pwd)"
CONSOLIDATE_SCRIPT="${REPO_ROOT}/threat-lists/consolidate-lists.py"
OUTPUT_DIR="${REPO_ROOT}/threat-lists/output"
# shellcheck disable=SC2034  # used in Python import path below
SYNC_MANAGER="${SCRIPT_DIR}/sync_manager.py"

PYTHON="${PYTHON:-python3}"
SYNC_TTL_HOURS="${SYNC_TTL_HOURS:-168}"
SYNC_CATEGORIES="${SYNC_CATEGORIES:-tor,vpn,proxy,bot,abuse}"

DRY_RUN=0
SKIP_CONSOLIDATE=0
PLATFORMS="redis,dynamodb,cloudflare_kv,aws_waf"

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run)
      DRY_RUN=1
      shift
      ;;
    --skip-consolidate)
      SKIP_CONSOLIDATE=1
      shift
      ;;
    --platforms)
      PLATFORMS="$2"
      shift 2
      ;;
    *)
      echo "Unknown argument: $1" >&2
      exit 2
      ;;
  esac
done

# ---------------------------------------------------------------------------
# Logging helpers
# ---------------------------------------------------------------------------

log()  { echo "[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] [INFO]  $*"; }
warn() { echo "[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] [WARN]  $*" >&2; }
die()  { echo "[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] [ERROR] $*" >&2; exit 2; }

# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

log "Starting IP sync deployment (dry_run=${DRY_RUN})"
log "Script dir : ${SCRIPT_DIR}"
log "Platforms  : ${PLATFORMS}"
log "Categories : ${SYNC_CATEGORIES}"
log "TTL hours  : ${SYNC_TTL_HOURS}"

if [[ ! -f "${CONSOLIDATE_SCRIPT}" ]]; then
  warn "consolidate-lists.py not found at ${CONSOLIDATE_SCRIPT}"
  warn "Proceeding with existing output files if present."
  SKIP_CONSOLIDATE=1
fi

if [[ ! -d "${OUTPUT_DIR}" ]]; then
  die "Output directory does not exist: ${OUTPUT_DIR}"
fi

command -v "${PYTHON}" >/dev/null 2>&1 || die "Python not found at: ${PYTHON}"

# ---------------------------------------------------------------------------
# Step 1: Refresh threat data
# ---------------------------------------------------------------------------

if [[ "${SKIP_CONSOLIDATE}" -eq 0 ]]; then
  log "Running consolidate-lists.py..."

  if [[ "${DRY_RUN}" -eq 1 ]]; then
    log "[DRY RUN] Would run: ${PYTHON} ${CONSOLIDATE_SCRIPT} --output-dir ${OUTPUT_DIR}"
  else
    "${PYTHON}" "${CONSOLIDATE_SCRIPT}" \
      --output-dir "${OUTPUT_DIR}" \
      --cache-dir "${REPO_ROOT}/threat-lists/cache"

    CONSOLIDATE_STATUS=$?
    if [[ "${CONSOLIDATE_STATUS}" -ne 0 ]]; then
      die "consolidate-lists.py failed with exit code ${CONSOLIDATE_STATUS}"
    fi
    log "consolidate-lists.py complete."
  fi
else
  log "Skipping consolidator (--skip-consolidate flag set or script not found)."
fi

# ---------------------------------------------------------------------------
# Step 2: Report file sizes (sanity check)
# ---------------------------------------------------------------------------

log "Threat list file sizes:"
for file in tor-exits.txt vpn-ips.txt proxy-ips.txt bot-ips.txt abuse-ips.txt datacenter-ranges.txt; do
  filepath="${OUTPUT_DIR}/${file}"
  if [[ -f "${filepath}" ]]; then
    line_count=$(grep -cv '^#' "${filepath}" || true)
    size_kb=$(du -k "${filepath}" | cut -f1)
    log "  ${file}: ${line_count} entries, ${size_kb}KB"
  else
    warn "  ${file}: NOT FOUND (will be skipped during import)"
  fi
done

# ---------------------------------------------------------------------------
# Step 3: Push to all platforms via sync_manager
# ---------------------------------------------------------------------------

log "Pushing threat data to platforms: ${PLATFORMS}"

if [[ "${DRY_RUN}" -eq 1 ]]; then
  log "[DRY RUN] Would POST to /sync/import-threats"
  log "[DRY RUN] Payload: categories=${SYNC_CATEGORIES} ttl_hours=${SYNC_TTL_HOURS} platforms=${PLATFORMS}"
  exit 0
fi

# Build the platform list as a JSON array for the API payload
PLATFORMS_JSON=$(echo "${PLATFORMS}" | "${PYTHON}" -c "
import sys, json
platforms = sys.stdin.read().strip().split(',')
print(json.dumps([p.strip() for p in platforms if p.strip()]))
")

CATEGORIES_JSON=$(echo "${SYNC_CATEGORIES}" | "${PYTHON}" -c "
import sys, json
cats = sys.stdin.read().strip().split(',')
print(json.dumps([c.strip() for c in cats if c.strip()]))
")

# Check if sync_manager is running as a service (HTTP API mode)
# If SYNC_MANAGER_URL is set, use the HTTP API; otherwise run the Python import directly.

if [[ -n "${SYNC_MANAGER_URL:-}" ]]; then
  log "Using sync_manager HTTP API at ${SYNC_MANAGER_URL}"

  RESPONSE=$(curl -s -w "\n%{http_code}" \
    -X POST "${SYNC_MANAGER_URL}/sync/import-threats" \
    -H "Content-Type: application/json" \
    -d "{
      \"run_consolidator\": false,
      \"categories\": ${CATEGORIES_JSON},
      \"ttl_hours\": ${SYNC_TTL_HOURS},
      \"platforms\": ${PLATFORMS_JSON}
    }" \
  )

  HTTP_STATUS=$(echo "${RESPONSE}" | tail -1)
  RESPONSE_BODY=$(echo "${RESPONSE}" | head -n -1)

  log "HTTP status: ${HTTP_STATUS}"

  if [[ "${HTTP_STATUS}" -ne 200 ]]; then
    warn "import-threats returned HTTP ${HTTP_STATUS}"
    warn "Response: ${RESPONSE_BODY}"
    exit 1
  fi

  # Parse and display per-platform counts
  echo "${RESPONSE_BODY}" | "${PYTHON}" -c "
import sys, json
data = json.load(sys.stdin)
print(f'Entries loaded : {data.get(\"entries_loaded\", 0):,}')
print(f'Categories     : {data.get(\"categories\")}')
print()
print('Per-platform results:')
for p in data.get('platforms', []):
    status = 'OK' if p['success'] else 'FAIL'
    count = p.get('count', 0)
    latency = p.get('latency_ms', 0)
    error = p.get('error') or ''
    print(f'  {p[\"platform\"]:20s} {status:4s}  count={count:>7,}  latency={latency:.0f}ms  {error}')
"

else
  log "SYNC_MANAGER_URL not set — running Python direct import"

  "${PYTHON}" -c "
import asyncio, json, os, sys
sys.path.insert(0, '${SCRIPT_DIR}')

# Set env vars for the sync manager
os.environ.setdefault('THREAT_LIST_OUTPUT_DIR', '${OUTPUT_DIR}')
os.environ.setdefault('CONSOLIDATE_SCRIPT_PATH', '${CONSOLIDATE_SCRIPT}')

from sync_manager import _load_category_ips, _bulk_push_to_platform, _registry

categories = ${CATEGORIES_JSON}
ttl_hours = ${SYNC_TTL_HOURS}

entries = _load_category_ips('${OUTPUT_DIR}', categories, ttl_hours)
print(f'Loaded {len(entries):,} IP entries')

platforms_requested = ${PLATFORMS_JSON}
adapters = _registry.for_platforms(platforms_requested)

if not adapters:
    print('ERROR: No adapters configured. Check environment variables.', file=sys.stderr)
    sys.exit(2)

async def run():
    import asyncio
    tasks = [
        _bulk_push_to_platform(adapter, entries, source='deploy_sh')
        for adapter in adapters.values()
    ]
    results = await asyncio.gather(*tasks)
    success = True
    for r in results:
        status = 'OK' if r.success else 'FAIL'
        error = r.error or ''
        print(f'  {r.platform:20s} {status:4s}  count={r.count:>7,}  latency={r.latency_ms:.0f}ms  {error}')
        if not r.success:
            success = False
    return success

success = asyncio.run(run())
sys.exit(0 if success else 1)
"
fi

PUSH_EXIT_CODE=$?

# ---------------------------------------------------------------------------
# Step 4: Final status report
# ---------------------------------------------------------------------------

if [[ "${PUSH_EXIT_CODE}" -eq 0 ]]; then
  log "Deployment complete. All platforms succeeded."
else
  warn "Deployment completed with one or more platform failures (exit ${PUSH_EXIT_CODE})."
fi

exit "${PUSH_EXIT_CODE}"
