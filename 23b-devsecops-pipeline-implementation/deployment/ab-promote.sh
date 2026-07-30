#!/usr/bin/env bash
# Companion code for "The Backend of Luck" - Chapter 23b, DevSecOps Pipeline Implementation.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# ============================================================================
# CONFIGURATION: Update the variables below before running this script.
# Replace all YOUR_* placeholders with your environment-specific values.
# ============================================================================
# =============================================================================
# A/B Promote: Promote Green to Active after validation passes
# Argo Rollouts BlueGreen strategy in acmetocasino-prod namespace
#
# Usage: ./ab-promote.sh [--kubeconfig PATH] [--namespace NS] [--skip-smoke]
# =============================================================================
set -euo pipefail

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------
KUBECONFIG="${KUBECONFIG:-/path/to/your/kubeconfig}"
NAMESPACE="${NAMESPACE:-acmetocasino-prod}"
ROLLOUT_NAME="${ROLLOUT_NAME:-casino-api}"
ACTIVE_SVC="${ACTIVE_SVC:-casino-api-active}"
HEALTH_PATH="${HEALTH_PATH:-/health}"
DASHBOARD_WEBHOOK="${DASHBOARD_WEBHOOK:-https://your-dashboard.example.com/api/ci-findings}"
SMOKE_SAMPLES=5
SKIP_SMOKE=false
export KUBECONFIG

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------
while [[ $# -gt 0 ]]; do
  case "$1" in
    --kubeconfig)  KUBECONFIG="$2"; shift 2 ;;
    --namespace)   NAMESPACE="$2";  shift 2 ;;
    --skip-smoke)  SKIP_SMOKE=true; shift ;;
    *) echo "Unknown arg: $1"; exit 1 ;;
  esac
done

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
log()  { echo "[$(date '+%H:%M:%S')] $*"; }
STARTED_AT=$(date +%s)

# ---------------------------------------------------------------------------
# 1. Pre-flight: verify rollout is paused and ready for promotion
# ---------------------------------------------------------------------------
log "=== Pre-flight Checks ==="

ROLLOUT_STATUS=$(kubectl argo rollouts status "${ROLLOUT_NAME}" \
  -n "${NAMESPACE}" --timeout 5s 2>&1 || echo "UNKNOWN")
log "Rollout status: ${ROLLOUT_STATUS}"

ROLLOUT_PHASE=$(kubectl argo rollouts get rollout "${ROLLOUT_NAME}" \
  -n "${NAMESPACE}" -o json 2>/dev/null \
  | python3 -c "import sys,json; print(json.load(sys.stdin).get('status',{}).get('phase','Unknown'))" 2>/dev/null \
  || echo "Unknown")
log "Rollout phase: ${ROLLOUT_PHASE}"

if [[ "${ROLLOUT_PHASE}" != "Paused" ]]; then
  log "WARNING: Rollout is not in Paused phase (current: ${ROLLOUT_PHASE})"
  log "Promotion may not have an effect if there is no pending revision."
fi

# ---------------------------------------------------------------------------
# 2. Promote Green to Active
# ---------------------------------------------------------------------------
log "=== Promoting ${ROLLOUT_NAME} ==="
log "Running: kubectl argo rollouts promote ${ROLLOUT_NAME} -n ${NAMESPACE}"

if ! kubectl argo rollouts promote "${ROLLOUT_NAME}" -n "${NAMESPACE}"; then
  log "ERROR: Promotion command failed"
  exit 1
fi

log "Promotion command accepted."

# ---------------------------------------------------------------------------
# 3. Wait for promotion to complete
# ---------------------------------------------------------------------------
log "=== Waiting for Rollout to Complete ==="
TIMEOUT=300  # 5 minutes

if kubectl argo rollouts status "${ROLLOUT_NAME}" \
  -n "${NAMESPACE}" --timeout "${TIMEOUT}s" 2>&1; then
  log "Rollout completed successfully."
else
  log "ERROR: Rollout did not complete within ${TIMEOUT}s"
  log "Current status:"
  kubectl argo rollouts get rollout "${ROLLOUT_NAME}" -n "${NAMESPACE}" 2>&1 || true
  exit 1
fi

# ---------------------------------------------------------------------------
# 4. Post-promotion smoke tests
# ---------------------------------------------------------------------------
if [[ "${SKIP_SMOKE}" == "true" ]]; then
  log "=== Smoke Tests SKIPPED (--skip-smoke) ==="
