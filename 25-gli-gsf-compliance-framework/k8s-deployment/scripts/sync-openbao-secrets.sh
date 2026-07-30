#!/usr/bin/env bash
# Companion code for "The Backend of Luck" - Chapter 25, GLI-GSF Compliance Framework.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# Pull GLI-13 mTLS material from OpenBao and create the corresponding K8s
# Secrets in the gli-compliance namespace.
#
# This is the "manual" injection pattern used because the cluster does not
# (yet) run the Vault Agent Injector, External Secrets Operator, or the
# Secret-Store CSI driver. Run on a cron from a host with `bao` in PATH and
# kubectl access. In a future iteration, replace with one of the operators
# above and delete this script.
#
# Required env:
#   BAO_ADDR        e.g. https://127.0.0.1:8200
#   BAO_TOKEN       OpenBao token with read on secret/gli-compliance/mcs
#   BAO_SKIP_VERIFY (optional, set to "true" for self-signed internal CA)
#   KUBECONFIG      kubectl access to the cluster
#
# Exit codes:
#   0  secrets created/updated
#   2  config / dependency error

set -euo pipefail

: "${BAO_ADDR:?BAO_ADDR required}"
: "${BAO_TOKEN:?BAO_TOKEN required}"
NAMESPACE="${NAMESPACE:-gli-compliance}"
SECRET_PATH="${SECRET_PATH:-secret/gli-compliance/mcs}"

for cmd in bao kubectl python3; do
    if ! command -v "$cmd" >/dev/null 2>&1; then
        echo "error: $cmd not on PATH" >&2
        exit 2
    fi
done

tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

bao kv get -format=json "$SECRET_PATH" > "$tmp/raw.json"

python3 - "$tmp" <<'PY'
import json, os, sys
tmp = sys.argv[1]
data = json.load(open(f"{tmp}/raw.json"))["data"]["data"]
for key, fname in (("client_crt", "client.crt"),
                   ("client_key", "client.key"),
                   ("ca_crt",     "ca.crt")):
    open(f"{tmp}/{fname}", "w").write(data[key])
open(f"{tmp}/host", "w").write(data["mcs_host"])
open(f"{tmp}/port", "w").write(str(data["mcs_port"]))
PY

kubectl -n "$NAMESPACE" create secret generic mcs-mtls \
    --from-file=client.crt="$tmp/client.crt" \
    --from-file=client.key="$tmp/client.key" \
    --from-file=ca.crt="$tmp/ca.crt" \
    --dry-run=client -o yaml | kubectl apply -f -

kubectl -n "$NAMESPACE" create secret generic mcs-mtls-runtime \
    --from-file=host="$tmp/host" \
    --from-file=port="$tmp/port" \
    --dry-run=client -o yaml | kubectl apply -f -

echo "OK: mcs-mtls and mcs-mtls-runtime synced from $SECRET_PATH"
