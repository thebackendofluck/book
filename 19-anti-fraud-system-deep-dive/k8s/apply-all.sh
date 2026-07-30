#!/usr/bin/env bash
# Companion code for "The Backend of Luck" - Chapter 19, Anti-Fraud System Deep Dive.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# =============================================================================
# fraud-detection -> K3s/k3d  (idempotent deployment driver)
# =============================================================================
# Each phase waits for Ready before moving to the next. Safe to re-run.
#
# Usage:
#   ./apply-all.sh                  # full apply, all phases
#   ./apply-all.sh --phase prereqs  # only install operators / cert-manager / Traefik
#   ./apply-all.sh --phase data     # only data tier (Postgres / Redis / Kafka / ES)
#   ./apply-all.sh --phase apps     # only fraud-detection app Deployments
#   ./apply-all.sh --phase verify   # run health checks only
#   ./apply-all.sh --dry-run        # kubectl apply with --dry-run=server
#
# Environment overrides:
#   REPO_ROOT       repo root (default: two levels above this script)
#   MANIFESTS_ROOT  manifest tree (default: <script-dir>/manifests)
#   OVERLAY         kustomize overlay name (default: prod)
#   NAMESPACE       app namespace (default: fraud-detection)
#   WAIT_TIMEOUT    kubectl --timeout value (default: 600s)
#
# Requires: kubectl, helm, openssl. Run from anywhere; paths resolve via $0.
#
# NOTE on schema-registry: the fix for the "PORT is deprecated" CrashLoopBackOff
# lives in manifests/operators/schema-registry.yaml as
# `spec.template.spec.enableServiceLinks: false`. See ./troubleshooting-k3s.md
# Gotcha D for the full story.
# =============================================================================
set -euo pipefail

# --- Configurable ------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="${REPO_ROOT:-$(cd "${SCRIPT_DIR}/../../../.." && pwd)}"
MANIFESTS_ROOT="${MANIFESTS_ROOT:-${SCRIPT_DIR}/manifests}"
OVERLAY="${OVERLAY:-prod}"
NAMESPACE="${NAMESPACE:-fraud-detection}"
WAIT_TIMEOUT="${WAIT_TIMEOUT:-600s}"

# --- Args --------------------------------------------------------------------
PHASE="all"
DRY_RUN=""
while [[ $# -gt 0 ]]; do
    case "$1" in
    --phase)
        PHASE="$2"
        shift 2
        ;;
    --dry-run)
        DRY_RUN="--dry-run=server"
        shift
        ;;
    -h | --help)
        sed -n '2,30p' "$0"
        exit 0
        ;;
    *)
        echo "Unknown arg: $1" >&2
        exit 1
        ;;
    esac
done

log() { printf '\n\033[1;34m[%s]\033[0m %s\n' "$(date +%H:%M:%S)" "$*"; }
warn() { printf '\n\033[1;33m[WARN]\033[0m %s\n' "$*"; }
fail() {
    printf '\n\033[1;31m[FAIL]\033[0m %s\n' "$*" >&2
    exit 1
}

require_bin() { command -v "$1" >/dev/null 2>&1 || fail "missing binary: $1"; }
require_bin kubectl
require_bin helm
require_bin openssl

