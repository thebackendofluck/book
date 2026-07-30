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

# GLI-13 v3.0 (2024) — Monitoring & Control System (MCS) connector liveness check.
#
# Verifies that the connector publishing gaming events to the regulator's MCS is:
#   1. Reachable (TCP+TLS).
#   2. Authenticated (mTLS handshake completes; no anonymous fallback).
#   3. Time-synchronised (drift vs MCS clock < 1s).
#   4. Actively flushing events (last_event_ts is recent).
#
# Designed to run on a cron (every 60s in production). Failure pages the on-call
# compliance engineer — a stalled MCS connector is a Notifiable Event in most
# jurisdictions (SPA/SIGAP in Brazil, MGCB CIDS in Michigan, KSA in NL).
#
# Exit codes:
#   0  all checks pass
#   1  reachability or authentication failure
#   2  time drift exceeds threshold
#   3  no events flushed in the last $STALL_WINDOW_SEC
#   4  configuration error
#
# Usage:
#   MCS_HOST=mcs.example.gov \
#   MCS_PORT=443 \
#   MCS_CLIENT_CERT=/etc/mcs/client.crt \
#   MCS_CLIENT_KEY=/etc/mcs/client.key \
#   MCS_CA_BUNDLE=/etc/mcs/ca-bundle.pem \
#   STALL_WINDOW_SEC=300 \
#       ./mcs-connector-test.sh

set -euo pipefail

: "${MCS_HOST:?MCS_HOST required}"
: "${MCS_PORT:=443}"
: "${MCS_CLIENT_CERT:?MCS_CLIENT_CERT required}"
: "${MCS_CLIENT_KEY:?MCS_CLIENT_KEY required}"
: "${MCS_CA_BUNDLE:?MCS_CA_BUNDLE required}"
: "${STALL_WINDOW_SEC:=300}"
: "${MCS_HEALTH_PATH:=/v1/health}"
: "${MCS_LAST_EVENT_PATH:=/v1/connector/last-event}"

for f in "$MCS_CLIENT_CERT" "$MCS_CLIENT_KEY" "$MCS_CA_BUNDLE"; do
    if [[ ! -r "$f" ]]; then
        echo "config error: cannot read $f" >&2
        exit 4
    fi
done

base="https://${MCS_HOST}:${MCS_PORT}"
curl_args=(
    --silent --show-error --fail
    --cert "$MCS_CLIENT_CERT" --key "$MCS_CLIENT_KEY"
    --cacert "$MCS_CA_BUNDLE"
    --max-time 10
)

# 1+2: reachability + mTLS auth in one shot
if ! health_body=$(curl "${curl_args[@]}" "${base}${MCS_HEALTH_PATH}"); then
    echo "FAIL: cannot reach or authenticate to ${base}${MCS_HEALTH_PATH}" >&2
    exit 1
fi

# 3: time drift — MCS exposes its own clock under .server_time_unix
local_now=$(date +%s)
mcs_now=$(echo "$health_body" | jq -r '.server_time_unix // empty')
if [[ -z "$mcs_now" ]]; then
    echo "FAIL: MCS health did not report server_time_unix" >&2
    exit 2
fi
drift=$((local_now > mcs_now ? local_now - mcs_now : mcs_now - local_now))
if (( drift > 1 )); then
    echo "FAIL: clock drift ${drift}s exceeds 1s budget (GLI-13 / GLI-11 §2.5)" >&2
    exit 2
fi

# 4: connector flush freshness
if ! flush_body=$(curl "${curl_args[@]}" "${base}${MCS_LAST_EVENT_PATH}"); then
    echo "FAIL: cannot fetch last-event status" >&2
    exit 1
fi
last_event_ts=$(echo "$flush_body" | jq -r '.last_event_ts_unix // empty')
if [[ -z "$last_event_ts" ]]; then
    echo "FAIL: last-event response missing last_event_ts_unix" >&2
    exit 3
fi
age=$((local_now - last_event_ts))
if (( age > STALL_WINDOW_SEC )); then
    echo "FAIL: connector stalled — last event ${age}s ago (window ${STALL_WINDOW_SEC}s)" >&2
    exit 3
fi

echo "OK: MCS reachable, authenticated, drift=${drift}s, last_event_age=${age}s"
exit 0
