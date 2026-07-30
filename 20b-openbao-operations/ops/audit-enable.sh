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

# Verify that declarative audit devices are active in the sandbox.
#
# Important context for this script: OpenBao 2.5+ removed the ability to
# enable audit devices via the HTTP API. Attempting
#   bao audit enable file file_path=...
# returns:
#   cannot enable audit device via API; use declarative, config-based audit
#   device management instead
#
# The sandbox's config.hcl declares a file audit device under the
# file_audit/ path; this script simply confirms that the device is listed
# and exits non-zero if it is not. For the defence-in-depth pattern with
# both file and syslog audits, add a second `audit "syslog" "syslog_audit"`
# stanza to the config file and restart OpenBao.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../lib/common.sh
source "$SCRIPT_DIR/../lib/common.sh"

ensure_sandbox
load_root_token

log "active audit devices:"
"$BAO_BIN" audit list

# Parse the list into a machine-readable form and verify file_audit is present
devices=$("$BAO_BIN" audit list -format=json 2>/dev/null || echo '{}')
if printf '%s' "$devices" | python3 -c '
import json, sys
try:
    data = json.load(sys.stdin)
except json.JSONDecodeError:
    sys.exit(2)
paths = [k for k in data.keys() if k.endswith("/")]
if not paths:
    sys.exit(1)
print("enabled paths:", ", ".join(paths))
'; then
  log "audit devices present -- OK"
else
  die "no audit devices enabled; check the audit stanza in $SANDBOX_DIR/config.hcl and restart the sandbox"
fi
