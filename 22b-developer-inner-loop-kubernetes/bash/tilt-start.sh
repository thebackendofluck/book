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

# Start Tilt for Kubernetes inner-loop development
# Section 3.2 — Full Tiltfile for Casino Microservices

# Connect to ops-host K3s
export KUBECONFIG=~/.kube/ops-host-config

# Verify you are on the right cluster
kubectl config current-context
# Expected: k3s-ops-host

# Start Tilt — opens browser at http://localhost:10350
tilt up

# Or headless if you prefer terminal-only
# tilt up --stream
