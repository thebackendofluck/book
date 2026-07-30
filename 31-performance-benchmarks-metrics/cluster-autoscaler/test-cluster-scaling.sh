#!/bin/bash
# Companion code for "The Backend of Luck" - Chapter 31, Performance Benchmarks and Metrics.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# shellcheck disable=SC1090,SC1091,SC2329
# test-cluster-scaling.sh
# End-to-end test for K3s cluster node autoscaling.
# Simulates load, verifies scale-up, reduces load, verifies scale-down.
#
# Usage:
#   ./test-cluster-scaling.sh                # full test
#   ./test-cluster-scaling.sh --quick        # add/remove one worker only
#   ./test-cluster-scaling.sh --stress-only  # just run load test (no scale)

set -euo pipefail

# ─── Colours ───────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; BOLD='\033[1m'; NC='\033[0m'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONF_FILE="${K3S_AUTOSCALER_CONF:-/etc/k3s-autoscaler.conf}"
[[ -f "$CONF_FILE" ]] && source "$CONF_FILE"
[[ -f "${SCRIPT_DIR}/k3s-autoscaler.conf" ]] && source "${SCRIPT_DIR}/k3s-autoscaler.conf"

LOG_FILE="/tmp/test-cluster-scaling.log"
RESULTS_FILE="/tmp/test-cluster-scaling-results.txt"

pass() { echo -e "${GREEN}[PASS]${NC} $*" | tee -a "$LOG_FILE"; }
fail_msg() { echo -e "${RED}[FAIL]${NC} $*" | tee -a "$LOG_FILE"; }
info() { echo -e "${BLUE}[..]${NC}   $*" | tee -a "$LOG_FILE"; }
warn() { echo -e "${YELLOW}[!!]${NC}  $*" | tee -a "$LOG_FILE"; }
banner() {
    echo -e "\n${BOLD}══════════════════════════════════════════════════${NC}" | tee -a "$LOG_FILE"
    echo -e "${BOLD} $*${NC}" | tee -a "$LOG_FILE"
    echo -e "${BOLD}══════════════════════════════════════════════════${NC}" | tee -a "$LOG_FILE"
}

MODE="full"
[[ "${1:-}" == "--quick" ]] && MODE="quick"
[[ "${1:-}" == "--stress-only" ]] && MODE="stress"

TEST_WORKER_NAME="${WORKER_NAME_PREFIX:-k3s-autoscale-worker}-test"
TEST_WORKER_IP="${WORKER_IP_BASE:-10.0.10}.49"
STRESS_NAMESPACE="autoscaler-test"

echo "=== Test started: $(date) ===" > "$LOG_FILE"
echo "" > "$RESULTS_FILE"
TOTAL_START=$(date +%s)
TESTS_PASSED=0
TESTS_FAILED=0

record() {
    local status="$1" test_name="$2" elapsed="$3"
    echo "${status} | ${test_name} | ${elapsed}s" >> "$RESULTS_FILE"
    if [[ "$status" == "PASS" ]]; then
        TESTS_PASSED=$(( TESTS_PASSED + 1 ))
    else
        TESTS_FAILED=$(( TESTS_FAILED + 1 ))
    fi
}

cleanup() {
    info "Cleaning up test artifacts..."
    k3s kubectl delete namespace "$STRESS_NAMESPACE" --ignore-not-found 2>/dev/null || true
    if k3s kubectl get node "$TEST_WORKER_NAME" &>/dev/null; then
        "${SCRIPT_DIR}/k3s-remove-worker.sh" --name "$TEST_WORKER_NAME" 2>/dev/null || true
    fi
    if virsh list --all --name 2>/dev/null | grep -q "^${TEST_WORKER_NAME}$"; then
        virsh destroy "$TEST_WORKER_NAME" 2>/dev/null || true
        virsh undefine "$TEST_WORKER_NAME" --remove-all-storage 2>/dev/null || true
    fi
}

# Trap for cleanup on exit
trap 'warn "Test interrupted — cleaning up..."; cleanup' EXIT

# ═══════════════════════════════════════════════════════════════════════════
# Test 1: Preflight checks
# ═══════════════════════════════════════════════════════════════════════════
banner "Test 1: Preflight Checks"
T1_START=$(date +%s)

# K3s running?
if k3s kubectl get nodes &>/dev/null; then
    pass "K3s cluster accessible"
else
    fail_msg "Cannot reach K3s cluster"
    record "FAIL" "preflight_k3s" "0"
    exit 1
fi

# Golden image exists?
if [[ -f "${GOLDEN_IMAGE:-/nvme-0-zfs/vms/golden/noble-golden-generic.qcow2}" ]]; then
    pass "Golden image exists"
