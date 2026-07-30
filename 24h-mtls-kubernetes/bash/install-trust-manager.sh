#!/usr/bin/env bash
# Companion code for "The Backend of Luck" - Chapter 24h, Mutual TLS Between Kubernetes Services for iGaming Platforms.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# On ops-host — run after playbook-k8s-addons-deploy.yml completes
# admin@ops-server

helm repo add jetstack https://charts.jetstack.io
helm repo update
helm install trust-manager jetstack/trust-manager \
  --namespace cert-manager \
  --set app.trust.namespace=cert-manager \
  --version v0.9.2 \
  --wait

kubectl rollout status deployment trust-manager -n cert-manager
