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

# Trigger an Intruder scheduled scan on release.
# Requires: INTRUDER_API_KEY, INTRUDER_TARGET_GROUP_ID.
set -euo pipefail

: "${INTRUDER_API_KEY:?INTRUDER_API_KEY is required}"
: "${INTRUDER_TARGET_GROUP_ID:?INTRUDER_TARGET_GROUP_ID is required}"

curl --fail --silent --show-error \
    -X POST "https://api.intruder.io/v1/scans" \
    -H "Authorization: Token ${INTRUDER_API_KEY}" \
    -H "Content-Type: application/json" \
    -d "{
          \"target_group_id\": ${INTRUDER_TARGET_GROUP_ID},
          \"scan_type\": \"new_threat_scan\"
        }"
