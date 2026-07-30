#!/usr/bin/env bash
# Companion code for "The Backend of Luck" - Chapter 47d, Casino as a Service.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: BUSL-1.1
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# render_manifests.sh — render the per-tenant Kubernetes manifests from the
# templates in ../manifests/ into ../manifests/rendered/<slug>/, ready for
# `kubectl apply`. Pure text substitution; no cluster access.
#
# Usage: render_manifests.sh <slug> [domain] [jurisdiction] [dbclass]
#   domain       default: <slug>.acmetocasino.com
#   jurisdiction default: demo   (br | mga | ukgc | demo)
#   dbclass      default: standard
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TPL_DIR="$(cd "${SCRIPT_DIR}/../manifests" && pwd)"

slug="${1:-}"
domain="${2:-}"
jurisdiction="${3:-demo}"
dbclass="${4:-standard}"

# --- validate slug: 3-40 chars, kebab-case, no leading/trailing hyphen ---
if [[ ! ${slug} =~ ^[a-z0-9][a-z0-9-]{1,38}[a-z0-9]$ ]]; then
    printf 'ERROR: invalid slug %q — must be 3-40 chars, lowercase letters/digits/hyphens.\n' "${slug}" >&2
    exit 1
fi
[ -n "${domain}" ] || domain="${slug}.acmetocasino.com"

# --- jurisdiction -> reporting ConfigMap key the gate checks ---
case "${jurisdiction}" in
    br)   reporting_key="sigap_enabled" ;;
    mga)  reporting_key="mga_reporting_enabled" ;;
    ukgc) reporting_key="ukgc_reporting_enabled" ;;
    demo) reporting_key="sandbox_reporting_enabled" ;;
    *)
        printf 'ERROR: unknown jurisdiction %q (expected br|mga|ukgc|demo).\n' "${jurisdiction}" >&2
        exit 1
        ;;
esac

out_dir="${TPL_DIR}/rendered/${slug}"
mkdir -p "${out_dir}"

render() {
    # render <template-basename> <output-basename>
    local tpl="${TPL_DIR}/$1" out="${out_dir}/$2"
    sed -e "s/__SLUG__/${slug}/g" \
        -e "s/__DOMAIN__/${domain}/g" \
        -e "s/__JURISDICTION__/${jurisdiction}/g" \
        -e "s/__DBCLASS__/${dbclass}/g" \
        -e "s/__REPORTING_KEY__/${reporting_key}/g" \
        "${tpl}" > "${out}"
    printf 'rendered %s\n' "${out#"${TPL_DIR}/"}"
}

render cnpg-cluster.yaml        "cnpg-cluster-${slug}.yaml"
render certificate.yaml         "certificate-${slug}.yaml"
render db-migrate-cronjob.yaml  "db-migrate-cronjob.yaml"
render compliance.yaml          "compliance.yaml"

printf 'Rendered per-tenant manifests for %q (jurisdiction=%s) into %s\n' \
    "${slug}" "${jurisdiction}" "${out_dir}"
