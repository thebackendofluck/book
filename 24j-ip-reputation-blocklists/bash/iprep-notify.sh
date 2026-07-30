#!/bin/bash
# Companion code for "The Backend of Luck" - Chapter 24j, IP Reputation and Blocklist Integration for iGaming Platform.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# /usr/local/bin/iprep-notify.sh
# Post iprep update summary after iprep-update.py completes.

set -euo pipefail

METADATA_FILE="/etc/suricata/iprep/reputation.meta.json"
SLACK_WEBHOOK="${IPREP_SLACK_WEBHOOK:-}"
# Under systemd this directory comes from LogsDirectory=iprep in
# iprep-update.service, which is what makes it writable despite
# ProtectSystem=strict. The mkdir covers manual runs outside the unit.
AUDIT_LOG="${IPREP_AUDIT_LOG:-/var/log/iprep/audit.log}"

if [[ ! -f "$METADATA_FILE" ]]; then
    echo "No metadata file found at $METADATA_FILE — skipping notification"
    exit 0
fi

mkdir -p "$(dirname "$AUDIT_LOG")"

# Parse metadata
# shellcheck disable=SC2034  # expanded by the shell inside the PYTHON heredoc below
UPDATED_AT=$(python3 -c "import json,sys; d=json.load(open('$METADATA_FILE')); print(d['updated_at'])")
ENTRY_COUNT=$(python3 -c "import json,sys; d=json.load(open('$METADATA_FILE')); print(d['merged_entry_count'])")
SHA256=$(python3 -c "import json,sys; d=json.load(open('$METADATA_FILE')); print(d['reputation_file_sha256'][:12])")
# Per-category line counts. A category missing here is a rule that cannot
# match, which is the failure mode worth catching in the audit trail.
CATEGORIES=$(python3 -c "import json,sys; d=json.load(open('$METADATA_FILE')); c=d.get('category_counts',{}); print(' '.join(f'{k}={v}' for k,v in c.items()) or 'none')")

# Write to audit log
echo "$(date -u '+%Y-%m-%dT%H:%M:%SZ') iprep-update completed: ${ENTRY_COUNT} entries, sha256=${SHA256}, categories: ${CATEGORIES}" >> "$AUDIT_LOG"

# Post to Slack if webhook is configured
if [[ -n "$SLACK_WEBHOOK" ]]; then
    python3 - <<PYTHON
import json, urllib.request

payload = {
    "text": (
        f":shield: *iprep-update completed* ({UPDATED_AT})\n"
        f"Entries deployed: *{int('$ENTRY_COUNT'):,}*\n"
        f"Reputation file SHA256: \`$SHA256...\`"
    )
}
req = urllib.request.Request(
    "$SLACK_WEBHOOK",
    data=json.dumps(payload).encode(),
    headers={"Content-Type": "application/json"}
)
urllib.request.urlopen(req, timeout=10)
PYTHON
fi
