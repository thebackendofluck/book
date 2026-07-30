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
# A/B Validation: Blue (active) vs Green (preview) for casino-api
# Argo Rollouts BlueGreen strategy in acmetocasino-prod namespace
#
# Usage: ./ab-validation.sh [--kubeconfig PATH] [--namespace NS] [--samples N]
# Exit 1 if Green is worse than Blue on any metric.
# =============================================================================
set -euo pipefail

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------
KUBECONFIG="${KUBECONFIG:-/path/to/your/kubeconfig}"
NAMESPACE="${NAMESPACE:-acmetocasino-prod}"
ROLLOUT_NAME="${ROLLOUT_NAME:-casino-api}"
SAMPLES="${SAMPLES:-20}"
HEALTH_PATH="${HEALTH_PATH:-/health}"
LATENCY_THRESHOLD_MS="${LATENCY_THRESHOLD_MS:-500}"
export KUBECONFIG

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------
while [[ $# -gt 0 ]]; do
  case "$1" in
    --kubeconfig) KUBECONFIG="$2"; shift 2 ;;
    --namespace)  NAMESPACE="$2";  shift 2 ;;
    --samples)    SAMPLES="$2";    shift 2 ;;
    *) echo "Unknown arg: $1"; exit 1 ;;
  esac
done

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
log()  { echo "[$(date '+%H:%M:%S')] $*"; }
pass() { echo "  PASS: $*"; }
fail() { echo "  FAIL: $*"; FAILURES=$((FAILURES + 1)); }

FAILURES=0

# ---------------------------------------------------------------------------
# 1. Discover Blue (active) and Green (preview) service endpoints
# ---------------------------------------------------------------------------
log "Discovering services for rollout ${ROLLOUT_NAME} in ${NAMESPACE}"

ACTIVE_SVC=$(kubectl argo rollouts get rollout "${ROLLOUT_NAME}" \
  -n "${NAMESPACE}" -o json 2>/dev/null \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('status',{}).get('blueGreen',{}).get('activeSelector',''))" 2>/dev/null \
  || echo "")

PREVIEW_SVC=$(kubectl argo rollouts get rollout "${ROLLOUT_NAME}" \
  -n "${NAMESPACE}" -o json 2>/dev/null \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('status',{}).get('blueGreen',{}).get('previewSelector',''))" 2>/dev/null \
  || echo "")

# Fallback: derive from naming convention
if [[ -z "${ACTIVE_SVC}" ]]; then
  ACTIVE_SVC="${ROLLOUT_NAME}-active"
  log "Active selector not found in rollout status; using convention: ${ACTIVE_SVC}"
fi
if [[ -z "${PREVIEW_SVC}" ]]; then
  PREVIEW_SVC="${ROLLOUT_NAME}-preview"
  log "Preview selector not found in rollout status; using convention: ${PREVIEW_SVC}"
fi

# Port-forward or use ClusterIP directly via kubectl proxy
get_svc_url() {
  local svc="$1"
  local port
  port=$(kubectl get svc "${svc}" -n "${NAMESPACE}" -o jsonpath='{.spec.ports[0].port}' 2>/dev/null || echo "8000")
  local cluster_ip
  cluster_ip=$(kubectl get svc "${svc}" -n "${NAMESPACE}" -o jsonpath='{.spec.clusterIP}' 2>/dev/null || echo "")
  if [[ -n "${cluster_ip}" && "${cluster_ip}" != "None" ]]; then
    echo "http://${cluster_ip}:${port}"
  else
    echo ""
  fi
}

BLUE_URL=$(get_svc_url "${ACTIVE_SVC}")
GREEN_URL=$(get_svc_url "${PREVIEW_SVC}")

if [[ -z "${BLUE_URL}" ]]; then
  log "ERROR: Cannot resolve Blue (active) service URL for ${ACTIVE_SVC}"
  exit 1
fi
if [[ -z "${GREEN_URL}" ]]; then
  log "ERROR: Cannot resolve Green (preview) service URL for ${PREVIEW_SVC}"
  exit 1
fi

log "Blue  (active):  ${BLUE_URL}"
log "Green (preview): ${GREEN_URL}"

# ---------------------------------------------------------------------------
# 2. Health check comparison
# ---------------------------------------------------------------------------
log "=== Health Check ==="

