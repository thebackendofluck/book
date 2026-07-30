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

# Update the HackerOne program scope on release tag.
# Requires: HACKERONE_API_TOKEN, HACKERONE_PROGRAM_HANDLE.
set -euo pipefail

: "${HACKERONE_API_TOKEN:?HACKERONE_API_TOKEN is required}"
: "${HACKERONE_PROGRAM_HANDLE:?HACKERONE_PROGRAM_HANDLE is required}"

RELEASE_TAG="${CI_COMMIT_TAG:-${GITHUB_REF_NAME:-unknown}}"

# Notify the program that a new release is live -- researchers use this as a
# signal to re-test. HackerOne does not auto-launch tests; this is metadata.
curl --fail --silent --show-error \
    -X POST "https://api.hackerone.com/v1/programs/${HACKERONE_PROGRAM_HANDLE}/updates" \
    -u "api:${HACKERONE_API_TOKEN}" \
    -H "Accept: application/json" \
    -H "Content-Type: application/json" \
    -d "{
          \"data\": {
            \"type\": \"program-update\",
            \"attributes\": {
              \"title\": \"Release ${RELEASE_TAG} deployed to production\",
              \"message\": \"New release available for testing. Scope and bounty rules unchanged.\"
            }
          }
        }"
