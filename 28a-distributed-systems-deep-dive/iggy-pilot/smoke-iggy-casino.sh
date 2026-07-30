#!/usr/bin/env bash
# Companion code for "The Backend of Luck" - Chapter 28a, Distributed Systems Deep Dive.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

set -euo pipefail

BASE_URL="${IGGY_BASE_URL:-http://localhost:3000}"
USERNAME="${IGGY_ROOT_USERNAME:?Set IGGY_ROOT_USERNAME}"
PASSWORD="${IGGY_ROOT_PASSWORD:?Set IGGY_ROOT_PASSWORD}"
STREAM_NAME="${IGGY_STREAM_NAME:-casino-ops}"
TOPIC_NAME="${IGGY_TOPIC_NAME:-casino-events}"
PARTITIONS="${IGGY_TOPIC_PARTITIONS:-1}"

curl_json() {
  curl -fsS "$@" -H "content-type: application/json"
}

TOKEN="$(
  curl_json -X POST "${BASE_URL}/users/login" \
    -d "{\"username\":\"${USERNAME}\",\"password\":\"${PASSWORD}\"}" |
    jq -r '.token // .access_token // .jwt // empty'
)"

if [ -z "${TOKEN}" ]; then
  echo "login succeeded but no token was returned" >&2
  exit 1
fi

AUTH_HEADER=(-H "authorization: Bearer ${TOKEN}")

STREAM_ID="$(
  curl_json "${AUTH_HEADER[@]}" "${BASE_URL}/streams" |
    jq -r --arg name "${STREAM_NAME}" '.[]? | select(.name == $name) | .id' |
    head -n 1
)"

if [ -z "${STREAM_ID}" ]; then
  STREAM_ID="$(
    curl_json "${AUTH_HEADER[@]}" -X POST "${BASE_URL}/streams" \
      -d "{\"name\":\"${STREAM_NAME}\"}" |
      jq -r '.id'
  )"
fi

TOPIC_ID="$(
  curl_json "${AUTH_HEADER[@]}" "${BASE_URL}/streams/${STREAM_ID}/topics" |
    jq -r --arg name "${TOPIC_NAME}" '.[]? | select(.name == $name) | .id' |
    head -n 1
)"

if [ -z "${TOPIC_ID}" ]; then
  TOPIC_ID="$(
    curl_json "${AUTH_HEADER[@]}" -X POST "${BASE_URL}/streams/${STREAM_ID}/topics" \
      -d "{\"name\":\"${TOPIC_NAME}\",\"partitions_count\":${PARTITIONS}}" |
      jq -r '.id'
  )"
fi

EVENT_ID="evt-$(date +%s)"
PAYLOAD="$(
  jq -nc --arg event_id "${EVENT_ID}" --arg ts "$(date -u +%FT%TZ)" '{
    event_id: $event_id,
    event_type: "bet_placed",
    casino: "acmetocasino",
    player_id: "synthetic-player-001",
    game_id: "aviator",
    currency: "EUR",
    stake: 5.00,
    timestamp: $ts
  }' | base64 | tr -d '\n'
)"

curl_json "${AUTH_HEADER[@]}" -X POST "${BASE_URL}/streams/${STREAM_ID}/topics/${TOPIC_ID}/messages" \
  -d "{\"partition_id\":0,\"messages\":[{\"payload\":\"${PAYLOAD}\"}]}" >/dev/null

READBACK="$(
  curl_json "${AUTH_HEADER[@]}" \
    "${BASE_URL}/streams/${STREAM_ID}/topics/${TOPIC_ID}/messages?consumer_id=1&partition_id=0&kind=offset&value=0&count=10&auto_commit=false"
)"

echo "${READBACK}" |
  jq --arg event_id "${EVENT_ID}" '{
    stream: "'"${STREAM_NAME}"'",
    topic: "'"${TOPIC_NAME}"'",
    event_id: $event_id,
    found: ([.messages[]?.payload? | @base64d | fromjson? | select(.event_id == $event_id)] | length) > 0
  }'
