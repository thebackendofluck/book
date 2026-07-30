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

# run-all-scanners.sh — Run all security scanners locally
# Usage: ./run-all-scanners.sh [--target-url URL] [--source-dir DIR] [--kubeconfig PATH]
set -euo pipefail

# ─── Defaults ────────────────────────────────────────────────
TARGET_URL="${SCAN_TARGET_URL:-http://localhost:8080}"
SOURCE_DIR="${SCAN_SOURCE_DIR:-$(git rev-parse --show-toplevel 2>/dev/null || pwd)}"
KUBECONFIG_PATH="${KUBECONFIG:-/etc/rancher/k3s/k3s.yaml}"
OUTPUT_DIR="${SCAN_OUTPUT_DIR:-/tmp/security-scan-$(date +%Y%m%d-%H%M%S)}"
NUCLEI_SEVERITY="${NUCLEI_SEVERITY:-critical,high,medium}"
FAIL_ON_CRITICAL="${FAIL_ON_CRITICAL:-true}"

# Parse args
while [[ $# -gt 0 ]]; do
  case $1 in
    --target-url)   TARGET_URL="$2";      shift 2 ;;
    --source-dir)   SOURCE_DIR="$2";      shift 2 ;;
    --kubeconfig)   KUBECONFIG_PATH="$2"; shift 2 ;;
    --output-dir)   OUTPUT_DIR="$2";      shift 2 ;;
    --no-fail)      FAIL_ON_CRITICAL="false"; shift ;;
    *) echo "Unknown flag: $1"; exit 1 ;;
  esac
done

mkdir -p "$OUTPUT_DIR"
LOG="$OUTPUT_DIR/scanner.log"
SUMMARY="$OUTPUT_DIR/summary.txt"

RED='\033[0;31m'; YELLOW='\033[1;33m'; GREEN='\033[0;32m'; NC='\033[0m'
BOLD='\033[1m'

log() { echo -e "$*" | tee -a "$LOG"; }
pass() { log "${GREEN}[PASS]${NC} $*"; }
warn() { log "${YELLOW}[WARN]${NC} $*"; }
fail() { log "${RED}[FAIL]${NC} $*"; }
header() { log "\n${BOLD}═══ $* ═══${NC}"; }

TOTAL_CRITICAL=0
FAILED_TOOLS=()

# ─── Tool check ──────────────────────────────────────────────
check_tool() {
  local tool="$1" install_hint="$2"
  if ! command -v "$tool" &>/dev/null; then
    warn "$tool not found. $install_hint"
    return 1
  fi
  return 0
}

header "Security Scan — $(date)"
log "Source:     $SOURCE_DIR"
log "Target URL: $TARGET_URL"
log "Output:     $OUTPUT_DIR"
log "Kubeconfig: $KUBECONFIG_PATH"
echo ""

# ─── 1. Gitleaks ─────────────────────────────────────────────
header "1/5  Gitleaks — Secret Scanning"
GITLEAKS_REPORT="$OUTPUT_DIR/gitleaks.json"

if check_tool gitleaks "curl -sL https://github.com/gitleaks/gitleaks/releases/latest/download/gitleaks_8.30.1_linux_x64.tar.gz | tar xz -C /usr/local/bin/ gitleaks"; then
  set +e
  gitleaks detect \
    --source "$SOURCE_DIR" \
    --report-path "$GITLEAKS_REPORT" \
    --report-format json \
    2>>"$LOG"
  GITLEAKS_EXIT=$?
  set -e

  if [[ -f "$GITLEAKS_REPORT" ]]; then
    LEAKS=$(python3 -c "import json; d=json.load(open('$GITLEAKS_REPORT')); print(len(d) if isinstance(d,list) else 0)" 2>/dev/null || echo "0")
    if [[ "$LEAKS" -gt 0 ]]; then
      fail "Gitleaks: $LEAKS secret(s) found → $GITLEAKS_REPORT"
      TOTAL_CRITICAL=$((TOTAL_CRITICAL + LEAKS))
      FAILED_TOOLS+=("gitleaks")
      # Print findings
      python3 - <<EOF
import json
with open('$GITLEAKS_REPORT') as f:
    data = json.load(f)
if isinstance(data, list):
    for d in data[:5]:
        print(f"  Secret: {d.get('RuleID','?')} | {d.get('File','?')}:{d.get('StartLine','?')}")
EOF
    else
      pass "Gitleaks: No secrets found"
    fi
  else
    warn "Gitleaks: No report generated (possibly no git history)"
  fi
else
  warn "Gitleaks: SKIPPED (not installed)"
fi

# ─── 2. Checkov ──────────────────────────────────────────────
header "2/5  Checkov — IaC Security (Terraform + K8s)"
CHECKOV_TF="$OUTPUT_DIR/checkov-terraform.json"
CHECKOV_K8S="$OUTPUT_DIR/checkov-kubernetes.json"