else
    fail_msg "Golden image not found"
    record "FAIL" "preflight_golden_image" "0"
    exit 1
fi

# Scripts exist?
for script in k3s-add-worker.sh k3s-remove-worker.sh k3s-node-autoscaler.sh k3s-cluster-status.sh; do
    if [[ -x "${SCRIPT_DIR}/${script}" ]]; then
        pass "Script executable: ${script}"
    else
        fail_msg "Script missing or not executable: ${script}"
        record "FAIL" "preflight_scripts" "0"
        exit 1
    fi
done

# Test IP not in use?
if ping -c 1 -W 1 "${TEST_WORKER_IP}" &>/dev/null; then
    fail_msg "Test IP ${TEST_WORKER_IP} is already in use"
    record "FAIL" "preflight_ip" "0"
    exit 1
fi
pass "Test IP ${TEST_WORKER_IP} is available"

record "PASS" "preflight" "$(( $(date +%s) - T1_START ))"

# ═══════════════════════════════════════════════════════════════════════════
# Test 2: Add worker node
# ═══════════════════════════════════════════════════════════════════════════
if [[ "$MODE" != "stress" ]]; then
    banner "Test 2: Add Worker Node (small: 2 CPU, 4GB)"
    T2_START=$(date +%s)

    info "Adding test worker: ${TEST_WORKER_NAME} (${TEST_WORKER_IP})"
    if "${SCRIPT_DIR}/k3s-add-worker.sh" \
        --name "$TEST_WORKER_NAME" \
        --ip "$TEST_WORKER_IP" \
        --cpu 2 \
        --ram 4096 \
        --disk 20 2>&1 | tee -a "$LOG_FILE"; then

        T2_ELAPSED=$(( $(date +%s) - T2_START ))

        # Verify node is Ready
        NODE_STATUS=$(k3s kubectl get node "$TEST_WORKER_NAME" \
            -o jsonpath='{.status.conditions[?(@.type=="Ready")].status}' 2>/dev/null || echo "NotFound")

        if [[ "$NODE_STATUS" == "True" ]]; then
            pass "Worker added and Ready in ${T2_ELAPSED}s"
            record "PASS" "add_worker" "$T2_ELAPSED"
        else
            fail_msg "Worker added but not Ready (status: ${NODE_STATUS})"
            record "FAIL" "add_worker" "$T2_ELAPSED"
        fi

        # Verify labels
        LABELS=$(k3s kubectl get node "$TEST_WORKER_NAME" \
            -o jsonpath='{.metadata.labels}' 2>/dev/null)
        if echo "$LABELS" | grep -q "autoscaler-managed"; then
            pass "Autoscaler label present"
        else
            fail_msg "Autoscaler label missing"
        fi

        # Show node info
        info "Node details:"
        k3s kubectl get node "$TEST_WORKER_NAME" -o wide

    else
        T2_ELAPSED=$(( $(date +%s) - T2_START ))
        fail_msg "Failed to add worker (${T2_ELAPSED}s)"
        record "FAIL" "add_worker" "$T2_ELAPSED"
    fi
fi

# ═══════════════════════════════════════════════════════════════════════════
# Test 3: Cluster status dashboard
# ═══════════════════════════════════════════════════════════════════════════
banner "Test 3: Cluster Status Dashboard"
T3_START=$(date +%s)

if "${SCRIPT_DIR}/k3s-cluster-status.sh" 2>&1 | tee -a "$LOG_FILE"; then
    pass "Dashboard script works"
    record "PASS" "dashboard" "$(( $(date +%s) - T3_START ))"
else
    fail_msg "Dashboard script failed"
    record "FAIL" "dashboard" "$(( $(date +%s) - T3_START ))"
fi

# ═══════════════════════════════════════════════════════════════════════════
# Test 4: Stress test (create pending pods)
# ═══════════════════════════════════════════════════════════════════════════
if [[ "$MODE" == "full" || "$MODE" == "stress" ]]; then
    banner "Test 4: Generate Load (CPU stress pods)"
    T4_START=$(date +%s)

    k3s kubectl create namespace "$STRESS_NAMESPACE" 2>/dev/null || true

    # Deploy stress pods
    info "Deploying 10 CPU stress pods..."
    cat <<'STRESSYAML' | k3s kubectl apply -n "$STRESS_NAMESPACE" -f -
apiVersion: apps/v1
kind: Deployment
metadata:
  name: cpu-stress
spec:
  replicas: 10
  selector:
    matchLabels:
      app: cpu-stress
  template:
    metadata:
      labels:
        app: cpu-stress
    spec:
      containers:
      - name: stress
        image: polinux/stress
        command: ["stress"]
        args: ["--cpu", "1", "--timeout", "300s"]
        resources:
          requests:
            cpu: "500m"
            memory: "128Mi"
          limits:
            cpu: "1"
            memory: "256Mi"