# --- Phase: prereqs ----------------------------------------------------------
phase_prereqs() {
    log "Phase 1: cluster-wide operators"

    log "helm repo add/update"
    helm repo add jetstack https://charts.jetstack.io --force-update >/dev/null
    helm repo add traefik https://traefik.github.io/charts --force-update >/dev/null
    helm repo add strimzi https://strimzi.io/charts/ --force-update >/dev/null
    helm repo add elastic https://helm.elastic.co --force-update >/dev/null
    helm repo add prometheus-community https://prometheus-community.github.io/helm-charts --force-update >/dev/null
    helm repo update >/dev/null

    # ---- cert-manager -------------------------------------------------------
    # NOTE: --set startupapicheck.enabled=false avoids Gotcha A
    #       (startupapicheck Pending on k3d due to kubelet proxy race).
    # NOTE: every subcomponent gets explicit resources.limits to satisfy a
    #       Kyverno require-pod-resources policy (Gotcha F).
    log "cert-manager"
    kubectl create namespace cert-manager --dry-run=client -o yaml | kubectl apply -f -
    helm upgrade --install cert-manager jetstack/cert-manager \
        --namespace cert-manager \
        --version v1.15.3 \
        --set installCRDs=true \
        --set resources.limits.cpu=200m --set resources.limits.memory=256Mi \
        --set resources.requests.cpu=50m --set resources.requests.memory=64Mi \
        --set webhook.resources.limits.cpu=200m --set webhook.resources.limits.memory=128Mi \
        --set webhook.resources.requests.cpu=50m --set webhook.resources.requests.memory=32Mi \
        --set cainjector.resources.limits.cpu=200m --set cainjector.resources.limits.memory=256Mi \
        --set cainjector.resources.requests.cpu=50m --set cainjector.resources.requests.memory=64Mi \
        --set startupapicheck.enabled=false \
        --wait --timeout "${WAIT_TIMEOUT}"

    # ---- Traefik ------------------------------------------------------------
    log "traefik"
    kubectl create namespace traefik --dry-run=client -o yaml | kubectl apply -f -
    helm upgrade --install traefik traefik/traefik \
        --namespace traefik \
        --version 28.3.0 \
        --set service.type=ClusterIP \
        --set ingressClass.isDefaultClass=true \
        --set providers.kubernetesCRD.enabled=true \
        --set providers.kubernetesIngress.enabled=true \
        --set resources.limits.cpu=500m --set resources.limits.memory=256Mi \
        --set resources.requests.cpu=100m --set resources.requests.memory=64Mi \
        --wait --timeout "${WAIT_TIMEOUT}"
    warn "k3d clusters do NOT bind 30080/30443 to the host by default."
    warn "LAN access requires either:"
    warn "  (a) recreate the k3d cluster with '--port 443:30443@loadbalancer'"
    warn "  (b) run 'kubectl -n traefik port-forward svc/traefik 8443:443'"
    warn "  (c) add a LoadBalancer IP via MetalLB (needs host-bound Docker network)"

    # ---- Strimzi Kafka Operator --------------------------------------------
    log "strimzi-kafka-operator"
    kubectl create namespace strimzi-system --dry-run=client -o yaml | kubectl apply -f -
    helm upgrade --install strimzi-kafka-operator strimzi/strimzi-kafka-operator \
        --namespace strimzi-system \
        --version 0.41.0 \
        --set watchAnyNamespace=true \
        --set resources.limits.cpu=500m --set resources.limits.memory=384Mi \
        --set resources.requests.cpu=100m --set resources.requests.memory=128Mi \
        --wait --timeout "${WAIT_TIMEOUT}"

    # If Kyverno is installed and auto-generates default-deny NetworkPolicies,
    # the operator will silently fail leader election (Gotcha C). Allow egress
    # to the apiserver:
    if kubectl get crd clusterpolicies.kyverno.io >/dev/null 2>&1; then
        warn "Kyverno detected — applying allow-apiserver-egress NetworkPolicy in strimzi-system"
        cat <<'EOF' | kubectl apply -f -
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: allow-apiserver-egress
  namespace: strimzi-system
spec:
  podSelector: {}
  policyTypes: [Egress]
  egress:
    - to:
        - ipBlock:
            cidr: 10.43.0.1/32  # ClusterIP of kubernetes.default
      ports:
        - protocol: TCP
          port: 443
    - to:
        - ipBlock:
            cidr: 0.0.0.0/0
            except: [10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16]
      ports:
        - protocol: TCP
          port: 6443
    - ports:  # DNS
        - protocol: UDP
          port: 53
EOF
    fi

    # ---- ECK (Elastic Cloud on Kubernetes) ---------------------------------
    log "eck-operator"
    kubectl create namespace elastic-system --dry-run=client -o yaml | kubectl apply -f -
    helm upgrade --install eck-operator elastic/eck-operator \
        --namespace elastic-system \
        --version 2.14.0 \
        --set resources.limits.cpu=1 --set resources.limits.memory=512Mi \
        --set resources.requests.cpu=100m --set resources.requests.memory=128Mi \
        --wait --timeout "${WAIT_TIMEOUT}"

    log "Waiting for operator CRDs to register"
    kubectl wait --for=condition=Established --timeout=120s \
        crd/kafkas.kafka.strimzi.io \
        crd/elasticsearches.elasticsearch.k8s.elastic.co \
        crd/kibanas.kibana.k8s.elastic.co \
        crd/clusterissuers.cert-manager.io
}

