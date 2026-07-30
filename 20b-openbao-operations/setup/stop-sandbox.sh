#!/usr/bin/env bash
# Companion code for "The Backend of Luck" - Chapter 20b, OpenBao Operations.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# Stop the chapter-20b OpenBao sandbox and, by default, delete its data dir.
# Pass --keep to preserve /tmp/openbao-sandbox-20b/ for inspection.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../lib/common.sh
source "$SCRIPT_DIR/../lib/common.sh"

KEEP=0
for arg in "$@"; do
  case "$arg" in
    --keep) KEEP=1 ;;
    *) die "unknown arg: $arg" ;;
  esac
done

if [[ -f "$SANDBOX_PID_FILE" ]]; then
  pid=$(cat "$SANDBOX_PID_FILE")
  if kill -0 "$pid" 2>/dev/null; then
    log "stopping sandbox (pid $pid)"
    kill "$pid"
    for _ in 1 2 3 4 5; do
      if ! kill -0 "$pid" 2>/dev/null; then break; fi
      sleep 0.5
    done
    if kill -0 "$pid" 2>/dev/null; then
      log "sandbox did not exit on SIGTERM; sending SIGKILL"
      kill -9 "$pid"
    fi
  else
    log "stale pid file; removing"
  fi
  rm -f "$SANDBOX_PID_FILE"
fi

if (( KEEP == 0 )); then
  log "removing $SANDBOX_DIR"
  rm -rf "$SANDBOX_DIR"
else
  log "keeping $SANDBOX_DIR for inspection"
fi
