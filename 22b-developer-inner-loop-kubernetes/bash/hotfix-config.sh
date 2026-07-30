#!/usr/bin/env bash
# Companion code for "The Backend of Luck" - Chapter 22b, Developer Inner-Loop Experience in Containerized iGaming Pla.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# Change one environment variable on a running deployment during an incident.
# Section 6.2 — Configuration hotfix
#
# Usage:
#   hotfix-config.sh --change-ref INC-1234 \
#       --deployment wallet-service --namespace casino-staging \
#       --set PAYMENT_GATEWAY_TIMEOUT_MS=5000
#
#   ... --namespace casino-prod --i-accept-production-impact
#
# A `kubectl set env` is a configuration change that outlives the incident: it
# lives on the live object and not in the repository, so the next deploy from
# git silently reverts it. Whatever you set here, open the follow-up to put it
# in the manifest before you close the incident.
#
# Values that are part of a certified configuration (RTP targets, bet limits,
# responsible-gaming thresholds, AML limits) do not belong on this path. They
# are reviewed settings, and a live edit to one is an unrecorded change to what
# the licence covers.

set -euo pipefail

# shellcheck source-path=SCRIPTDIR
# shellcheck source=hotfix-guard.sh
source "$(dirname "$0")/hotfix-guard.sh"

hotfix_parse_flags "$@"
set -- "${HOTFIX_REMAINING_ARGS[@]+"${HOTFIX_REMAINING_ARGS[@]}"}"

# Fail before touching the cluster at all, not after the first read.
hotfix_require_change_ref

DEPLOYMENT=""
NAMESPACE=""
ENV_ASSIGNMENTS=()

while [ "$#" -gt 0 ]; do
    case "$1" in
        --deployment) DEPLOYMENT="$2"; shift 2 ;;
        --namespace|-n) NAMESPACE="$2"; shift 2 ;;
        --set) ENV_ASSIGNMENTS+=("$2"); shift 2 ;;
        *) hotfix_die "unknown argument: $1" ;;
    esac
done

[ -n "$DEPLOYMENT" ] || hotfix_die "--deployment is required"
[ -n "$NAMESPACE" ] || hotfix_die "--namespace is required"
[ "${#ENV_ASSIGNMENTS[@]}" -gt 0 ] || hotfix_die "at least one --set KEY=VALUE is required"

for assignment in "${ENV_ASSIGNMENTS[@]}"; do
    printf '%s' "$assignment" | grep -Eq '^[A-Z_][A-Z0-9_]*=' \
        || hotfix_die "'$assignment' is not a KEY=VALUE assignment"
done

hotfix_require_production_override "$NAMESPACE"

kubectl get deployment "$DEPLOYMENT" -n "$NAMESPACE" >/dev/null \
    || hotfix_die "deployment/$DEPLOYMENT not found in namespace $NAMESPACE"

# Capture the values we are about to overwrite, so the audit record says what
# changed and not just what it changed to.
for assignment in "${ENV_ASSIGNMENTS[@]}"; do
    key="${assignment%%=*}"
    previous=$(kubectl get deployment "$DEPLOYMENT" -n "$NAMESPACE" \
        -o jsonpath="{.spec.template.spec.containers[0].env[?(@.name=='${key}')].value}")
    printf 'hotfix: %s currently %s=%s\n' "$DEPLOYMENT" "$key" "${previous:-<unset>}" >&2
    hotfix_audit "$NAMESPACE" "env ${key}: '${previous:-<unset>}' -> '${assignment#*=}' on deployment/${DEPLOYMENT}" "previous-value"
done

hotfix_authorize "$NAMESPACE" "set env ${ENV_ASSIGNMENTS[*]} on deployment/${DEPLOYMENT}"

kubectl annotate deployment "$DEPLOYMENT" -n "$NAMESPACE" --overwrite \
    "kubernetes.io/change-cause=config hotfix ${ENV_ASSIGNMENTS[*]} for ${HOTFIX_CHANGE_REF} by $(hotfix_operator)" \
    "hotfix/change-ref=${HOTFIX_CHANGE_REF}"

kubectl set env "deployment/${DEPLOYMENT}" \
    --namespace "$NAMESPACE" \
    "${ENV_ASSIGNMENTS[@]}"

# `kubectl set env` already triggers a rollout by mutating the pod template;
# an explicit restart here would start a second one.
if kubectl rollout status "deployment/${DEPLOYMENT}" -n "$NAMESPACE" --timeout=120s; then
    hotfix_audit "$NAMESPACE" "set env ${ENV_ASSIGNMENTS[*]} on deployment/${DEPLOYMENT}" "rolled-out"
else
    hotfix_audit "$NAMESPACE" "set env ${ENV_ASSIGNMENTS[*]} on deployment/${DEPLOYMENT}" "rollout-failed"
    printf 'hotfix: roll back with: kubectl rollout undo deployment/%s -n %s\n' \
        "$DEPLOYMENT" "$NAMESPACE" >&2
    exit 1
fi

printf 'hotfix: %s applied. Open the follow-up to move this into the manifest.\n' \
    "${ENV_ASSIGNMENTS[*]}" >&2
