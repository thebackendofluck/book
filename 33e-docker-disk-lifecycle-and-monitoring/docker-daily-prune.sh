#!/usr/bin/env bash
# Companion code for "The Backend of Luck" - Chapter 33e, Docker Disk Lifecycle, Truncation, and the Anatomy of a Disk.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# Daily Docker maintenance: image + system + volume prune.
# Safe defaults: --filter "until=168h" preserves anything used in the last week.
# Volume prune NEVER uses --all (named volumes attached to compose stacks are protected).
set -euo pipefail

LOG=/var/log/docker-daily-prune.log
mark() { printf '\n=== %s @ %s ===\n' "$1" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" >> "$LOG"; }
run()  { echo "RUN: $*" >> "$LOG"; "$@" >> "$LOG" 2>&1 || echo "FAIL($?): $*" >> "$LOG"; }

mark "BEFORE"; df -h / >> "$LOG"

if ! docker info >/dev/null 2>&1; then
    echo "docker daemon down — skipping prune cycle" >> "$LOG"
    exit 0
fi

docker system df >> "$LOG" 2>&1

mark "image-prune-168h"
run docker image prune -a --filter "until=168h" -f

mark "system-prune-168h"
run docker system prune --filter "until=168h" -f

mark "volume-prune-dangling-only"
run docker volume prune -f

mark "container-log-truncate-5g"
find /var/lib/docker/containers /data-pool/docker/containers \
     -name '*-json.log' -size +5G 2>/dev/null | \
  while read -r f; do
    echo "TRUNCATE: $f" >> "$LOG"
    truncate -s 0 "$f" 2>>"$LOG" || true
  done

mark "AFTER"
docker system df >> "$LOG" 2>&1
df -h / >> "$LOG"

USAGE=$(df / | awk 'NR==2 {print $5}' | tr -d '%')
if [[ "$USAGE" -gt 85 ]]; then
    logger -p local0.warning -t docker-daily-prune "DISK STILL HIGH: $USAGE% on $(hostname)"
fi