STRESSYAML

    info "Waiting 30s for pods to schedule..."
    sleep 30

    RUNNING=$(k3s kubectl get pods -n "$STRESS_NAMESPACE" --no-headers 2>/dev/null | grep -c "Running" || echo "0")
    PENDING=$(k3s kubectl get pods -n "$STRESS_NAMESPACE" --no-headers 2>/dev/null | grep -c "Pending" || echo "0")
    info "Stress pods: ${RUNNING} running, ${PENDING} pending"

    if [[ "$RUNNING" -gt 0 ]]; then
        pass "Stress pods running (${RUNNING}/${PENDING})"
        record "PASS" "stress_deploy" "$(( $(date +%s) - T4_START ))"
    else
        warn "No stress pods running yet"
        record "WARN" "stress_deploy" "$(( $(date +%s) - T4_START ))"
    fi

    # Test autoscaler dry-run evaluation
    banner "Test 4b: Autoscaler Evaluation (dry-run)"
    info "Running autoscaler in dry-run mode..."
    "${SCRIPT_DIR}/k3s-node-autoscaler.sh" --dry-run 2>&1 | tee -a "$LOG_FILE" || true

    # Cleanup stress
    info "Cleaning up stress pods..."
    k3s kubectl delete namespace "$STRESS_NAMESPACE" --ignore-not-found 2>/dev/null || true
fi

# ═══════════════════════════════════════════════════════════════════════════
# Test 5: Remove worker node
# ═══════════════════════════════════════════════════════════════════════════
if [[ "$MODE" != "stress" ]]; then
    banner "Test 5: Remove Worker Node"
    T5_START=$(date +%s)

    if k3s kubectl get node "$TEST_WORKER_NAME" &>/dev/null; then
        info "Removing test worker: ${TEST_WORKER_NAME}"
        if "${SCRIPT_DIR}/k3s-remove-worker.sh" --name "$TEST_WORKER_NAME" 2>&1 | tee -a "$LOG_FILE"; then
            T5_ELAPSED=$(( $(date +%s) - T5_START ))

            # Verify node gone
            if k3s kubectl get node "$TEST_WORKER_NAME" &>/dev/null; then
                fail_msg "Node still in cluster after removal"
                record "FAIL" "remove_worker" "$T5_ELAPSED"
            else
                pass "Worker removed in ${T5_ELAPSED}s"
                record "PASS" "remove_worker" "$T5_ELAPSED"
            fi

            # Verify VM gone
            if virsh list --all --name 2>/dev/null | grep -q "^${TEST_WORKER_NAME}$"; then
                fail_msg "VM still exists after removal"
            else
                pass "VM destroyed"
            fi
        else
            T5_ELAPSED=$(( $(date +%s) - T5_START ))
            fail_msg "Remove script failed (${T5_ELAPSED}s)"
            record "FAIL" "remove_worker" "$T5_ELAPSED"
        fi
    else
        warn "Test worker not found — skipping removal test"
        record "SKIP" "remove_worker" "0"
    fi
fi

# ═══════════════════════════════════════════════════════════════════════════
# Results summary
# ═══════════════════════════════════════════════════════════════════════════
TOTAL_ELAPSED=$(( $(date +%s) - TOTAL_START ))

# Disable exit trap cleanup (tests completed normally)
trap - EXIT

banner "Test Results Summary"

echo ""
printf "  %-25s %8s %10s\n" "TEST" "STATUS" "TIME"
printf "  %-25s %8s %10s\n" "----" "------" "----"
while IFS='|' read -r status name elapsed; do
    status=$(echo "$status" | xargs)
    name=$(echo "$name" | xargs)
    elapsed=$(echo "$elapsed" | xargs)
    color="$GREEN"
    [[ "$status" == "FAIL" ]] && color="$RED"
    [[ "$status" == "WARN" || "$status" == "SKIP" ]] && color="$YELLOW"
    printf "  %-25s ${color}%8s${NC} %10s\n" "$name" "$status" "$elapsed"
done < "$RESULTS_FILE"

echo ""
echo -e "  Total time: ${BOLD}${TOTAL_ELAPSED}s${NC} ($(( TOTAL_ELAPSED / 60 ))m $(( TOTAL_ELAPSED % 60 ))s)"
echo -e "  Passed: ${GREEN}${TESTS_PASSED}${NC}  Failed: ${RED}${TESTS_FAILED}${NC}"

if [[ $TESTS_FAILED -eq 0 ]]; then
    echo ""
    pass "All tests passed"
    exit 0
else
    echo ""
    fail_msg "${TESTS_FAILED} test(s) failed"
    exit 1
fi