# --- Phase: secrets & namespace ---------------------------------------------
phase_secrets() {
    log "Phase 2a: namespace + seeded secrets"

    kubectl apply -f "${MANIFESTS_ROOT}/base/namespace.yaml" ${DRY_RUN}

    seed_secret() {
        local name="$1"
        shift
        if kubectl -n "${NAMESPACE}" get secret "${name}" >/dev/null 2>&1; then
            warn "secret ${name} already exists, leaving in place (rotate via your secrets manager)"
            return
        fi
        log "seeding secret ${name}"
        kubectl -n "${NAMESPACE}" create secret generic "${name}" "$@" ${DRY_RUN}
    }

    local pg_pass
    pg_pass="$(openssl rand -base64 24 | tr -d '=+/' | cut -c1-24)"
    seed_secret postgres-credentials \
        --from-literal=username="fraud_user" \
        --from-literal=password="${pg_pass}" \
        --from-literal=postgres-password="${pg_pass}"

    local redis_pass
    redis_pass="$(openssl rand -base64 24 | tr -d '=+/' | cut -c1-24)"
    seed_secret redis-credentials \
        --from-literal=redis-password="${redis_pass}"

    local grafana_pass
    grafana_pass="$(openssl rand -base64 24 | tr -d '=+/' | cut -c1-24)"
    seed_secret grafana-admin \
        --from-literal=admin-user="admin" \
        --from-literal=admin-password="${grafana_pass}"

    log "Phase 2b: ConfigMaps, NetworkPolicies, init-sql"
    kubectl apply ${DRY_RUN} \
        -f "${MANIFESTS_ROOT}/base/configmap-app.yaml" \
        -f "${MANIFESTS_ROOT}/base/configmap-prometheus.yaml" \
        -f "${MANIFESTS_ROOT}/base/network-policies.yaml" \
        -f "${MANIFESTS_ROOT}/base/init-sql.yaml"

    log "Phase 2c: cert-manager ClusterIssuers (cluster-scope)"
    kubectl apply ${DRY_RUN} -f "${MANIFESTS_ROOT}/operators/cert-manager-issuers.yaml"
}

# --- Phase: data -------------------------------------------------------------
phase_data() {
    log "Phase 3: data tier (Postgres, Redis)"
    kubectl apply ${DRY_RUN} \
        -f "${MANIFESTS_ROOT}/base/postgres.yaml" \
        -f "${MANIFESTS_ROOT}/base/redis.yaml"

    log "Waiting for Postgres Ready"
    kubectl -n "${NAMESPACE}" rollout status statefulset/postgres --timeout="${WAIT_TIMEOUT}"
    log "Waiting for Redis Ready"
    kubectl -n "${NAMESPACE}" rollout status statefulset/redis-master --timeout="${WAIT_TIMEOUT}"

    log "Phase 4: Kafka (Strimzi) + Schema Registry"
    kubectl apply ${DRY_RUN} -f "${MANIFESTS_ROOT}/operators/strimzi-kafka.yaml"
    log "Waiting for Strimzi to reconcile Kafka CR (up to 5 min)"
    kubectl -n "${NAMESPACE}" wait kafka/fraud-kafka --for=condition=Ready --timeout=300s \
        || warn "Kafka not Ready within 5min — check 'kubectl -n ${NAMESPACE} describe kafka fraud-kafka' (see troubleshooting-k3s.md Gotcha C)"

    kubectl apply ${DRY_RUN} -f "${MANIFESTS_ROOT}/operators/schema-registry.yaml"
    kubectl -n "${NAMESPACE}" rollout status deployment/schema-registry --timeout="${WAIT_TIMEOUT}" \
        || warn "schema-registry still rolling — see troubleshooting-k3s.md Gotcha D"

    log "Phase 5: Elasticsearch + Kibana (ECK)"
    kubectl apply ${DRY_RUN} -f "${MANIFESTS_ROOT}/operators/eck-elasticsearch.yaml"
    log "Waiting for Elasticsearch green (up to 5 min)"
    local health=""
    for _ in $(seq 1 30); do
        health="$(kubectl -n "${NAMESPACE}" get elasticsearch fraud-es -o jsonpath='{.status.health}' 2>/dev/null || echo '')"
        [[ "${health}" == "green" || "${health}" == "yellow" ]] && break
        sleep 10
    done
    [[ "${health}" == "green" || "${health}" == "yellow" ]] \
        || warn "Elasticsearch not Ready — check 'kubectl -n ${NAMESPACE} describe elasticsearch fraud-es'"
}

