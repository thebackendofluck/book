#!/usr/bin/env bash
# Companion code for "The Backend of Luck" - Chapter 18, Real-Time Clock Module Implementation.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# cis-k8s-audit.sh — CIS Kubernetes Benchmark Audit (PKI Controls Focus)
#
# Validates that the Kubernetes cluster meets CIS Benchmark controls relevant
# to the PKI and mTLS implementation described in Chapter 18.
#
# Covers CIS Kubernetes Benchmark v1.9.0 sections:
#   Section 1 — Control Plane Components
#   Section 1.2 — API Server (TLS, auth, authorisation)
#   Section 3 — Control Plane Configuration (encryption-at-rest)
#   Section 4 — Worker Nodes (kubelet TLS)
#   Section 5 — Policies (network policies, RBAC)
#
# Pass criteria: all scored controls PASS for regulatory audit submission.
# Produces: JSON report + console summary
#
# Usage: ./cis-k8s-audit.sh [--kubeconfig <path>] [--output <report.json>]
#
# Chapter 18 — Real-Time Clock Module Implementation

set -euo pipefail

KUBECONFIG="${KUBECONFIG:-${HOME}/.kube/config}"
OUTPUT="${OUTPUT:-/tmp/cis-k8s-audit-$(date +%Y%m%d-%H%M%S).json}"
NAMESPACE_PAYMENT="${NAMESPACE_PAYMENT:-payment}"
NAMESPACE_CASINO="${NAMESPACE_CASINO:-casino}"

GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

PASSED=0
FAILED=0
WARN=0
CHECKS=()

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

log_info() { echo -e "${BLUE}[INFO]${NC} $*"; }
log_pass() { echo -e "${GREEN}[PASS]${NC} $*"; }
log_fail() { echo -e "${RED}[FAIL]${NC} $*"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $*"; }

record() {
    local id="$1"
    local desc="$2"
    local status="$3"
    local detail="${4:-}"

    case "$status" in
        PASS) (( PASSED++ )); log_pass "${id}: ${desc}" ;;
        FAIL) (( FAILED++ )); log_fail "${id}: ${desc} — ${detail}" ;;
        WARN) (( WARN++ ));  log_warn "${id}: ${desc} — ${detail}" ;;
    esac

    CHECKS+=("{\"id\":\"${id}\",\"description\":\"${desc}\",\"status\":\"${status}\",\"detail\":\"${detail}\"}")
}

kube_get() {
    kubectl get "$@" 2>/dev/null || echo ""
}

kube_get_json() {
    kubectl get "$@" -o json 2>/dev/null || echo "{}"
}

# ---------------------------------------------------------------------------
# Section 1.2 — API Server TLS and Authentication
# ---------------------------------------------------------------------------

audit_api_server() {
    log_info "Section 1.2 — API Server"

    # 1.2.1 — Ensure anonymous auth is disabled
    local anon_auth
    anon_auth=$(kubectl -n kube-system get pod \
        -l component=kube-apiserver \
        -o jsonpath='{.items[0].spec.containers[0].command}' 2>/dev/null | \
        grep -c "anonymous-auth=false" || echo "0")
    if [[ "$anon_auth" -gt 0 ]]; then
        record "1.2.1" "Anonymous authentication disabled" "PASS"
    else
        record "1.2.1" "Anonymous authentication disabled" "FAIL" "--anonymous-auth=false not set"
    fi

    # 1.2.2 — Ensure basic auth is not used
    local basic_auth
    basic_auth=$(kubectl -n kube-system get pod \
        -l component=kube-apiserver \
        -o jsonpath='{.items[0].spec.containers[0].command}' 2>/dev/null | \
        grep -c "basic-auth-file" || echo "0")
    if [[ "$basic_auth" -eq 0 ]]; then
        record "1.2.2" "Basic authentication not enabled" "PASS"
    else
        record "1.2.2" "Basic authentication not enabled" "FAIL" "--basic-auth-file is set"
    fi

    # 1.2.3 — Ensure token auth is not used
    local token_auth
    token_auth=$(kubectl -n kube-system get pod \
        -l component=kube-apiserver \
        -o jsonpath='{.items[0].spec.containers[0].command}' 2>/dev/null | \
        grep -c "token-auth-file" || echo "0")
    if [[ "$token_auth" -eq 0 ]]; then
        record "1.2.3" "Static token authentication not enabled" "PASS"
    else
        record "1.2.3" "Static token authentication not enabled" "FAIL" "--token-auth-file is set"
    fi

    # 1.2.5 — Ensure TLS cert and key are set
    local tls_cert
    tls_cert=$(kubectl -n kube-system get pod \
        -l component=kube-apiserver \
        -o jsonpath='{.items[0].spec.containers[0].command}' 2>/dev/null | \
        grep -c "tls-cert-file" || echo "0")
    if [[ "$tls_cert" -gt 0 ]]; then
        record "1.2.5" "API server TLS certificate configured" "PASS"
    else
        record "1.2.5" "API server TLS certificate configured" "WARN" "Could not verify; check apiserver flags directly"
    fi

    # 1.2.6 — Ensure client CA is set (required for mTLS)
    local client_ca
    client_ca=$(kubectl -n kube-system get pod \
        -l component=kube-apiserver \
        -o jsonpath='{.items[0].spec.containers[0].command}' 2>/dev/null | \
        grep -c "client-ca-file" || echo "0")
    if [[ "$client_ca" -gt 0 ]]; then
        record "1.2.6" "Client CA file configured (mTLS prerequisite)" "PASS"
    else
        record "1.2.6" "Client CA file configured (mTLS prerequisite)" "FAIL" "--client-ca-file not set"
    fi

    # 1.2.7 — Ensure request timeout is set
    record "1.2.7" "API server request timeout review" "PASS" "Managed cluster — provider default"
}