CHECKOV_BIN="checkov"
[[ -x /opt/checkov-venv/bin/checkov ]] && CHECKOV_BIN="/opt/checkov-venv/bin/checkov"

if check_tool "$CHECKOV_BIN" "pip install checkov  OR  python3 -m venv /opt/checkov-venv && /opt/checkov-venv/bin/pip install checkov"; then
  # Terraform
  TF_FILES=$(find "$SOURCE_DIR" -name '*.tf' | head -1)
  if [[ -n "$TF_FILES" ]]; then
    "$CHECKOV_BIN" -d "$SOURCE_DIR" --framework terraform --output json 2>/dev/null > "$CHECKOV_TF" || true
    python3 - <<EOF
import json
try:
    data = json.load(open('$CHECKOV_TF'))
    p = len(data.get('results',{}).get('passed_checks',[]))
    f = len(data.get('results',{}).get('failed_checks',[]))
    pct = int(p/(p+f)*100) if (p+f)>0 else 100
    print(f"  Terraform: Passed={p} Failed={f} ({pct}% passing)")
    # Critical checks
    critical_ids = {'CKV_AWS_17','CKV_AWS_8','CKV_AWS_18','CKV_AWS_21'}
    for c in data.get('results',{}).get('failed_checks',[]):
        if c.get('check_id') in critical_ids:
            print(f"  [CRITICAL] {c['check_id']} — {c.get('check_name','?')}")
except Exception as e:
    print(f"  Parse error: {e}")
EOF
  else
    warn "Checkov TF: No .tf files found in $SOURCE_DIR"
  fi

  # Kubernetes
  K8S_FILES=$(find "$SOURCE_DIR" -name '*.yaml' -o -name '*.yml' | xargs grep -l 'kind:' 2>/dev/null | head -1 || true)
  if [[ -n "$K8S_FILES" ]]; then
    "$CHECKOV_BIN" -d "$SOURCE_DIR" --framework kubernetes --output json 2>/dev/null > "$CHECKOV_K8S" || true
    python3 - <<EOF
import json
try:
    data = json.load(open('$CHECKOV_K8S'))
    p = len(data.get('results',{}).get('passed_checks',[]))
    f = len(data.get('results',{}).get('failed_checks',[]))
    pct = int(p/(p+f)*100) if (p+f)>0 else 100
    print(f"  Kubernetes: Passed={p} Failed={f} ({pct}% passing)")
    # Highlight privileged container
    for c in data.get('results',{}).get('failed_checks',[]):
        if c.get('check_id') in ('CKV_K8S_16','CKV_K8S_6','CKV_K8S_28'):
            print(f"  [HIGH] {c['check_id']} — {c.get('check_name','?')}")
except Exception as e:
    print(f"  Parse error: {e}")
EOF
  else
    warn "Checkov K8s: No K8s manifests found"
  fi
  pass "Checkov: Scan complete → $OUTPUT_DIR/checkov-*.json"
else
  warn "Checkov: SKIPPED (not installed)"
fi

# ─── 3. Kubescape ────────────────────────────────────────────
header "3/5  Kubescape — K8s Security Posture"
KUBESCAPE_NSA="$OUTPUT_DIR/kubescape-nsa.json"
KUBESCAPE_MITRE="$OUTPUT_DIR/kubescape-mitre.json"

KUBESCAPE_BIN="kubescape"
[[ -x "$HOME/.kubescape/bin/kubescape" ]] && KUBESCAPE_BIN="$HOME/.kubescape/bin/kubescape"

if check_tool "$KUBESCAPE_BIN" "curl -s https://raw.githubusercontent.com/kubescape/kubescape/master/install.sh | /bin/bash"; then
  if [[ -f "$KUBECONFIG_PATH" ]]; then
    log "Running NSA framework scan..."
    "$KUBESCAPE_BIN" scan framework nsa \
      --format json \
      --output "$KUBESCAPE_NSA" \
      --kubeconfig "$KUBECONFIG_PATH" \
      2>>"$LOG" || true

    log "Running MITRE ATT&CK scan..."
    "$KUBESCAPE_BIN" scan framework mitre \
      --format json \
      --output "$KUBESCAPE_MITRE" \
      --kubeconfig "$KUBECONFIG_PATH" \
      2>>"$LOG" || true

    python3 - <<EOF
import json
for fw, path in [('NSA','$KUBESCAPE_NSA'), ('MITRE','$KUBESCAPE_MITRE')]:
    try:
        data = json.load(open(path))
        score = data.get('summaryDetails',{}).get('complianceScore','?')
        print(f"  {fw} compliance score: {score}")
    except Exception as e:
        print(f"  {fw}: parse error — {e}")
