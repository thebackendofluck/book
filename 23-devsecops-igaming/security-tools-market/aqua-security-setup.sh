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

# =============================================================================
# Aqua Security Platform Setup for iGaming Container Security
# =============================================================================
#
# Purpose:
#   Deploy and configure Aqua Security CSP (Cloud Security Platform) for
#   iGaming container environments. Covers runtime protection, image scanning,
#   compliance templates, and drift prevention for casino workloads.
#
# Why Aqua Security for iGaming:
#   Casino platforms run dozens of containerised microservices: wallet, PAM,
#   game aggregation, compliance engines, payment gateways. Each container
#   is a potential attack vector. Aqua Security provides:
#     - Runtime protection: blocks unauthorised processes inside containers
#     - Image scanning: catches vulnerabilities before deployment
#     - Compliance templates: PCI-DSS, SOC2, ISO 27001 out of the box
#     - Drift prevention: ensures production containers match their images
#
# Usage:
#   ./aqua-security-setup.sh [--namespace aqua] [--registry-url REGISTRY]
#
# Prerequisites:
#   - Kubernetes cluster (1.26+) with kubectl configured
#   - Helm 3.x installed
#   - Aqua Security license (trial or enterprise)
#   - Container registry credentials
#
# =============================================================================

set -euo pipefail

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
AQUA_NAMESPACE="${AQUA_NAMESPACE:-aqua}"
AQUA_VERSION="${AQUA_VERSION:-2024.4}"
AQUA_REGISTRY="${AQUA_REGISTRY:-registry.aquasec.com}"
AQUA_DB_PASSWORD="${AQUA_DB_PASSWORD:-$(openssl rand -base64 24)}"
AQUA_ADMIN_PASSWORD="${AQUA_ADMIN_PASSWORD:-$(openssl rand -base64 16)}"
AQUA_LICENSE_TOKEN="${AQUA_LICENSE_TOKEN:-}"
CLUSTER_NAME="${CLUSTER_NAME:-igaming-production}"

# Compliance frameworks relevant to iGaming operators
COMPLIANCE_FRAMEWORKS="pci-dss-v4,soc2-type2,iso27001-2022"

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log_info()  { printf "${GREEN}[INFO]${NC}  %s\n" "$1"; }
log_warn()  { printf "${YELLOW}[WARN]${NC}  %s\n" "$1"; }
log_error() { printf "${RED}[ERROR]${NC} %s\n" "$1" >&2; }
log_step()  { printf "${BLUE}[STEP]${NC}  %s\n" "$1"; }

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------
while [[ $# -gt 0 ]]; do
    case "$1" in
        --namespace)
            AQUA_NAMESPACE="$2"
            shift 2
            ;;
        --registry-url)
            AQUA_REGISTRY="$2"
            shift 2
            ;;
        --license)
            AQUA_LICENSE_TOKEN="$2"
            shift 2
            ;;
        --cluster-name)
            CLUSTER_NAME="$2"
            shift 2
            ;;
        -h|--help)
            printf "Usage: %s [--namespace NS] [--registry-url URL] [--license TOKEN]\n" "$0"
            exit 0
            ;;
        *)
            log_error "Unknown option: $1"
            exit 1
            ;;
    esac
done

