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

# Enable and start the iprep-update systemd timer.
# Run once after deploying the service and timer unit files.

set -euo pipefail

systemctl daemon-reload
systemctl enable --now iprep-update.timer

# Verify timer status
systemctl list-timers iprep-update.timer

# Run immediately for initial population
systemctl start iprep-update.service

# Check the update log
journalctl -u iprep-update.service --since "1 hour ago"