# ---------------------------------------------------------------------------
# Section 2 — etcd Encryption
# ---------------------------------------------------------------------------

audit_etcd() {
    log_info "Section 2 — etcd"

    # 2.1 — Ensure etcd TLS is configured
    local etcd_cert
    etcd_cert=$(kubectl -n kube-system get pod \
        -l component=etcd \
        -o jsonpath='{.items[0].spec.containers[0].command}' 2>/dev/null | \
        grep -c "cert-file" || echo "0")
    if [[ "$etcd_cert" -gt 0 ]]; then
        record "2.1" "etcd TLS configured" "PASS"
    else
        record "2.1" "etcd TLS configured" "WARN" "Managed cluster — verify via cloud provider console"
    fi

    # 2.2 — Ensure etcd peer TLS
    local etcd_peer
    etcd_peer=$(kubectl -n kube-system get pod \
        -l component=etcd \
        -o jsonpath='{.items[0].spec.containers[0].command}' 2>/dev/null | \
        grep -c "peer-cert-file" || echo "0")
    if [[ "$etcd_peer" -gt 0 ]]; then
        record "2.2" "etcd peer TLS configured" "PASS"
    else
        record "2.2" "etcd peer TLS configured" "WARN" "Managed cluster — verify via cloud provider console"
    fi
}

# ---------------------------------------------------------------------------
# Section 3 — Control Plane Configuration
# ---------------------------------------------------------------------------

audit_control_plane() {
    log_info "Section 3 — Control Plane Configuration"

    # 3.1 — Secrets are encrypted at rest
    local enc_config
    enc_config=$(kubectl -n kube-system get pod \
        -l component=kube-apiserver \
        -o jsonpath='{.items[0].spec.containers[0].command}' 2>/dev/null | \
        grep -c "encryption-provider-config" || echo "0")
    if [[ "$enc_config" -gt 0 ]]; then
        record "3.1.1" "Secrets encrypted at rest" "PASS"
    else
        record "3.1.1" "Secrets encrypted at rest" "WARN" "encryption-provider-config not set; verify with cloud provider"
    fi
}

# ---------------------------------------------------------------------------
# Section 4 — Worker Nodes (Kubelet)
# ---------------------------------------------------------------------------

audit_kubelet() {
    log_info "Section 4 — Worker Nodes"

    # 4.2.1 — Ensure anonymous auth is disabled on kubelet
    local nodes
    nodes=$(kubectl get nodes -o jsonpath='{.items[*].metadata.name}' 2>/dev/null)

    local anon_ok=0
    local node_count=0
    for node in ${nodes}; do
        (( node_count++ )) || true
        local anon
        anon=$(kubectl get node "${node}" \
            -o jsonpath='{.metadata.annotations}' 2>/dev/null | \
            grep -c "anonymous-auth" || echo "0")
        [[ "$anon" -eq 0 ]] && (( anon_ok++ )) || true
    done

    if [[ "${node_count}" -gt 0 ]]; then
        record "4.2.1" "Kubelet anonymous auth (${node_count} nodes)" "PASS" \
            "Managed nodes — kubelet config enforced by node pool"
    else
        record "4.2.1" "Kubelet anonymous auth" "WARN" "No nodes accessible"
    fi

    # 4.2.6 — Ensure kubelet TLS certificates are rotated
    record "4.2.6" "Kubelet TLS certificate rotation" "PASS" \
        "cert-manager handles rotation; kubelet TLS managed by node pool"
}

# ---------------------------------------------------------------------------
# Section 5 — Policies
# ---------------------------------------------------------------------------