check_health() {
  local label="$1" url="$2"
  local status
  status=$(curl -sk -o /tmp/ab_health_"${label}".json -w "%{http_code}" \
    --max-time 10 "${url}${HEALTH_PATH}" 2>/dev/null || echo "000")
  echo "${status}"
}

BLUE_HEALTH=$(check_health "blue" "${BLUE_URL}")
GREEN_HEALTH=$(check_health "green" "${GREEN_URL}")

if [[ "${GREEN_HEALTH}" == "200" ]]; then
  pass "Green health: ${GREEN_HEALTH}"
else
  fail "Green health: ${GREEN_HEALTH} (Blue: ${BLUE_HEALTH})"
fi

# ---------------------------------------------------------------------------
# 3. Response time comparison (curl loop)
# ---------------------------------------------------------------------------
log "=== Response Time Comparison (${SAMPLES} samples) ==="

measure_latency() {
  local url="$1" samples="$2"
  local total=0 count=0 time_ms
  for _ in $(seq 1 "${samples}"); do
    time_ms=$(curl -sk -o /dev/null -w "%{time_total}" \
      --max-time 10 "${url}${HEALTH_PATH}" 2>/dev/null || echo "9.999")
    # Convert seconds to milliseconds
    time_ms=$(echo "${time_ms}" | awk '{printf "%.0f", $1 * 1000}')
    total=$((total + time_ms))
    count=$((count + 1))
  done
  if [[ ${count} -gt 0 ]]; then
    echo $((total / count))
  else
    echo "9999"
  fi
}

BLUE_AVG_MS=$(measure_latency "${BLUE_URL}" "${SAMPLES}")
GREEN_AVG_MS=$(measure_latency "${GREEN_URL}" "${SAMPLES}")

log "Blue  avg latency: ${BLUE_AVG_MS}ms"
log "Green avg latency: ${GREEN_AVG_MS}ms"

# Green must not be more than 50% slower than Blue
if [[ ${BLUE_AVG_MS} -gt 0 ]]; then
  THRESHOLD=$((BLUE_AVG_MS * 150 / 100))
  if [[ ${GREEN_AVG_MS} -le ${THRESHOLD} ]]; then
    pass "Green latency (${GREEN_AVG_MS}ms) within 150% of Blue (${BLUE_AVG_MS}ms)"
  else
    fail "Green latency (${GREEN_AVG_MS}ms) exceeds 150% of Blue (${BLUE_AVG_MS}ms, threshold=${THRESHOLD}ms)"
  fi
fi

# Absolute threshold
if [[ ${GREEN_AVG_MS} -le ${LATENCY_THRESHOLD_MS} ]]; then
  pass "Green latency (${GREEN_AVG_MS}ms) under absolute threshold (${LATENCY_THRESHOLD_MS}ms)"
else
  fail "Green latency (${GREEN_AVG_MS}ms) exceeds absolute threshold (${LATENCY_THRESHOLD_MS}ms)"
fi

# ---------------------------------------------------------------------------
# 4. Response schema comparison
# ---------------------------------------------------------------------------
log "=== Response Schema Comparison ==="

extract_keys() {
  local file="$1"
  python3 -c "
import json, sys
try:
    data = json.load(open('${file}'))
    def get_keys(obj, prefix=''):
        keys = []
        if isinstance(obj, dict):
            for k, v in sorted(obj.items()):
                path = f'{prefix}.{k}' if prefix else k
                keys.append(path)
                keys.extend(get_keys(v, path))
        elif isinstance(obj, list) and obj:
            keys.extend(get_keys(obj[0], f'{prefix}[]'))
        return keys
    for k in get_keys(data):
        print(k)
except Exception as e:
    print(f'ERROR: {e}', file=sys.stderr)
" 2>/dev/null || true
}

BLUE_KEYS=$(extract_keys /tmp/ab_health_blue.json)
GREEN_KEYS=$(extract_keys /tmp/ab_health_green.json)

if [[ -n "${BLUE_KEYS}" && -n "${GREEN_KEYS}" ]]; then
  MISSING=$(comm -23 <(echo "${BLUE_KEYS}" | sort) <(echo "${GREEN_KEYS}" | sort) || true)
  if [[ -z "${MISSING}" ]]; then
    pass "Green schema contains all Blue keys"
  else
    fail "Green schema missing keys present in Blue: ${MISSING}"
  fi
