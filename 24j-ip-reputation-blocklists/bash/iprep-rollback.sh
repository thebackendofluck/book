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

# /usr/local/bin/iprep-rollback.sh
# Roll back to the previous reputation file version.

set -euo pipefail

IPREP_DIR="/etc/suricata/iprep"
CURRENT="$IPREP_DIR/reputation.list"
BACKUP_1="$IPREP_DIR/reputation.list.bak"
BACKUP_2="$IPREP_DIR/reputation.list.bak.2"

if [[ ! -f "$BACKUP_1" ]]; then
    echo "ERROR: No backup file found at $BACKUP_1" >&2
    exit 1
fi

echo "Rolling back $CURRENT from $BACKUP_1"

# Rotate: current becomes bak.2, bak becomes current
if [[ -f "$CURRENT" ]]; then
    cp -a "$CURRENT" "$BACKUP_2"
fi
cp -a "$BACKUP_1" "$CURRENT"

echo "Rolled back. Reloading Suricata..."
suricatasc -c reload-rules || pkill -USR2 suricata

echo "Rollback complete. Current entry count:"
grep -v '^#' "$CURRENT" | grep -c '.' || echo 0
