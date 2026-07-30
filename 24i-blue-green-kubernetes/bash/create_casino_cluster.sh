#!/usr/bin/env bash
# Companion code for "The Backend of Luck" - Chapter 24i, Blue-Green Cluster Switching for iGaming Kubernetes Environm.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# create_casino_cluster.sh — Provision a new casino K3s cluster
# Usage: ./create_casino_cluster.sh <color> <config_file>
# Example: ./create_casino_cluster.sh green /etc/casino/cluster.conf
#
# Required environment (or sourced from config_file):
#   CLUSTER_API_HOST      — IP of the K3s API server node
#   CLUSTER_WORKER_HOSTS  — Space-separated worker node IPs
#   CLUSTER_TOKEN         — K3s join token (from Vault or secrets manager)
#   POSTGRES_HOST         — Shared PostgreSQL host
#   POSTGRES_PASSWORD     — Pulled from Vault at runtime
#   REDIS_HOST            — Shared Redis host
#   REDIS_PASSWORD        — Pulled from Vault at runtime
#   VAULT_ADDR            — HashiCorp Vault address
#   VAULT_TOKEN           — Vault token with provisioning policy
#   REGISTRY_HOST         — Container image registry
#   IMAGE_TAG             — Git commit SHA or release tag to deploy
#   KUBECONFIG_OUTPUT     — Path to write generated kubeconfig

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_FILE="/var/log/casino/cluster-provision-$(date +%Y%m%d-%H%M%S).log"
mkdir -p "$(dirname "$LOG_FILE")"

# ── logging ─────────────────────────────────────────────────────────────────

log() {
    local level="$1"; shift
    local msg="$*"
    local ts; ts="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
    echo "${ts} [${level}] ${msg}" | tee -a "$LOG_FILE"
}

die() {
    log ERROR "$*"
    exit 1
}

# ── argument parsing ─────────────────────────────────────────────────────────

CLUSTER_COLOR="${1:?Usage: $0 <blue|green> <config_file>}"
CONFIG_FILE="${2:?Usage: $0 <blue|green> <config_file>}"

[[ "$CLUSTER_COLOR" == "blue" || "$CLUSTER_COLOR" == "green" ]] \
    || die "Cluster color must be 'blue' or 'green', got: $CLUSTER_COLOR"

[[ -f "$CONFIG_FILE" ]] || die "Config file not found: $CONFIG_FILE"

# shellcheck source=/dev/null
source "$CONFIG_FILE"

log INFO "=== Provisioning $CLUSTER_COLOR cluster ==="
log INFO "API host: ${CLUSTER_API_HOST}"
log INFO "Workers: ${CLUSTER_WORKER_HOSTS}"
log INFO "Image tag: ${IMAGE_TAG}"

# ── prerequisites check ──────────────────────────────────────────────────────

check_prerequisites() {
    local required_tools=(ssh kubectl helm vault jq curl)
    for tool in "${required_tools[@]}"; do
        command -v "$tool" &>/dev/null || die "Required tool not found: $tool"
    done

    # Verify SSH connectivity to all hosts
    local all_hosts=("$CLUSTER_API_HOST" $CLUSTER_WORKER_HOSTS)
    for host in "${all_hosts[@]}"; do
        ssh -o ConnectTimeout=10 -o StrictHostKeyChecking=no \
            "root@${host}" "echo ok" &>/dev/null \
            || die "Cannot SSH to host: $host"
    done

    # Verify Vault is reachable and token is valid
    VAULT_TOKEN_STATUS=$(vault token lookup -format=json 2>/dev/null | jq -r '.data.display_name') \
        || die "Vault token invalid or Vault unreachable"
    log INFO "Vault token valid: $VAULT_TOKEN_STATUS"
}

# ── fetch secrets from vault ─────────────────────────────────────────────────

fetch_secrets() {
    log INFO "Fetching secrets from Vault..."

    POSTGRES_PASSWORD=$(vault kv get -field=password \
        "secret/casino/${CLUSTER_COLOR}/postgres") \
        || die "Failed to fetch PostgreSQL password from Vault"

    REDIS_PASSWORD=$(vault kv get -field=password \
        "secret/casino/${CLUSTER_COLOR}/redis") \
        || die "Failed to fetch Redis password from Vault"

    CLUSTER_TOKEN=$(vault kv get -field=token \
        "secret/casino/${CLUSTER_COLOR}/k3s-join-token") \
        || die "Failed to fetch K3s join token from Vault"

    JWT_SIGNING_KEY=$(vault kv get -field=private_key \
        "secret/casino/${CLUSTER_COLOR}/jwt-signing") \
        || die "Failed to fetch JWT signing key from Vault"

    log INFO "All secrets fetched successfully"
}

