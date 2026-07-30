#!/usr/bin/env bash
# Companion code for "The Backend of Luck" - Chapter 47d, Casino as a Service.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: BUSL-1.1
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# provision_tenant.sh — POSIX/bash wrapper for CaaS tenant provisioning
#
# Usage:
#   [DRY_RUN=1] ./provision_tenant.sh <tenant-slug>
#
# Environment:
#   DRY_RUN=1  (default) — echo commands without executing
#   DRY_RUN=0            — execute commands against the live cluster
#
# Steps performed (matching caasctl provision):
#   1. Create Kubernetes namespace caas-<slug>
#   2. Provision CNPG database caas-<slug>-db
#   3. Seed secrets in OpenBao at caas/<slug>/
#   4. Issue TLS certificate for <slug>.acmetocasino.com
#   5. Helm release caas-<slug> in namespace caas-<slug>
#   6. Run database migrations
#   7. Run smoke test

set -euo pipefail

# ---------------------------------------------------------------------------
# Configuration defaults
# ---------------------------------------------------------------------------
DRY_RUN="${DRY_RUN:-1}"
DOMAIN="${DOMAIN:-acmetocasino.com}"
JURISDICTION="${JURISDICTION:-br}"
ENVIRONMENT="${ENVIRONMENT:-staging}"
CHART="${CHART:-charts/tenant-runtime}"

# ---------------------------------------------------------------------------
# run_cmd — echo or execute depending on DRY_RUN
# ---------------------------------------------------------------------------
run_cmd() {
    if [ "${DRY_RUN}" = "1" ]; then
        printf '[DRY-RUN] %s\n' "$*"
    else
        printf '[EXECUTE] %s\n' "$*"
        "$@"
    fi
}

# ---------------------------------------------------------------------------
# log — simple timestamped info message
# ---------------------------------------------------------------------------
log() {
    printf '[%s] %s\n' "$(date '+%Y-%m-%dT%H:%M:%S')" "$*"
}

# ---------------------------------------------------------------------------
# validate_slug — kebab-case, 3-40 chars, no leading/trailing hyphen
# ---------------------------------------------------------------------------
validate_slug() {
    local slug="$1"
    # Regex must NOT be quoted (shellcheck SC2076)
    if [[ ! $slug =~ ^[a-z0-9][a-z0-9-]{1,38}[a-z0-9]$ ]]; then
        printf 'ERROR: invalid slug %q — must be 3-40 chars, lowercase letters/digits/hyphens, no leading/trailing hyphen.\n' "$slug" >&2
        return 1
    fi
    return 0
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
if [ $# -lt 1 ]; then
    printf 'Usage: [DRY_RUN=1] %s <tenant-slug>\n' "$0" >&2
    exit 1
fi

slug="$1"

validate_slug "$slug"

NAMESPACE="caas-${slug}"
DB_NAME="caas-${slug}-db"
SECRET_PATH="caas/${slug}/"
CERT_DOMAIN="${slug}.${DOMAIN}"
HELM_RELEASE="caas-${slug}"

log "Provisioning tenant '${slug}' [DRY_RUN=${DRY_RUN}] env=${ENVIRONMENT} jurisdiction=${JURISDICTION} domain=${DOMAIN}"

# ---------------------------------------------------------------------------
# Step 1 — Kubernetes namespace
# ---------------------------------------------------------------------------
log "Step 1/7 — Create namespace ${NAMESPACE}"
run_cmd kubectl create namespace "${NAMESPACE}" \
    --dry-run=client -o yaml
run_cmd kubectl apply -f - \
    --server-side=true

# ---------------------------------------------------------------------------
# Step 2 — CNPG database cluster
# ---------------------------------------------------------------------------
log "Step 2/7 — Provision CNPG database ${DB_NAME}"
run_cmd kubectl apply \
    -f "manifests/cnpg-cluster-${slug}.yaml" \
    --namespace "${NAMESPACE}"

# ---------------------------------------------------------------------------
# Step 3 — Seed secrets in OpenBao
# ---------------------------------------------------------------------------
log "Step 3/7 — Seed secrets at ${SECRET_PATH}"
run_cmd bao kv put "${SECRET_PATH}" \
    db_url="postgres://caas:CHANGE_ME@${DB_NAME}.${NAMESPACE}.svc.cluster.local:5432/casino" \
    jwt_secret="CHANGE_ME" \
    psp_key="CHANGE_ME"

# ---------------------------------------------------------------------------
# Step 4 — TLS certificate via cert-manager
# ---------------------------------------------------------------------------
log "Step 4/7 — Issue TLS cert for ${CERT_DOMAIN}"
run_cmd kubectl apply \
    -f "manifests/certificate-${slug}.yaml" \
    --namespace "${NAMESPACE}"

# ---------------------------------------------------------------------------
# Step 5 — Helm release
# ---------------------------------------------------------------------------
log "Step 5/7 — Deploy Helm release ${HELM_RELEASE}"
run_cmd helm upgrade --install "${HELM_RELEASE}" "${CHART}" \
    --namespace "${NAMESPACE}" \
    --set "tenant.slug=${slug}" \
    --set "tenant.domain=${CERT_DOMAIN}" \
    --set "tenant.jurisdiction=${JURISDICTION}" \
    --set "tenant.environment=${ENVIRONMENT}" \
    --wait \
    --timeout 5m

# ---------------------------------------------------------------------------
# Step 6 — Database migrations
# ---------------------------------------------------------------------------
log "Step 6/7 — Run database migrations"
run_cmd kubectl create job \
    --from="cronjob/db-migrate" \
    "migrate-${slug}-$(date +%s)" \
    --namespace "${NAMESPACE}"

# ---------------------------------------------------------------------------
# Step 7 — Smoke test
# ---------------------------------------------------------------------------
log "Step 7/7 — Smoke test"
run_cmd kubectl run "smoke-${slug}" \
    --image=curlimages/curl \
    --rm -i \
    --restart=Never \
    --namespace "${NAMESPACE}" \
    -- curl -sf "http://${HELM_RELEASE}/health"

log "Provisioning complete for tenant '${slug}'"