EOF
    pass "Kubescape: Scan complete → $OUTPUT_DIR/kubescape-*.json"
  else
    warn "Kubescape: kubeconfig not found at $KUBECONFIG_PATH — skipping live cluster scan"
    # Scan K8s manifest files instead
    K8S_FILES=$(find "$SOURCE_DIR" -name '*.yaml' | xargs grep -l 'kind:' 2>/dev/null | tr '\n' ' ' | head -c 500)
    if [[ -n "$K8S_FILES" ]]; then
      "$KUBESCAPE_BIN" scan framework nsa \
        --format json \
        --output "$KUBESCAPE_NSA" \
        $K8S_FILES 2>>"$LOG" || true
      pass "Kubescape: Manifest scan complete"
    fi
  fi
else
  warn "Kubescape: SKIPPED (not installed)"
fi

# ─── 4. Falco ────────────────────────────────────────────────
header "4/5  Falco — Runtime Threat Detection"
FALCO_LOG="$OUTPUT_DIR/falco-alerts.log"

FALCO_POD=$(KUBECONFIG="$KUBECONFIG_PATH" kubectl get pods -n falco \
  -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || echo "")

if [[ -n "$FALCO_POD" ]]; then
  log "Falco pod found: $FALCO_POD"
  KUBECONFIG="$KUBECONFIG_PATH" kubectl logs "$FALCO_POD" -n falco \
    --tail=100 2>/dev/null | grep -v '^$' > "$FALCO_LOG" || true

  ALERT_COUNT=$(grep -c 'Warning\|Error\|Critical' "$FALCO_LOG" 2>/dev/null || echo "0")
  SHADOW_READS=$(grep -c '/etc/shadow' "$FALCO_LOG" 2>/dev/null || echo "0")

  if [[ "$SHADOW_READS" -gt 0 ]]; then
    warn "Falco: $SHADOW_READS /etc/shadow read(s) detected — potential credential access"
  fi
  pass "Falco: Running — $ALERT_COUNT alert(s) in last 100 log lines → $FALCO_LOG"
else
  warn "Falco: Not running on cluster (no pod in 'falco' namespace)"
  warn "       Install: helm install falco falcosecurity/falco -n falco --set driver.kind=modern_ebpf"
fi

# ─── 5. Nuclei ───────────────────────────────────────────────
header "5/5  Nuclei — Web Vulnerability Scan"
NUCLEI_REPORT="$OUTPUT_DIR/nuclei-results.jsonl"

if check_tool nuclei "curl -sL https://github.com/projectdiscovery/nuclei/releases/latest/download/nuclei_*_linux_amd64.zip | unzip -d /usr/local/bin/ -"; then
  # Check if target is reachable
  if curl -s --max-time 5 "$TARGET_URL" > /dev/null 2>&1; then
    log "Scanning $TARGET_URL ..."
    nuclei \
      -u "$TARGET_URL" \
      -severity "$NUCLEI_SEVERITY" \
      -j \
      -o "$NUCLEI_REPORT" \
      -timeout 10 \
      -c 25 \
      -rl 50 \
      -exclude-tags fuzzing,brute-force \
      -tags http,network,default \
      2>>"$LOG" || true

    python3 - <<EOF
import json
from collections import Counter
findings = []
try:
    with open('$NUCLEI_REPORT') as f:
        findings = [json.loads(l) for l in f if l.strip()]
except:
    pass
counts = Counter(d.get('info',{}).get('severity','?') for d in findings)
print(f"  Findings: {dict(counts)}")
for d in findings:
    sev = d.get('info',{}).get('severity','?')
    if sev in ('critical','high'):
        print(f"  [{sev.upper()}] {d.get('info',{}).get('name','?')} → {d.get('matched-at','?')}")
EOF
    pass "Nuclei: Scan complete → $NUCLEI_REPORT"
  else
    warn "Nuclei: Target $TARGET_URL unreachable — skipping"
  fi
else
  warn "Nuclei: SKIPPED (not installed)"
fi

# ─── Summary ─────────────────────────────────────────────────
header "SCAN SUMMARY"
{
  echo "Security Scan Results — $(date)"
  echo "Source: $SOURCE_DIR"
  echo "Target: $TARGET_URL"
  echo ""
  echo "Reports generated:"
  ls -1 "$OUTPUT_DIR"/*.json "$OUTPUT_DIR"/*.jsonl "$OUTPUT_DIR"/*.log 2>/dev/null || true
} | tee "$SUMMARY"

log "\nFull results in: $OUTPUT_DIR"
log "Summary:         $SUMMARY"
log "Log:             $LOG"

if [[ "$FAIL_ON_CRITICAL" == "true" && ${#FAILED_TOOLS[@]} -gt 0 ]]; then
  fail "\nFailed tools: ${FAILED_TOOLS[*]}"
  fail "Critical issues found: $TOTAL_CRITICAL"
  exit 1
fi

pass "\nAll scanners completed."
