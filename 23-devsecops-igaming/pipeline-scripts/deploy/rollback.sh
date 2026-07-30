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

set -euo pipefail

NAMESPACE="${NAMESPACE:-production}"
DEPLOYMENT="${DEPLOYMENT:-casino-platform}"
REVISION="${1:-}"

if [[ -n "$REVISION" ]]; then
  kubectl rollout undo "deployment/${DEPLOYMENT}" --to-revision="$REVISION" -n "$NAMESPACE"
else
  kubectl rollout undo "deployment/${DEPLOYMENT}" -n "$NAMESPACE"
fi

kubectl rollout status "deployment/${DEPLOYMENT}" -n "$NAMESPACE" --timeout=180s
