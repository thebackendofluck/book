#!/usr/bin/env bash
# Companion code for "The Backend of Luck" - Chapter 24h, Mutual TLS Between Kubernetes Services for iGaming Platforms.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# scripts/chapter-24h/rotation-chaos-test.sh
# Chaos test for zero-downtime cert rotation under peak casino traffic simulation.
# Distinct from test-mtls.sh: this test applies concurrency chaos (webhook slowdown)
# during rotation, not just load.
# Run: ssh admin@ops-server "bash -s" < scripts/chapter-24h/rotation-chaos-test.sh
set -euo pipefail

NAMESPACE="${1:-player-service}"
SERVICE_URL="${2:-https://player-service.player-service.svc.cluster.local:8443/healthz}"
LOAD_DURATION=180     # seconds — long enough to capture full rotation cycle
ROTATE_AFTER=30       # trigger rotation after load stabilizes
WEBHOOK_CHAOS=true    # simulate cert-manager webhook slowdown during rotation
RESULTS_DIR="/tmp/rotation-chaos-$(date +%s)"

mkdir -p "${RESULTS_DIR}"

echo "=== mTLS Rotation Chaos Test ==="
echo "Namespace:     ${NAMESPACE}"
echo "Service:       ${SERVICE_URL}"
echo "Results dir:   ${RESULTS_DIR}"
echo "Webhook chaos: ${WEBHOOK_CHAOS}"
echo ""

# Verify test client cert exists (extract from existing secret or generate)
if [[ ! -f /tmp/test-client.crt ]]; then
  echo "[0] Extracting test client cert from player-service secret..."
  kubectl get secret player-service-tls -n player-service \
    -o jsonpath='{.data.tls\.crt}' | base64 -d > /tmp/test-client.crt
  kubectl get secret player-service-tls -n player-service \
    -o jsonpath='{.data.tls\.key}' | base64 -d > /tmp/test-client.key
fi

# Record pre-rotation certificate fingerprint
PRE_FINGERPRINT=$(kubectl get secret player-service-tls \
    -n "${NAMESPACE}" \
    -o jsonpath='{.data.tls\.crt}' | base64 -d | \
    openssl x509 -noout -fingerprint -sha256 | awk -F= '{print $2}')
echo "Pre-rotation SHA-256: ${PRE_FINGERPRINT}"

# Phase 1: Sustained load with high concurrency (simulates live event traffic)
echo ""
echo "[1] Starting sustained load — 8 threads, 200 connections (peak casino traffic sim)..."
wrk --duration "${LOAD_DURATION}s" \
    --threads 8 \
    --connections 200 \
    --latency \
    --cert /tmp/test-client.crt \
    --key  /tmp/test-client.key \
    "${SERVICE_URL}" \
    > "${RESULTS_DIR}/wrk-output.txt" 2>&1 &
WRK_PID=$!

# Allow load to ramp up
sleep "${ROTATE_AFTER}"

# Phase 2: Introduce webhook chaos — scale cert-manager webhook to 0 briefly
# This simulates a slow control plane during a high-traffic event window
if [[ "${WEBHOOK_CHAOS}" == "true" ]]; then
  echo ""
  echo "[2] CHAOS: Scaling cert-manager-webhook to 0 for 15 seconds..."
  kubectl scale deployment cert-manager-webhook -n cert-manager --replicas=0

  # Trigger rotation WHILE webhook is unavailable
  echo "[2a] Triggering cert rotation during webhook downtime..."
  kubectl patch certificate player-service-tls \
      -n "${NAMESPACE}" \
      --type merge \
      -p '{"metadata":{"annotations":{"cert-manager.io/force-renewal":"'"$(date +%s)"'"}}}'

  sleep 15

  echo "[2b] Restoring cert-manager-webhook..."
  kubectl scale deployment cert-manager-webhook -n cert-manager --replicas=1
  kubectl rollout status deployment cert-manager-webhook -n cert-manager --timeout=60s
else
  # Standard rotation without chaos
  echo "[2] Triggering certificate rotation..."
  kubectl patch certificate player-service-tls \
      -n "${NAMESPACE}" \
      --type merge \
      -p '{"metadata":{"annotations":{"cert-manager.io/force-renewal":"'"$(date +%s)"'"}}}'
fi

# Phase 3: Wait for rotation to complete
echo ""
echo "[3] Waiting for new certificate to be issued (up to 120s)..."
ROTATION_START=$(date +%s)
kubectl wait --for=condition=Ready certificate/player-service-tls \
    -n "${NAMESPACE}" \
    --timeout=120s
ROTATION_END=$(date +%s)
ROTATION_DURATION=$((ROTATION_END - ROTATION_START))
echo "    Rotation completed in ${ROTATION_DURATION}s"

# Phase 4: Verify new certificate fingerprint differs from pre-rotation
POST_FINGERPRINT=$(kubectl get secret player-service-tls \
    -n "${NAMESPACE}" \
    -o jsonpath='{.data.tls\.crt}' | base64 -d | \
    openssl x509 -noout -fingerprint -sha256 | awk -F= '{print $2}')
echo ""
echo "[4] Certificate fingerprints:"
echo "    Pre-rotation:  ${PRE_FINGERPRINT}"
echo "    Post-rotation: ${POST_FINGERPRINT}"

if [[ "${PRE_FINGERPRINT}" == "${POST_FINGERPRINT}" ]]; then
  echo "    WARNING: Certificate fingerprint did not change — rotation may not have occurred"
else
  echo "    OK: Certificate rotated successfully"
fi

# Wait for load test to complete
wait "${WRK_PID}" || true

# Phase 5: Analyze results
echo ""
echo "=== Load Test Results ==="
cat "${RESULTS_DIR}/wrk-output.txt"

echo ""
echo "=== Error Analysis ==="
NON_2XX=$(grep -E "Non-2xx|Socket errors" "${RESULTS_DIR}/wrk-output.txt" || echo "None detected")
TIMEOUTS=$(grep -E "Timeout" "${RESULTS_DIR}/wrk-output.txt" || echo "None detected")
echo "Non-2xx / Socket errors: ${NON_2XX}"
echo "Timeouts:                ${TIMEOUTS}"

echo ""
echo "=== Latency P99 Spike Check ==="
P99=$(grep "99%" "${RESULTS_DIR}/wrk-output.txt" | awk '{print $2}')
echo "P99 latency during rotation window: ${P99}"
# Acceptable: up to 50ms P99 spike during rotation. Alarm if > 100ms.
P99_NUM=$(echo "${P99}" | sed 's/ms//')
if (( $(echo "${P99_NUM} > 100" | bc -l) )); then
  echo "WARN: P99 latency spike exceeds 100ms threshold — investigate inotify reload path"
fi

echo ""
echo "=== Rotation Chaos Test Complete ==="
echo "Results saved to: ${RESULTS_DIR}/"
