#!/usr/bin/env bash
# Companion code for "The Backend of Luck" - Chapter 23b, DevSecOps Pipeline Implementation.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# Kick off a Cobalt pentest engagement from CI on release tag.
# Requires: COBALT_API_TOKEN, COBALT_ASSET_ID.
set -euo pipefail

: "${COBALT_API_TOKEN:?COBALT_API_TOKEN is required}"
: "${COBALT_ASSET_ID:?COBALT_ASSET_ID is required}"

RELEASE_TAG="${CI_COMMIT_TAG:-${GITHUB_REF_NAME:-unknown}}"

curl --fail --silent --show-error \
    -X POST "https://api.cobalt.io/pentests" \
    -H "Authorization: Bearer ${COBALT_API_TOKEN}" \
    -H "X-Org-Token: required" \
    -H "Content-Type: application/vnd.api+json" \
    -d "{
          \"data\": {
            \"type\": \"pentest\",
            \"attributes\": {
              \"title\": \"Casino platform release ${RELEASE_TAG}\",
              \"objectives\": \"Post-release verification pentest\",
              \"type\": \"agile\",
              \"methodology\": \"web\"
            },
            \"relationships\": {
              \"asset\": { \"data\": { \"type\": \"asset\", \"id\": \"${COBALT_ASSET_ID}\" } }
            }
          }
        }"
