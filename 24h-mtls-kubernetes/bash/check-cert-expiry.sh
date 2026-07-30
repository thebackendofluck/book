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

# scripts/check-cert-expiry.sh
# Prints all Certificate resources with their expiry and renewal status.
# Run: ssh admin@ops-server "kubectl get certificates -A -o json" | ./check-cert-expiry.sh

kubectl get certificates -A -o json | \
  jq -r '
    .items[] |
    [
      .metadata.namespace,
      .metadata.name,
      (.status.notAfter // "unknown"),
      (.status.renewalTime // "unknown"),
      (if (.status.conditions[]? | select(.type=="Ready") | .status) == "True"
       then "Ready" else "NotReady" end)
    ] | @tsv' | \
  column -t -s $'\t' | \
  sort -k3