# ── install k3s control plane ────────────────────────────────────────────────

install_k3s_server() {
    local host="$CLUSTER_API_HOST"
    log INFO "Installing K3s server on $host..."

    # Generate K3s install config
    cat > /tmp/k3s-config-${CLUSTER_COLOR}.yaml <<EOF
cluster-cidr: "10.${CLUSTER_CIDR_OCTET}.0.0/16"
service-cidr: "10.$((CLUSTER_CIDR_OCTET + 1)).0.0/16"
cluster-dns: "10.$((CLUSTER_CIDR_OCTET + 1)).0.10"
tls-san:
  - "${CLUSTER_API_HOST}"
  - "api.${CLUSTER_COLOR}.casino.internal"
disable:
  - traefik
flannel-backend: "wireguard-native"
kube-apiserver-arg:
  - "audit-log-path=/var/log/kubernetes/audit.log"
  - "audit-log-maxage=30"
  - "audit-log-maxbackup=10"
  - "audit-log-maxsize=100"
  - "enable-admission-plugins=PodSecurity,NodeRestriction,ServiceAccount"
  - "encryption-provider-config=/etc/kubernetes/encryption-config.yaml"
kubelet-arg:
  - "protect-kernel-defaults=true"
  - "event-qps=0"
  - "streaming-connection-idle-timeout=5m"
EOF

    # Copy config and run installer
    scp /tmp/k3s-config-${CLUSTER_COLOR}.yaml "root@${host}:/etc/rancher/k3s/config.yaml"
    ssh "root@${host}" "
        mkdir -p /var/log/kubernetes /etc/kubernetes
        cat > /etc/kubernetes/encryption-config.yaml <<'ENCEOF'
apiVersion: apiserver.config.k8s.io/v1
kind: EncryptionConfiguration
resources:
  - resources:
      - secrets
    providers:
      - aescbc:
          keys:
            - name: key1
              secret: $(openssl rand -base64 32)
      - identity: {}
ENCEOF
        curl -sfL https://get.k3s.io | \
            K3S_TOKEN='${CLUSTER_TOKEN}' \
            INSTALL_K3S_VERSION='v1.29.4+k3s1' \
            sh -s - server \
            --config /etc/rancher/k3s/config.yaml
    "

    # Wait for API server to be ready
    local retries=30
    local i=0
    while [[ $i -lt $retries ]]; do
        if ssh "root@${host}" "kubectl get nodes 2>/dev/null | grep -q 'Ready'"; then
            log INFO "K3s API server ready on $host"
            break
        fi
        ((i++))
        log INFO "Waiting for K3s API server... ($i/$retries)"
        sleep 10
    done
    [[ $i -lt $retries ]] || die "K3s API server did not become ready on $host"

    # Extract kubeconfig
    ssh "root@${host}" "cat /etc/rancher/k3s/k3s.yaml" \
        | sed "s/127.0.0.1/${CLUSTER_API_HOST}/g" \
        > "${KUBECONFIG_OUTPUT}"
    chmod 600 "${KUBECONFIG_OUTPUT}"
    log INFO "Kubeconfig written to ${KUBECONFIG_OUTPUT}"
}

# ── join worker nodes ────────────────────────────────────────────────────────

