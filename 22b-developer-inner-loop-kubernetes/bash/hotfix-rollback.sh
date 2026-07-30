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

# Roll back a deployment to a previous known-good revision
# Section 6.2 — Rollback to previous version (< 1 second, zero dropped requests)

# Check the rollout history — which revision was the last known-good?
kubectl rollout history deployment/bonus-engine -n casino-prod
# REVISION  CHANGE-CAUSE
# 1         Deployed v2.14.1 — bonus calculation refactor
# 2         Deployed v2.15.0 — new loyalty tier integration
# 3         Deployed v2.15.1 — hotfix for nil pointer in tier lookup

# Roll back to revision 2 (one before current)
kubectl rollout undo deployment/bonus-engine \
    --namespace casino-prod \
    --to-revision=2

# Watch the rollout — old pods come up, new pods terminate
kubectl rollout status deployment/bonus-engine -n casino-prod --timeout=120s

# Verify the running image is the expected revision
kubectl get deployment bonus-engine -n casino-prod \
    -o jsonpath='{.spec.template.spec.containers[0].image}'