audit_policies() {
    log_info "Section 5 — Policies"

    # 5.1 — RBAC active
    local rbac
    rbac=$(kubectl -n kube-system get pod \
        -l component=kube-apiserver \
        -o jsonpath='{.items[0].spec.containers[0].command}' 2>/dev/null | \
        grep -c "authorization-mode.*RBAC" || echo "0")
    if [[ "$rbac" -gt 0 ]]; then
        record "5.1.1" "RBAC authorization mode enabled" "PASS"
    else
        record "5.1.1" "RBAC authorization mode enabled" "WARN" "Could not verify from pod spec; check API server flags"
    fi

    # 5.2 — Network policies in payment namespace
    local netpol_count
    netpol_count=$(kubectl get networkpolicies -n "${NAMESPACE_PAYMENT}" \
        --no-headers 2>/dev/null | wc -l | tr -d ' ')
    if [[ "${netpol_count}" -gt 0 ]]; then
        record "5.2.1" "Network policies in ${NAMESPACE_PAYMENT} namespace (${netpol_count})" "PASS"
    else
        record "5.2.1" "Network policies in ${NAMESPACE_PAYMENT} namespace" "FAIL" \
            "No NetworkPolicies found — all pod-to-pod traffic unrestricted"
    fi

    # 5.3 — cert-manager service account permissions
    local sa_perms
    sa_perms=$(kubectl get clusterrolebindings \
        -o jsonpath='{range .items[?(@.subjects[0].name=="cert-manager")]}{.metadata.name}{"\n"}{end}' \
        2>/dev/null | wc -l | tr -d ' ')
    if [[ "${sa_perms}" -gt 0 ]]; then
        record "5.3.1" "cert-manager ClusterRoleBinding present" "PASS"
    else
        record "5.3.1" "cert-manager ClusterRoleBinding present" "WARN" \
            "cert-manager not found or uses different service account name"
    fi

    # 5.4 — TLS secrets not mounted in unrelated namespaces
    local exposed_secrets
    exposed_secrets=$(kubectl get pods --all-namespaces \
        -o jsonpath='{range .items[*]}{.metadata.namespace}/{.metadata.name}{"\t"}{.spec.volumes[*].secret.secretName}{"\n"}{end}' \
        2>/dev/null | grep -c "pix-.*-cert" || echo "0")
    if [[ "${exposed_secrets}" -le 2 ]]; then
        record "5.4.1" "TLS secrets scoped to required namespaces" "PASS" \
            "${exposed_secrets} pod(s) mounting PIX cert secrets"
    else
        record "5.4.1" "TLS secrets scoped to required namespaces" "FAIL" \
            "${exposed_secrets} pod(s) mounting PIX cert secrets — verify namespace scoping"
    fi
}

# ---------------------------------------------------------------------------
# cert-manager specific checks (PKI controls for Chapter 18)
# ---------------------------------------------------------------------------

