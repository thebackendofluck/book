#!/usr/bin/env bash
# Companion code for "The Backend of Luck" - Chapter 23, DevSecOps for iGaming.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

set -euo pipefail

OUT_DIR="${OUT_DIR:-out/perf-baseline}"
STAMP="$(date -u '+%Y%m%dT%H%M%SZ')"
HOST="$(hostname)"
RUN_DIR="${OUT_DIR}/${HOST}-${STAMP}"

mkdir -p "$RUN_DIR"
uptime > "${RUN_DIR}/uptime.txt"
ps -eo pid,ppid,comm,%cpu,%mem,rss --sort=-%cpu | head -50 > "${RUN_DIR}/top-cpu.txt"
ps -eo pid,ppid,comm,%cpu,%mem,rss --sort=-rss | head -50 > "${RUN_DIR}/top-rss.txt"

for pressure in cpu io memory; do
  if [[ -r "/proc/pressure/${pressure}" ]]; then
    cp "/proc/pressure/${pressure}" "${RUN_DIR}/pressure-${pressure}.txt"
  fi
done

if command -v docker >/dev/null 2>&1; then
  docker stats --no-stream > "${RUN_DIR}/docker-stats.txt" || true
fi

if command -v iostat >/dev/null 2>&1; then
  iostat -x 1 5 > "${RUN_DIR}/iostat.txt" || true
fi

echo "$RUN_DIR"