# ---------------------------------------------------------------------------
# Pre-flight checks
# ---------------------------------------------------------------------------
preflight_checks() {
    log_step "Running pre-flight checks..."

    local missing=()

    if ! command -v kubectl &>/dev/null; then
        missing+=("kubectl")
    fi

    if ! command -v helm &>/dev/null; then
        missing+=("helm")
    fi

    if [[ ${#missing[@]} -gt 0 ]]; then
        log_error "Missing required tools: ${missing[*]}"
        exit 1
    fi

    # Verify cluster connectivity
    if ! kubectl cluster-info &>/dev/null; then
        log_error "Cannot connect to Kubernetes cluster. Check your kubeconfig."
        exit 1
    fi

    # Check minimum node resources (Aqua needs ~4 GB RAM for server)
    local node_count
    node_count=$(kubectl get nodes --no-headers 2>/dev/null | wc -l)
    if [[ "$node_count" -lt 1 ]]; then
        log_error "No nodes available in the cluster."
        exit 1
    fi

    log_info "Pre-flight checks passed. Cluster: ${CLUSTER_NAME}, Nodes: ${node_count}"
}

# ---------------------------------------------------------------------------
# Create namespace and secrets
# ---------------------------------------------------------------------------
setup_namespace() {
    log_step "Setting up namespace and secrets..."

    kubectl create namespace "${AQUA_NAMESPACE}" --dry-run=client -o yaml | kubectl apply -f -

    # Label namespace for network policy isolation
    kubectl label namespace "${AQUA_NAMESPACE}" \
        app.kubernetes.io/part-of=aqua-security \
        compliance.igaming/scope=security-tooling \
        --overwrite

    # Registry credentials for pulling Aqua images
    kubectl create secret docker-registry aqua-registry \
        --namespace "${AQUA_NAMESPACE}" \
        --docker-server="${AQUA_REGISTRY}" \
        --docker-username="${AQUA_REGISTRY_USER:-}" \
        --docker-password="${AQUA_REGISTRY_PASS:-}" \
        --dry-run=client -o yaml | kubectl apply -f -

    # Database password
    kubectl create secret generic aqua-db-password \
        --namespace "${AQUA_NAMESPACE}" \
        --from-literal=password="${AQUA_DB_PASSWORD}" \
        --dry-run=client -o yaml | kubectl apply -f -

    # Admin credentials
    kubectl create secret generic aqua-admin \
        --namespace "${AQUA_NAMESPACE}" \
        --from-literal=password="${AQUA_ADMIN_PASSWORD}" \
        --dry-run=client -o yaml | kubectl apply -f -

    log_info "Namespace ${AQUA_NAMESPACE} configured with secrets."
}

# ---------------------------------------------------------------------------
# Deploy Aqua Server (Console + Gateway + Database)
# ---------------------------------------------------------------------------
deploy_aqua_server() {
    log_step "Deploying Aqua Security server components..."

    # Add Aqua Helm repository
    helm repo add aqua-helm https://helm.aquasec.com
    helm repo update

    # Deploy Aqua server with iGaming-optimised settings
    helm upgrade --install aqua-server aqua-helm/server \
        --namespace "${AQUA_NAMESPACE}" \
        --set global.imageCredentials.create=false \
        --set global.imageCredentials.name=aqua-registry \
        --set global.platform=k8s \
        --set global.db.external.enabled=false \
        --set global.db.passwordSecret=aqua-db-password \
        --set server.adminPassword="${AQUA_ADMIN_PASSWORD}" \
        --set server.license.token="${AQUA_LICENSE_TOKEN}" \
        --set server.replicaCount=2 \
        --set gateway.replicaCount=2 \
        --set server.resources.requests.memory=2Gi \
        --set server.resources.requests.cpu=1000m \
        --set server.resources.limits.memory=4Gi \
        --set server.resources.limits.cpu=2000m \
        --wait \
        --timeout 600s

    log_info "Aqua server deployed with HA configuration."
}

# ---------------------------------------------------------------------------
# Deploy Aqua Enforcer (runtime protection agent)
# ---------------------------------------------------------------------------
deploy_enforcer() {
    log_step "Deploying Aqua Enforcer for runtime protection..."

    # The Enforcer runs as a DaemonSet on every node.
    # For iGaming, runtime protection is critical -- it prevents:
    # - Cryptojacking in game server containers
    # - Reverse shells in payment processing pods
    # - Unauthorised binary execution in wallet services
    helm upgrade --install aqua-enforcer aqua-helm/enforcer \
        --namespace "${AQUA_NAMESPACE}" \
        --set global.imageCredentials.create=false \
        --set global.imageCredentials.name=aqua-registry \
        --set global.platform=k8s \
        --set enforcer.gateway.host=aqua-gateway-svc \
        --set enforcer.gateway.port=8443 \
        --set enforcerLogicalName="${CLUSTER_NAME}-enforcer" \
        --wait \
        --timeout 300s

    log_info "Enforcer deployed as DaemonSet across all nodes."
}

# ---------------------------------------------------------------------------
# Configure runtime protection policies for gaming containers
# ---------------------------------------------------------------------------
configure_runtime_policies() {
    log_step "Configuring runtime protection policies for iGaming workloads..."

    # Generate runtime policy manifests for key gaming services
    # These policies restrict what processes can run inside containers

    local policies_dir="/tmp/aqua-policies"
    mkdir -p "${policies_dir}"

    # Policy: Wallet Service -- strictest possible (handles real money)
    cat > "${policies_dir}/wallet-runtime-policy.json" <<'JSON'
{
    "name": "iGaming-Wallet-Service",
    "description": "Runtime protection for wallet/payment containers. Zero tolerance for drift.",
    "enabled": true,
    "enforce": true,
    "type": "container.runtime",
    "scope": {
        "expression": "v1 && v2",
        "variables": [
            {"attribute": "kubernetes.namespace", "value": "casino-wallet"},
            {"attribute": "image.name", "value": "*/wallet-service:*"}
        ]
    },
    "runtime_options": {
        "block_non_compliant_workloads": true,
        "blocked_executables": ["wget", "curl", "nc", "ncat", "nmap", "tcpdump"],
        "block_cryptocurrency_mining": true,
        "block_reverse_shell": true,
        "block_fileless_exec": true,
        "drift_prevention": {
            "enabled": true,
            "exec_lockdown": true
        },
        "readonly_files_and_directories": ["/app/config", "/etc/ssl"],
        "blocked_outbound_ports": [6667, 6697, 4444, 5555]
    },
    "compliance_frameworks": ["pci-dss-v4", "soc2-type2"]
}
JSON

    # Policy: Game Aggregation Layer -- moderate (handles game sessions)
    cat > "${policies_dir}/gal-runtime-policy.json" <<'JSON'
{
    "name": "iGaming-GAL-Service",
    "description": "Runtime protection for game aggregation layer containers.",
    "enabled": true,
    "enforce": true,
    "type": "container.runtime",
    "scope": {
        "expression": "v1",
        "variables": [
            {"attribute": "kubernetes.namespace", "value": "casino-games"}
        ]
    },
    "runtime_options": {
        "block_non_compliant_workloads": true,
        "blocked_executables": ["wget", "nc", "ncat", "nmap"],
        "block_cryptocurrency_mining": true,
        "block_reverse_shell": true,
        "drift_prevention": {
            "enabled": true,
            "exec_lockdown": false
        },
        "blocked_outbound_ports": [6667, 6697]
    },
    "compliance_frameworks": ["gli-33", "iso27001-2022"]
}
JSON

    # Policy: PAM (Player Account Management) -- strict (handles PII)
    cat > "${policies_dir}/pam-runtime-policy.json" <<'JSON'
{
    "name": "iGaming-PAM-Service",
    "description": "Runtime protection for player account management. Handles PII and KYC data.",
    "enabled": true,
    "enforce": true,
    "type": "container.runtime",
    "scope": {
        "expression": "v1",
        "variables": [
            {"attribute": "kubernetes.namespace", "value": "casino-pam"}
        ]
    },
    "runtime_options": {
        "block_non_compliant_workloads": true,
        "blocked_executables": ["wget", "curl", "nc", "ncat", "nmap", "tcpdump", "strace"],
        "block_cryptocurrency_mining": true,
        "block_reverse_shell": true,
        "block_fileless_exec": true,
        "drift_prevention": {
            "enabled": true,
            "exec_lockdown": true
        },
        "readonly_files_and_directories": ["/app/config", "/etc/ssl", "/app/templates"],
        "blocked_outbound_ports": [6667, 6697, 4444, 5555]
    },
    "compliance_frameworks": ["pci-dss-v4", "gdpr", "soc2-type2"]
}
JSON

    log_info "Runtime policies generated in ${policies_dir}/"
    log_info "Apply via Aqua Console API or import through the UI."
}

# ---------------------------------------------------------------------------
# Configure network firewall policies
# ---------------------------------------------------------------------------
configure_network_policies() {
    log_step "Configuring network firewall policies..."

    # Kubernetes NetworkPolicy to isolate the Aqua namespace
    kubectl apply -f - <<'NETPOL'
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: aqua-namespace-isolation
  namespace: aqua
spec:
  podSelector: {}
  policyTypes:
    - Ingress
    - Egress
  ingress:
    # Allow traffic from gaming namespaces (enforcers report back)
    - from:
        - namespaceSelector:
            matchLabels:
              compliance.igaming/scope: gaming-workload
      ports:
        - port: 8443
          protocol: TCP
    # Allow traffic within the aqua namespace
    - from:
        - podSelector: {}
  egress:
    # Allow DNS resolution
    - to:
        - namespaceSelector: {}
      ports:
        - port: 53
          protocol: UDP
        - port: 53
          protocol: TCP
    # Allow internal communication
    - to:
        - podSelector: {}
    # Allow outbound to Aqua cloud for threat intelligence feeds
    - to:
        - ipBlock:
            cidr: 0.0.0.0/0
      ports:
        - port: 443
          protocol: TCP
NETPOL

    log_info "Network policies applied for Aqua namespace isolation."
}

# ---------------------------------------------------------------------------
# Configure Kubernetes admission control
# ---------------------------------------------------------------------------
configure_admission_control() {
    log_step "Configuring Kubernetes admission control (KubeEnforcer)..."

    # KubeEnforcer acts as a validating webhook -- it blocks deployment of
    # non-compliant images. For iGaming, this means:
    # - No images with critical CVEs reach production
    # - Only images from approved registries can deploy
    # - Images must be signed (for payment-critical services)
    helm upgrade --install aqua-kube-enforcer aqua-helm/kube-enforcer \
        --namespace "${AQUA_NAMESPACE}" \
        --set global.imageCredentials.create=false \
        --set global.imageCredentials.name=aqua-registry \
        --set global.platform=k8s \
        --set kubeEnforcer.gateway.host=aqua-gateway-svc \
        --set kubeEnforcer.gateway.port=8443 \
        --wait \
        --timeout 300s

    log_info "KubeEnforcer deployed. Non-compliant images will be blocked."
}

# ---------------------------------------------------------------------------
# Main execution
# ---------------------------------------------------------------------------
main() {
    log_info "=========================================="
    log_info "Aqua Security Setup for iGaming"
    log_info "=========================================="
    log_info "Namespace:    ${AQUA_NAMESPACE}"
    log_info "Cluster:      ${CLUSTER_NAME}"
    log_info "Version:      ${AQUA_VERSION}"
    log_info "Compliance:   ${COMPLIANCE_FRAMEWORKS}"
    log_info ""

    preflight_checks
    setup_namespace
    deploy_aqua_server
    deploy_enforcer
    configure_runtime_policies
    configure_network_policies
    configure_admission_control

    log_info "=========================================="
    log_info "Aqua Security deployment complete!"
    log_info "=========================================="
    log_info ""
    log_info "Access the console:"
    log_info "  kubectl port-forward svc/aqua-web -n ${AQUA_NAMESPACE} 8080:8080"
    log_info "  URL:      http://localhost:8080"
    log_info "  User:     administrator"
    log_info "  Password: ${AQUA_ADMIN_PASSWORD}"
    log_info ""
    log_info "Next steps:"
    log_info "  1. Import compliance templates (PCI-DSS, SOC2, ISO 27001)"
    log_info "  2. Configure image scanning for your container registries"
    log_info "  3. Apply runtime policies from /tmp/aqua-policies/"
    log_info "  4. Enable secrets management integration (Vault/AWS SM)"
    log_info "  5. Set up alerting (Slack/PagerDuty) for runtime violations"
    log_info "=========================================="
}

main "$@"