# --- Phase: apps -------------------------------------------------------------
phase_apps() {
    log "Phase 6: fraud-detection apps (overlay=${OVERLAY})"
    kubectl apply -k "${MANIFESTS_ROOT}/overlays/${OVERLAY}" ${DRY_RUN}

    for d in data-ingestion feature-engineering model-serving alerting mailhog; do
        log "Waiting for deployment ${d}"
        kubectl -n "${NAMESPACE}" rollout status deployment/"${d}" --timeout="${WAIT_TIMEOUT}" \
            || warn "${d} rollout timed out (image may still be a placeholder)"
    done

    log "Phase 7: kube-prometheus-stack (optional)"
    if [[ -f "${SCRIPT_DIR}/monitoring-values.yaml" ]]; then
        helm upgrade --install fraud-monitoring prometheus-community/kube-prometheus-stack \
            --namespace "${NAMESPACE}" \
            --version 62.3.0 \
            -f "${SCRIPT_DIR}/monitoring-values.yaml" \
            --wait --timeout "${WAIT_TIMEOUT}" \
            || warn "kube-prometheus-stack install failed or timed out"
    else
        warn "monitoring-values.yaml not found at ${SCRIPT_DIR}; skipping Prometheus install"
    fi
}

# --- Phase: verify -----------------------------------------------------------
phase_verify() {
    log "Phase 8: verification"

    echo
    log "Pods in ${NAMESPACE}:"
    kubectl get pods -n "${NAMESPACE}" -o wide

    echo
    log "Services in ${NAMESPACE}:"
    kubectl get svc -n "${NAMESPACE}"

    echo
    log "Ingress:"
    kubectl get ingress -n "${NAMESPACE}"

    echo
    log "Internal health-check pod (curl each /health endpoint from inside the cluster)"
    # shellcheck disable=SC2016  # expressions below expand inside the pod, not on the host
    kubectl run fraud-verify --rm -i --restart=Never \
        --image=curlimages/curl:8.9.1 \
        --namespace="${NAMESPACE}" -- sh -c '
            set +e
            for svc in data-ingestion:80 feature-engineering:80 model-serving:80 alerting:80 schema-registry:8081 fraud-es-es-http:9200 fraud-kibana-kb-http:5601; do
                name="${svc%%:*}"
                port="${svc##*:}"
                code="$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 "http://${name}:${port}/health" || true)"
                [ "${code}" = "000" ] && code="$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 "http://${name}:${port}/" || true)"
                printf "  %-30s HTTP %s\n" "${name}:${port}" "${code}"
            done
        ' 2>/dev/null || warn "verify pod failed (pods may not be Ready yet)"

    echo
    log "Strimzi Kafka status:"
    kubectl -n "${NAMESPACE}" get kafka,kafkatopic,kafkanodepool 2>/dev/null || true

    echo
    log "Elasticsearch status:"
    kubectl -n "${NAMESPACE}" get elasticsearch,kibana 2>/dev/null || true
}

# --- Orchestration -----------------------------------------------------------
case "${PHASE}" in
prereqs) phase_prereqs ;;
secrets) phase_secrets ;;
data)
    phase_secrets
    phase_data
    ;;
apps) phase_apps ;;
verify) phase_verify ;;
all)
    phase_prereqs
    phase_secrets
    phase_data
    phase_apps
    phase_verify
    ;;
*) fail "unknown --phase ${PHASE}" ;;
esac

log "Done. See ./troubleshooting-k3s.md for symptom -> fix recipes if anything stalled."
