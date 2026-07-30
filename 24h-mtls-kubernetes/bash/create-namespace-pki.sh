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

# scripts/create-namespace-pki.sh
set -euo pipefail

NAMESPACE="${1:?Usage: $0 <namespace>}"

cat <<EOF | kubectl apply -f -
---
apiVersion: v1
kind: Namespace
metadata:
  name: ${NAMESPACE}
  labels:
    mtls-enabled: "true"
---
apiVersion: cert-manager.io/v1
kind: Certificate
metadata:
  name: intermediate-ca
  namespace: ${NAMESPACE}
spec:
  isCA: true
  commonName: casino-intermediate-ca-${NAMESPACE}
  subject:
    organizations:
      - CasinoInfrastructure
    organizationalUnits:
      - ${NAMESPACE}
  duration: 8760h
  renewBefore: 720h
  secretName: intermediate-ca-key-pair
  privateKey:
    algorithm: ECDSA
    size: 256
  issuerRef:
    name: casino-internal-ca
    kind: ClusterIssuer
    group: cert-manager.io
---
apiVersion: cert-manager.io/v1
kind: Issuer
metadata:
  name: namespace-issuer
  namespace: ${NAMESPACE}
spec:
  ca:
    secretName: intermediate-ca-key-pair
EOF

echo "PKI created for namespace: ${NAMESPACE}"
