#!/bin/bash
# Companion code for "The Backend of Luck" - Chapter 27, Data Residency and Backup/Recovery.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# Refresh backup-status.json from wasabi-backup.log
# Runs every 15 minutes via cron or systemd timer
set -euo pipefail

LOG="/var/log/wasabi-backup.log"
STATUS_JSON="/var/www/acmetocasino.com/api-data/backup-status.json"

mkdir -p "$(dirname "${STATUS_JSON}")"

# If backup-status.json was written by the backup script, it's already fresh.
# This script fills in the "days_since_restore_test" from log analysis.

if [ ! -f "${LOG}" ]; then
    cat > "${STATUS_JSON}" << 'JSON'
{
  "last_backup": null,
  "last_backup_status": "never_run",
  "last_restore_test": null,
  "days_since_restore_test": null
}
JSON
    exit 0
fi

# Extract last backup timestamp (used indirectly through backup-status.json written by backup script)
_LAST_BACKUP_LINE=$(grep '=== Backup complete' "${LOG}" 2>/dev/null | tail -1 || echo "")
: "${_LAST_BACKUP_LINE}"  # suppress unused warning

# Extract last restore timestamp
LAST_RESTORE=$(grep '=== Restore summary' "${LOG}" 2>/dev/null | tail -1 | grep -oP '^\[([^\]]+)\]' | tr -d '[]' || echo "")

# Calculate days since restore
DAYS_SINCE_RESTORE="null"
if [ -n "${LAST_RESTORE}" ]; then
    RESTORE_TS=$(date -d "${LAST_RESTORE}" +%s 2>/dev/null || echo "")
    if [ -n "${RESTORE_TS}" ]; then
        NOW_TS=$(date +%s)
        DAYS_SINCE_RESTORE=$(( (NOW_TS - RESTORE_TS) / 86400 ))
    fi
fi

# Read existing status JSON for fields written by backup script
if [ -f "${STATUS_JSON}" ]; then
    # Merge: add days_since_restore_test
    python3 - "${STATUS_JSON}" "${LAST_RESTORE}" "${DAYS_SINCE_RESTORE}" << 'PYEOF'
import json, sys
path = sys.argv[1]
last_restore = sys.argv[2] if sys.argv[2] else None
days = int(sys.argv[3]) if sys.argv[3] != "null" else None
try:
    d = json.load(open(path))
except Exception:
    d = {}
d["last_restore_test"] = last_restore
d["days_since_restore_test"] = days
with open(path + ".tmp", "w") as f:
    json.dump(d, f, indent=2)
import os
os.rename(path + ".tmp", path)
PYEOF
fi
