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

# Deploy an already-built, attested image to a namespace during an incident.
# Section 6.2 — Image hotfix for a trivial code change
#
# Usage:
#   hotfix-image-deploy.sh --change-ref INC-1234 \
#       --deployment bonus-engine --namespace casino-staging --tag <ci-tag>
#
#   hotfix-image-deploy.sh --change-ref INC-1234 \
#       --deployment bonus-engine --namespace casino-prod --tag <ci-tag> \
#       --i-accept-production-impact
#
# This script does NOT build the image. That is deliberate. A build from a
# working tree has no review, no test run and no provenance, and "it is one
# line" is not a property anyone can verify after the fact. Build in the
# pipeline (Chapter 23) and deploy what the pipeline signed.
#
# Two changes must never come down this path at all, in any jurisdiction:
#   - anything that alters game mathematics (RTP, paytables, RNG, house edge)
#   - anything that alters wallet or ledger arithmetic
# Both are certified artefacts. Changing them outside the certification process
# is a licence problem regardless of how small the diff is or how fast the
# rollout was. Disable the affected game and take the revenue hit.

set -euo pipefail

# shellcheck source-path=SCRIPTDIR
# shellcheck source=hotfix-guard.sh
source "$(dirname "$0")/hotfix-guard.sh"

hotfix_parse_flags "$@"
set -- "${HOTFIX_REMAINING_ARGS[@]+"${HOTFIX_REMAINING_ARGS[@]}"}"

# Fail before touching the cluster at all, not after the first read.
hotfix_require_change_ref

REGISTRY="${REGISTRY:-registry.ops-host.local:5000}"
DEPLOYMENT=""
NAMESPACE=""
TAG=""

while [ "$#" -gt 0 ]; do
    case "$1" in
        --deployment) DEPLOYMENT="$2"; shift 2 ;;
        --namespace|-n) NAMESPACE="$2"; shift 2 ;;
        --tag) TAG="$2"; shift 2 ;;
        *) hotfix_die "unknown argument: $1" ;;
    esac
done

[ -n "$DEPLOYMENT" ] || hotfix_die "--deployment is required"
[ -n "$NAMESPACE" ] || hotfix_die "--namespace is required"
[ -n "$TAG" ] || hotfix_die "--tag is required (the tag the pipeline published)"

IMAGE="${REGISTRY}/casino/${DEPLOYMENT}:${TAG}"

hotfix_require_production_override "$NAMESPACE"

# The image has to exist in the registry before we look at its provenance.
# A tag that resolves nowhere usually means somebody expected this script to
# build for them.
crane manifest "$IMAGE" >/dev/null 2>&1 \
    || hotfix_die "$IMAGE is not in the registry.
   This script deploys pipeline artefacts; it does not build. Push a CI build
   of the fix and pass its tag."

hotfix_verify_image_provenance "$IMAGE"

kubectl get deployment "$DEPLOYMENT" -n "$NAMESPACE" >/dev/null \
    || hotfix_die "deployment/$DEPLOYMENT not found in namespace $NAMESPACE"

CURRENT_IMAGE=$(kubectl get deployment "$DEPLOYMENT" -n "$NAMESPACE" \
    -o jsonpath='{.spec.template.spec.containers[0].image}')
printf 'hotfix: %s currently runs %s\n' "$DEPLOYMENT" "$CURRENT_IMAGE" >&2

hotfix_authorize "$NAMESPACE" "set image deployment/${DEPLOYMENT} to ${IMAGE} (from ${CURRENT_IMAGE})"

# Record the reason on the object itself, so `kubectl rollout history` and the
# next responder both see why this revision exists.
kubectl annotate deployment "$DEPLOYMENT" -n "$NAMESPACE" --overwrite \
    "kubernetes.io/change-cause=hotfix ${TAG} for ${HOTFIX_CHANGE_REF} by $(hotfix_operator)" \
    "hotfix/change-ref=${HOTFIX_CHANGE_REF}" \
    "hotfix/previous-image=${CURRENT_IMAGE}"

kubectl set image "deployment/${DEPLOYMENT}" \
    --namespace "$NAMESPACE" \
    "${DEPLOYMENT}=${IMAGE}"

if kubectl rollout status "deployment/${DEPLOYMENT}" -n "$NAMESPACE" --timeout=120s; then
    hotfix_audit "$NAMESPACE" "set image deployment/${DEPLOYMENT} to ${IMAGE}" "rolled-out"
else
    hotfix_audit "$NAMESPACE" "set image deployment/${DEPLOYMENT} to ${IMAGE}" "rollout-failed"
    printf 'hotfix: rollout did not complete. Previous image was %s\n' "$CURRENT_IMAGE" >&2
    printf 'hotfix: roll back with: kubectl rollout undo deployment/%s -n %s\n' \
        "$DEPLOYMENT" "$NAMESPACE" >&2
    exit 1
fi

# Verify what is actually running, not what we asked for.
kubectl get pods -n "$NAMESPACE" -l "app=${DEPLOYMENT}" \
    -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{.spec.containers[0].image}{"\n"}{end}'