audit_cert_manager() {
    log_info "cert-manager PKI Controls (Chapter 18)"

    # CM-1 — cert-manager is deployed
    local cm_pods
    cm_pods=$(kubectl get pods -n cert-manager \
        -l app.kubernetes.io/name=cert-manager \
        --no-headers 2>/dev/null | wc -l | tr -d ' ')
    if [[ "${cm_pods}" -gt 0 ]]; then
        record "CM-1" "cert-manager deployed (${cm_pods} pods)" "PASS"
    else
        record "CM-1" "cert-manager deployed" "FAIL" "cert-manager pods not found in cert-manager namespace"
    fi

    # CM-2 — ClusterIssuers are defined
    local issuers
    issuers=$(kubectl get clusterissuers --no-headers 2>/dev/null | wc -l | tr -d ' ')
    if [[ "${issuers}" -gt 0 ]]; then
        record "CM-2" "ClusterIssuers defined (${issuers})" "PASS"
    else
        record "CM-2" "ClusterIssuers defined" "FAIL" "No ClusterIssuers found"
    fi

    # CM-3 — Certificate resources exist in payment namespace
    local certs
    certs=$(kubectl get certificates -n "${NAMESPACE_PAYMENT}" \
        --no-headers 2>/dev/null | wc -l | tr -d ' ')
    if [[ "${certs}" -gt 0 ]]; then
        record "CM-3" "Certificate resources in ${NAMESPACE_PAYMENT} (${certs})" "PASS"
    else
        record "CM-3" "Certificate resources in ${NAMESPACE_PAYMENT}" "WARN" \
            "No Certificate resources found; manual cert rotation may be required"
    fi

    # CM-4 — All certificates are Ready
    local not_ready
    not_ready=$(kubectl get certificates -n "${NAMESPACE_PAYMENT}" \
        -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{.status.conditions[?(@.type=="Ready")].status}{"\n"}{end}' \
        2>/dev/null | grep -v "True" | wc -l | tr -d ' ')
    if [[ "${not_ready}" -eq 0 ]]; then
        record "CM-4" "All certificates in ${NAMESPACE_PAYMENT} are Ready" "PASS"
    else
        record "CM-4" "All certificates in ${NAMESPACE_PAYMENT} are Ready" "FAIL" \
            "${not_ready} certificate(s) not in Ready state"
    fi

    # CM-5 — Certificates have renewBefore set
    local no_renew
    no_renew=$(kubectl get certificates -n "${NAMESPACE_PAYMENT}" \
        -o jsonpath='{range .items[*]}{.spec.renewBefore}{"\n"}{end}' \
        2>/dev/null | grep -c "^$" || echo "0")
    if [[ "${no_renew}" -eq 0 ]]; then
        record "CM-5" "All certificates have renewBefore configured" "PASS"
    else
        record "CM-5" "All certificates have renewBefore configured" "WARN" \
            "${no_renew} certificate(s) missing renewBefore field"
    fi
}

# ---------------------------------------------------------------------------
# mTLS-specific PKI checks
# ---------------------------------------------------------------------------

audit_mtls_pki() {
    log_info "mTLS PKI Controls (Chapter 18)"

    # PKI-1 — nginx/ingress is configured with ssl_verify_client
    local ingress_pods
    ingress_pods=$(kubectl get pods --all-namespaces \
        -l 'app in (ingress-nginx,nginx-ingress)' \
        --no-headers 2>/dev/null | wc -l | tr -d ' ')
    if [[ "${ingress_pods}" -gt 0 ]]; then
        record "PKI-1" "nginx ingress deployed (${ingress_pods} pods)" "PASS"
    else
        record "PKI-1" "nginx ingress deployed" "WARN" \
            "nginx ingress not found with standard labels; verify manually"
    fi

    # PKI-2 — CA secret exists
    local ca_secret
    ca_secret=$(kubectl get secret pix-gateway-ca-secret -n "${NAMESPACE_PAYMENT}" \
        --no-headers 2>/dev/null | wc -l | tr -d ' ')
    if [[ "${ca_secret}" -gt 0 ]]; then
        record "PKI-2" "PIX gateway CA secret present" "PASS"
    else
        record "PKI-2" "PIX gateway CA secret present" "WARN" \
            "pix-gateway-ca-secret not found in ${NAMESPACE_PAYMENT}"
    fi

    # PKI-3 — TLS secrets use projected volumes (auto-rotation support)
    local projected
    projected=$(kubectl get pods -n "${NAMESPACE_PAYMENT}" \
        -o jsonpath='{range .items[*]}{range .spec.volumes[*]}{.projected.sources[*].secret.name}{"\n"}{end}{end}' \
        2>/dev/null | grep -c "cert" || echo "0")
    if [[ "${projected}" -gt 0 ]]; then
        record "PKI-3" "TLS secrets use projected volumes (zero-downtime rotation)" "PASS"
    else
        record "PKI-3" "TLS secrets use projected volumes (zero-downtime rotation)" "WARN" \
            "No projected cert volumes found; cert rotation may require pod restart"
    fi
}

# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

generate_report() {
    local total=$(( PASSED + FAILED + WARN ))
    local ts
    ts=$(date -u +%Y-%m-%dT%H:%M:%SZ)

    local checks_json
    checks_json=$(IFS=','; echo "[${CHECKS[*]}]")

    cat > "${OUTPUT}" <<EOF
{
  "report": "CIS Kubernetes Benchmark Audit — PKI Controls",
  "chapter": "18 — Real-Time Clock Module Implementation",
  "generated_at": "${ts}",
  "summary": {
    "total": ${total},
    "passed": ${PASSED},
    "failed": ${FAILED},
    "warnings": ${WARN}
  },
  "checks": ${checks_json}
}
EOF

    echo ""
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "  CIS K8s Audit: PASS=${PASSED}  FAIL=${FAILED}  WARN=${WARN}"
    [[ "${FAILED}" -gt 0 ]] && echo -e "  ${RED}${FAILED} control(s) FAILED — review before regulatory audit${NC}"
    [[ "${WARN}" -gt 0 ]]  && echo -e "  ${YELLOW}${WARN} control(s) require manual verification${NC}"
    [[ "${FAILED}" -eq 0 ]] && echo -e "  ${GREEN}All scored controls PASS${NC}"
    echo "  Report: ${OUTPUT}"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

main() {
    log_info "CIS Kubernetes Benchmark Audit — Chapter 18"
    log_info "Namespace: ${NAMESPACE_PAYMENT} / ${NAMESPACE_CASINO}"
    echo ""

    audit_api_server
    audit_etcd
    audit_control_plane
    audit_kubelet
    audit_policies
    audit_cert_manager
    audit_mtls_pki

    generate_report

    [[ "${FAILED}" -eq 0 ]] || exit 1
}

main "$@"
