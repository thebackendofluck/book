#!/usr/bin/env bash
# Companion code for "The Backend of Luck" - Chapter 24m, Security Workflow Automation with n8n on Kubernetes.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# npm-cert-sync.sh — pull the K8s n8n-tls Secret and push it into
# nginx-proxy-manager's Custom SSL store. Run on a 12-hour schedule.
# See Chapter 24m, section 6.1.
set -euo pipefail

NAMESPACE=n8n
SECRET=n8n-tls
NPM_CERT_ID=${NPM_INTERNAL_CERT_ID:?set via env}
NPM_API=${NPM_API:?}
NPM_TOKEN=${NPM_TOKEN:?} # rotated via OpenBao — see Chapter 20b

# Materialise the cert/key to short-lived temp files so curl -F uploads the
# actual bytes (process substitution does not work with multipart uploads).
workdir=$(mktemp -d)
trap 'rm -rf "$workdir"' EXIT

kubectl -n "$NAMESPACE" get secret "$SECRET" \
  -o jsonpath='{.data.tls\.crt}' | base64 -d >"$workdir/tls.crt"
kubectl -n "$NAMESPACE" get secret "$SECRET" \
  -o jsonpath='{.data.tls\.key}' | base64 -d >"$workdir/tls.key"

curl -fsS -X POST "$NPM_API/api/nginx/certificates/$NPM_CERT_ID/upload" \
  -H "Authorization: Bearer $NPM_TOKEN" \
  -F "certificate=@$workdir/tls.crt" \
  -F "certificate_key=@$workdir/tls.key"