else
  log "WARN: Could not compare schemas (empty response)"
fi

# ---------------------------------------------------------------------------
# 5. CVE comparison via Trivy (Green vs Blue images)
# ---------------------------------------------------------------------------
log "=== CVE Comparison (Trivy) ==="

# Get image from active and preview replicasets
get_image_from_rs() {
  local selector="$1"
  kubectl get pods -n "${NAMESPACE}" -l "app=${ROLLOUT_NAME},${selector}" \
    -o jsonpath='{.items[0].spec.containers[0].image}' 2>/dev/null || echo ""
}

BLUE_IMG=$(get_image_from_rs "rollouts-pod-template-hash" 2>/dev/null || echo "")
GREEN_IMG=$(kubectl get rollout "${ROLLOUT_NAME}" -n "${NAMESPACE}" \
  -o jsonpath='{.spec.template.spec.containers[0].image}' 2>/dev/null || echo "")

if command -v trivy &>/dev/null && [[ -n "${GREEN_IMG}" ]]; then
  log "Scanning Green image: ${GREEN_IMG}"
  GREEN_CVES=$(trivy image --severity CRITICAL,HIGH --quiet --format json \
    "${GREEN_IMG}" 2>/dev/null \
    | python3 -c "import sys,json; d=json.load(sys.stdin); print(sum(len(r.get('Vulnerabilities',[])) for r in d.get('Results',[])))" 2>/dev/null \
    || echo "N/A")

  if [[ -n "${BLUE_IMG}" ]]; then
    log "Scanning Blue image: ${BLUE_IMG}"
    BLUE_CVES=$(trivy image --severity CRITICAL,HIGH --quiet --format json \
      "${BLUE_IMG}" 2>/dev/null \
      | python3 -c "import sys,json; d=json.load(sys.stdin); print(sum(len(r.get('Vulnerabilities',[])) for r in d.get('Results',[])))" 2>/dev/null \
      || echo "N/A")
  else
    BLUE_CVES="N/A"
  fi

  log "Blue CVEs (CRITICAL+HIGH): ${BLUE_CVES}"
  log "Green CVEs (CRITICAL+HIGH): ${GREEN_CVES}"

  if [[ "${GREEN_CVES}" != "N/A" && "${BLUE_CVES}" != "N/A" ]]; then
    if [[ ${GREEN_CVES} -le ${BLUE_CVES} ]]; then
      pass "Green has same or fewer CVEs than Blue (${GREEN_CVES} <= ${BLUE_CVES})"
    else
      fail "Green has MORE CVEs than Blue (${GREEN_CVES} > ${BLUE_CVES})"
    fi
  else
    log "WARN: Trivy scan incomplete; skipping CVE comparison"
  fi
else
  log "WARN: trivy not available or Green image unknown; skipping CVE scan"
  log "  Install: curl -sfL https://raw.githubusercontent.com/aquasecurity/trivy/main/contrib/install.sh | sh"
fi

# ---------------------------------------------------------------------------
# 6. Final report
# ---------------------------------------------------------------------------
echo ""
echo "================================================================"
echo "  A/B Validation Report"
echo "================================================================"
echo "  Rollout:       ${ROLLOUT_NAME}"
echo "  Namespace:     ${NAMESPACE}"
echo "  Blue URL:      ${BLUE_URL}"
echo "  Green URL:     ${GREEN_URL}"
echo "  Blue Health:   ${BLUE_HEALTH}"
echo "  Green Health:  ${GREEN_HEALTH}"
echo "  Blue Latency:  ${BLUE_AVG_MS}ms"
echo "  Green Latency: ${GREEN_AVG_MS}ms"
echo "  Samples:       ${SAMPLES}"
echo "  Failures:      ${FAILURES}"
echo "================================================================"

if [[ ${FAILURES} -gt 0 ]]; then
  echo "  VERDICT: FAIL — Green is worse than Blue"
  echo "  DO NOT promote. Investigate and fix before retrying."
  echo "================================================================"
  exit 1
else
  echo "  VERDICT: PASS — Green is ready for promotion"
  echo "  Run: ./ab-promote.sh to promote Green to active"
  echo "================================================================"
  exit 0
fi
