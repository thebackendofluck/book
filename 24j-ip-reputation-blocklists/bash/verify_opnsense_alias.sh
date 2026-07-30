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

# Verify OPNsense alias population after Data-Shield blocklist update.
# Run on OPNsense shell (SSH to firewall).

# List populated alias tables and entry counts
pfctl -a 'OPNsense/BlockAlias' -t DataShield_Blocklist_Recommended -Ts | wc -l

# Check a specific IP against the alias table
pfctl -a 'OPNsense/BlockAlias' -t DataShield_Blocklist_Recommended -T test 185.220.101.1

# Force alias refresh (before waiting for scheduled update)
# Via OPNsense web UI: Firewall > Diagnostics > Aliases > select alias > Update