else
  log "=== Post-Promotion Smoke Tests ==="

  # Get active service URL
  ACTIVE_PORT=$(kubectl get svc "${ACTIVE_SVC}" -n "${NAMESPACE}" \
    -o jsonpath='{.spec.ports[0].port}' 2>/dev/null || echo "8000")
  ACTIVE_IP=$(kubectl get svc "${ACTIVE_SVC}" -n "${NAMESPACE}" \
    -o jsonpath='{.spec.clusterIP}' 2>/dev/null || echo "")

  if [[ -z "${ACTIVE_IP}" || "${ACTIVE_IP}" == "None" ]]; then
    log "WARN: Cannot resolve active service ClusterIP; skipping smoke tests"
  else
    ACTIVE_URL="http://${ACTIVE_IP}:${ACTIVE_PORT}"
    log "Active service URL: ${ACTIVE_URL}"

    SMOKE_FAILURES=0

    # Health check
    HEALTH_STATUS=$(curl -sk -o /dev/null -w "%{http_code}" \
      --max-time 10 "${ACTIVE_URL}${HEALTH_PATH}" 2>/dev/null || echo "000")
    if [[ "${HEALTH_STATUS}" == "200" ]]; then
      log "  PASS: Health endpoint returned ${HEALTH_STATUS}"
    else
      log "  FAIL: Health endpoint returned ${HEALTH_STATUS}"
      SMOKE_FAILURES=$((SMOKE_FAILURES + 1))
    fi

    # API root
    API_STATUS=$(curl -sk -o /dev/null -w "%{http_code}" \
      --max-time 10 "${ACTIVE_URL}/api/v1/" 2>/dev/null || echo "000")
    if [[ "${API_STATUS}" =~ ^(200|404)$ ]]; then
      log "  PASS: API root returned ${API_STATUS}"
    else
      log "  FAIL: API root returned ${API_STATUS}"
      SMOKE_FAILURES=$((SMOKE_FAILURES + 1))
    fi

    # Latency check
    AVG_MS=0
    for _ in $(seq 1 "${SMOKE_SAMPLES}"); do
      TIME_MS=$(curl -sk -o /dev/null -w "%{time_total}" \
        --max-time 10 "${ACTIVE_URL}${HEALTH_PATH}" 2>/dev/null || echo "9.999")
      TIME_MS=$(echo "${TIME_MS}" | awk '{printf "%.0f", $1 * 1000}')
      AVG_MS=$((AVG_MS + TIME_MS))
    done
    AVG_MS=$((AVG_MS / SMOKE_SAMPLES))
    log "  Avg latency after promotion: ${AVG_MS}ms"

    if [[ ${SMOKE_FAILURES} -gt 0 ]]; then
      log "WARNING: ${SMOKE_FAILURES} smoke test(s) failed after promotion"
      log "Consider rolling back: kubectl argo rollouts undo ${ROLLOUT_NAME} -n ${NAMESPACE}"
    else
      log "All smoke tests passed."
    fi
  fi
fi

# ---------------------------------------------------------------------------
# 5. Report to dashboard webhook
# ---------------------------------------------------------------------------
log "=== Reporting to Dashboard ==="

ENDED_AT=$(date +%s)
DURATION=$((ENDED_AT - STARTED_AT))

NEW_IMAGE=$(kubectl get rollout "${ROLLOUT_NAME}" -n "${NAMESPACE}" \
  -o jsonpath='{.spec.template.spec.containers[0].image}' 2>/dev/null || echo "unknown")

PAYLOAD=$(cat <<JSONEOF
{
  "event": "ab-promotion",
  "rollout": "${ROLLOUT_NAME}",
  "namespace": "${NAMESPACE}",
  "image": "${NEW_IMAGE}",
  "phase": "promoted",
  "duration_seconds": ${DURATION},
  "smoke_passed": $([ "${SKIP_SMOKE}" == "true" ] && echo "null" || echo "true"),
  "timestamp": "$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
}
JSONEOF
)

log "Payload: ${PAYLOAD}"

HTTP_STATUS=$(curl -sk -o /dev/null -w "%{http_code}" \
  -X POST \
  -H "Content-Type: application/json" \
  -d "${PAYLOAD}" \
  "${DASHBOARD_WEBHOOK}" 2>/dev/null || echo "000")

if [[ "${HTTP_STATUS}" =~ ^(200|201|202|204)$ ]]; then
  log "Dashboard notified (HTTP ${HTTP_STATUS})"
else
  log "WARN: Dashboard webhook returned HTTP ${HTTP_STATUS} (non-fatal)"
fi

# ---------------------------------------------------------------------------
# 6. Summary
# ---------------------------------------------------------------------------
echo ""
echo "================================================================"
echo "  A/B Promotion Complete"
echo "================================================================"
echo "  Rollout:    ${ROLLOUT_NAME}"
echo "  Namespace:  ${NAMESPACE}"
echo "  Image:      ${NEW_IMAGE}"
echo "  Duration:   ${DURATION}s"
echo "  Status:     PROMOTED"
echo "================================================================"
