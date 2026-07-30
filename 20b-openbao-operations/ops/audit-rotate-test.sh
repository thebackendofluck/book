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

# Simulate a logrotate cycle against the sandbox audit log and verify that
# OpenBao keeps writing after the rotation.
#
# OpenBao 2.5+ requires audit devices to be declared in config.hcl, so the
# classical postrotate hook (`bao audit enable ...`) no longer works. The
# correct postrotate mechanism is either:
#
#   1. `kill -SIGHUP <openbao-pid>`  -- OpenBao reopens all audit file
#      handles, picking up the new inode after the rename.
#   2. Use `copytruncate` in the logrotate stanza, which copies the file
#      contents to the rotated name and truncates the original in-place
#      (the original inode is preserved, OpenBao's file handle still
#      points at the truncated file, and the next write appends to it).
#
# This test exercises option 1 because it is cheaper for large audit logs
# (no copy).

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../lib/common.sh
source "$SCRIPT_DIR/../lib/common.sh"

ensure_sandbox
load_root_token

AUDIT_LOG="${SANDBOX_DIR}/audit/audit.log"
[[ -f "$AUDIT_LOG" ]] || die "audit log not found; start the sandbox first"
[[ -f "$SANDBOX_PID_FILE" ]] || die "sandbox pid file missing; start the sandbox first"

log "forcing an audit entry (authenticated token lookup)"
# bao status is unauthenticated and does NOT generate an audit entry.
# bao token lookup is authenticated and definitely does.
"$BAO_BIN" token lookup >/dev/null 2>&1 || true
sleep 0.2

size_before=$(stat -c%s "$AUDIT_LOG")
log "size before rotate: $size_before bytes"

log "simulating logrotate (mv -> touch -> SIGHUP)"
mv "$AUDIT_LOG" "$AUDIT_LOG.1"
touch "$AUDIT_LOG"
chmod 600 "$AUDIT_LOG"

pid=$(cat "$SANDBOX_PID_FILE")
log "postrotate: kill -SIGHUP $pid (OpenBao logs 'reloading file audit backend')"
kill -HUP "$pid"

# Give OpenBao a moment to handle the signal and reopen the file.
sleep 0.5

log "triggering another authenticated audit entry"
"$BAO_BIN" token lookup >/dev/null 2>&1 || true
sleep 0.5

size_after=$(stat -c%s "$AUDIT_LOG")
log "size after rotate + new entry: $size_after bytes"

if (( size_after > 0 )); then
  log "audit rotate test PASS (new audit.log received $size_after bytes after SIGHUP)"
else
  die "FAIL: audit log did not grow after rotation; OpenBao may not have reopened the file handle"
fi
