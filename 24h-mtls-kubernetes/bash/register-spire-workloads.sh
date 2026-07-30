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

# Register player-service workload
kubectl exec -n spire spire-server-0 -- \
  /opt/spire/bin/spire-server entry create \
  -spiffeID spiffe://casino.internal/ns/player-service/sa/player-service \
  -parentID spiffe://casino.internal/k8s-node/$(kubectl get node 10.0.10.21 -o jsonpath='{.metadata.name}') \
  -selector k8s:ns:player-service \
  -selector k8s:sa:player-service \
  -selector k8s:pod-label:app:player-service \
  -ttl 3600

# Register wallet-service workload
kubectl exec -n spire spire-server-0 -- \
  /opt/spire/bin/spire-server entry create \
  -spiffeID spiffe://casino.internal/ns/payments/sa/wallet-service \
  -parentID spiffe://casino.internal/k8s-node/$(kubectl get node 10.0.10.21 -o jsonpath='{.metadata.name}') \
  -selector k8s:ns:payments \
  -selector k8s:sa:wallet-service \
  -selector k8s:pod-label:app:wallet-service \
  -ttl 3600
