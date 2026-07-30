#!/usr/bin/env bash
# Companion code for "The Backend of Luck" - Chapter 24i, Blue-Green Cluster Switching for iGaming Kubernetes Environm.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# push_rotation_metrics.sh — pushes metrics to Prometheus Pushgateway after each phase

PUSHGATEWAY="http://prometheus-pushgateway.casino.internal:9091"
PHASE="${1:?Usage: $0 <phase> <status> <duration_seconds>}"
STATUS="${2:?}"  # success | failure
DURATION="${3:-0}"

cat <<EOF | curl -sf --data-binary @- "${PUSHGATEWAY}/metrics/job/casino_rotation/instance/${PHASE}"
# HELP casino_rotation_phase_status Last rotation phase status (1=success, 0=failure)
# TYPE casino_rotation_phase_status gauge
casino_rotation_phase_status{phase="${PHASE}",color="$(cat /etc/casino/rotation-state.json | jq -r .active_color)"} $([ "$STATUS" == "success" ] && echo 1 || echo 0)
# HELP casino_rotation_phase_duration_seconds Duration of last rotation phase
# TYPE casino_rotation_phase_duration_seconds gauge
casino_rotation_phase_duration_seconds{phase="${PHASE}"} ${DURATION}
# HELP casino_rotation_phase_timestamp_seconds Unix timestamp of last rotation phase
# TYPE casino_rotation_phase_timestamp_seconds gauge
casino_rotation_phase_timestamp_seconds{phase="${PHASE}"} $(date +%s)
EOF