join_workers() {
    log INFO "Joining worker nodes..."
    local workers=($CLUSTER_WORKER_HOSTS)

    for worker in "${workers[@]}"; do
        log INFO "Joining worker: $worker"
        ssh "root@${worker}" "
            curl -sfL https://get.k3s.io | \
                K3S_URL='https://${CLUSTER_API_HOST}:6443' \
                K3S_TOKEN='${CLUSTER_TOKEN}' \
                INSTALL_K3S_VERSION='v1.29.4+k3s1' \
                sh -s - agent \
                --kubelet-arg=protect-kernel-defaults=true \
                --kubelet-arg=event-qps=0
        " &
    done
    wait

    # Wait for all workers to be Ready
    export KUBECONFIG="${KUBECONFIG_OUTPUT}"
    local expected_nodes=$(( 1 + ${#workers[@]} ))
    local retries=30
    local i=0
    while [[ $i -lt $retries ]]; do
        local ready_nodes
        ready_nodes=$(kubectl get nodes --no-headers 2>/dev/null | grep -c ' Ready' || echo 0)
        if [[ "$ready_nodes" -ge "$expected_nodes" ]]; then
            log INFO "All $expected_nodes nodes ready"
            break
        fi
        ((i++))
        log INFO "Waiting for nodes: $ready_nodes/$expected_nodes ready ($i/$retries)"
        sleep 10
    done
    [[ $i -lt $retries ]] || die "Not all worker nodes became Ready"
}

# ── install cluster addons ────────────────────────────────────────────────────

install_addons() {
    export KUBECONFIG="${KUBECONFIG_OUTPUT}"
    log INFO "Installing cluster addons..."

    # cert-manager
    helm repo add jetstack https://charts.jetstack.io --force-update
    helm upgrade --install cert-manager jetstack/cert-manager \
        --namespace cert-manager \
        --create-namespace \
        --version v1.14.5 \
        --set installCRDs=true \
        --set global.leaderElection.namespace=cert-manager \
        --wait --timeout=5m

    # ingress-nginx
    helm repo add ingress-nginx https://kubernetes.github.io/ingress-nginx --force-update
    helm upgrade --install ingress-nginx ingress-nginx/ingress-nginx \
        --namespace ingress-nginx \
        --create-namespace \
        --set controller.replicaCount=2 \
        --set controller.minAvailable=1 \
        --set controller.service.type=LoadBalancer \
        --set controller.config.use-forwarded-headers="true" \
        --set controller.config.ssl-protocols="TLSv1.3" \
        --set controller.config.ssl-ciphers="TLS_AES_256_GCM_SHA384:TLS_CHACHA20_POLY1305_SHA256" \
        --wait --timeout=5m

    # metrics-server
    helm repo add metrics-server https://kubernetes-sigs.github.io/metrics-server/ --force-update
    helm upgrade --install metrics-server metrics-server/metrics-server \
        --namespace kube-system \
        --set args[0]="--kubelet-insecure-tls" \
        --wait --timeout=3m

    log INFO "Addons installed successfully"
}

# ── apply pod security standards ─────────────────────────────────────────────

apply_security_baseline() {
    export KUBECONFIG="${KUBECONFIG_OUTPUT}"
    log INFO "Applying Pod Security Standards and network policies..."

    # Label namespaces for Pod Security Standards
    kubectl apply -f - <<EOF
apiVersion: v1
kind: Namespace
metadata:
  name: casino-prod
  labels:
    pod-security.kubernetes.io/enforce: restricted
    pod-security.kubernetes.io/audit: restricted
    pod-security.kubernetes.io/warn: restricted
    cluster: ${CLUSTER_COLOR}
    environment: production
---
apiVersion: v1
kind: Namespace
metadata:
  name: casino-infra
  labels:
    pod-security.kubernetes.io/enforce: baseline
    pod-security.kubernetes.io/audit: restricted
    pod-security.kubernetes.io/warn: restricted
    cluster: ${CLUSTER_COLOR}
EOF

    # Default-deny network policy for casino-prod
    kubectl apply -f - <<EOF
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: default-deny-all
  namespace: casino-prod
spec:
  podSelector: {}
  policyTypes:
    - Ingress
    - Egress
---
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: allow-ingress-controller
  namespace: casino-prod
spec:
  podSelector:
    matchLabels:
      tier: frontend
  policyTypes:
    - Ingress
  ingress:
    - from:
        - namespaceSelector:
            matchLabels:
              kubernetes.io/metadata.name: ingress-nginx
---
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: allow-external-data
  namespace: casino-prod
spec:
  podSelector: {}
  policyTypes:
    - Egress
  egress:
    - to:
        - ipBlock:
            cidr: ${POSTGRES_HOST}/32
      ports:
        - protocol: TCP
          port: 5432
    - to:
        - ipBlock:
            cidr: ${REDIS_HOST}/32
      ports:
        - protocol: TCP
          port: 6379
    - to:
        - ipBlock:
            cidr: ${VAULT_ADDR_IP}/32
      ports:
        - protocol: TCP
          port: 8200
    - ports:
        - protocol: UDP
          port: 53
EOF
    log INFO "Security baseline applied"
}

# ── deploy casino applications ────────────────────────────────────────────────

deploy_applications() {
    export KUBECONFIG="${KUBECONFIG_OUTPUT}"
    log INFO "Deploying casino applications (image tag: ${IMAGE_TAG})..."

    # Create application secrets from Vault-sourced values
    kubectl create secret generic casino-db \
        --namespace casino-prod \
        --from-literal=host="${POSTGRES_HOST}" \
        --from-literal=password="${POSTGRES_PASSWORD}" \
        --dry-run=client -o yaml | kubectl apply -f -

    kubectl create secret generic casino-redis \
        --namespace casino-prod \
        --from-literal=host="${REDIS_HOST}" \
        --from-literal=password="${REDIS_PASSWORD}" \
        --dry-run=client -o yaml | kubectl apply -f -

    kubectl create secret generic casino-jwt \
        --namespace casino-prod \
        --from-literal=private_key="${JWT_SIGNING_KEY}" \
        --dry-run=client -o yaml | kubectl apply -f -

    # Deploy applications via Helm
    local charts_dir="${SCRIPT_DIR}/../charts"
    local apps=(wallet-service game-api auth-service websocket-gateway bonus-engine fraud-detector)

    for app in "${apps[@]}"; do
        log INFO "Deploying $app..."
        helm upgrade --install "$app" "${charts_dir}/${app}" \
            --namespace casino-prod \
            --set image.tag="${IMAGE_TAG}" \
            --set image.registry="${REGISTRY_HOST}" \
            --set cluster.color="${CLUSTER_COLOR}" \
            --set config.postgresHost="${POSTGRES_HOST}" \
            --set config.redisHost="${REDIS_HOST}" \
            --values "${charts_dir}/${app}/values-production.yaml" \
            --wait --timeout=5m \
        || die "Failed to deploy $app"
    done

    log INFO "All applications deployed"
}

# ── readiness validation ──────────────────────────────────────────────────────

validate_cluster_health() {
    export KUBECONFIG="${KUBECONFIG_OUTPUT}"
    log INFO "Validating cluster health..."
    local failed=0

    # All pods running
    local not_running
    not_running=$(kubectl get pods -n casino-prod --no-headers 2>/dev/null \
        | grep -v 'Running\|Completed' | wc -l)
    if [[ "$not_running" -gt 0 ]]; then
        log ERROR "$not_running pods not in Running state"
        kubectl get pods -n casino-prod --no-headers | grep -v 'Running\|Completed' >> "$LOG_FILE"
        ((failed++))
    fi

    # All deployments have desired replicas
    local unready_deployments
    unready_deployments=$(kubectl get deployments -n casino-prod --no-headers 2>/dev/null \
        | awk '$2 != $3 {print $1}' | wc -l)
    if [[ "$unready_deployments" -gt 0 ]]; then
        log ERROR "$unready_deployments deployments not at desired replicas"
        ((failed++))
    fi

    # Ingress controller has external IP
    local ingress_ip
    ingress_ip=$(kubectl get svc -n ingress-nginx ingress-nginx-controller \
        -o jsonpath='{.status.loadBalancer.ingress[0].ip}' 2>/dev/null)
    if [[ -z "$ingress_ip" ]]; then
        log ERROR "Ingress controller has no external IP"
        ((failed++))
    else
        log INFO "Ingress controller IP: $ingress_ip"
        echo "$ingress_ip" > "/tmp/casino-${CLUSTER_COLOR}-ingress-ip"
    fi

    # Smoke test: wallet service health endpoint
    local wallet_pod
    wallet_pod=$(kubectl get pods -n casino-prod -l app=wallet-service \
        -o jsonpath='{.items[0].metadata.name}' 2>/dev/null)
    if [[ -n "$wallet_pod" ]]; then
        local health_status
        health_status=$(kubectl exec -n casino-prod "$wallet_pod" -- \
            curl -sf http://localhost:8080/health 2>/dev/null | jq -r '.status')
        if [[ "$health_status" != "ok" ]]; then
            log ERROR "Wallet service health check failed: $health_status"
            ((failed++))
        else
            log INFO "Wallet service health: $health_status"
        fi
    fi

    if [[ $failed -gt 0 ]]; then
        die "Cluster health validation failed with $failed errors — aborting"
    fi

    log INFO "Cluster health validation passed"
}

# ── main ─────────────────────────────────────────────────────────────────────

main() {
    log INFO "Starting cluster provisioning: color=$CLUSTER_COLOR tag=$IMAGE_TAG"
    local start_time; start_time=$(date +%s)

    check_prerequisites
    fetch_secrets
    install_k3s_server
    join_workers
    install_addons
    apply_security_baseline
    deploy_applications
    validate_cluster_health

    local end_time; end_time=$(date +%s)
    local duration=$(( end_time - start_time ))
    log INFO "=== Cluster $CLUSTER_COLOR provisioned successfully in ${duration}s ==="
    log INFO "Log: $LOG_FILE"
    log INFO "Kubeconfig: $KUBECONFIG_OUTPUT"
}

main "$@"
