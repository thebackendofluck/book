#!/usr/bin/env bash
# Companion code for "The Backend of Luck" - Chapter 22b, Developer Inner-Loop Experience in Containerized iGaming Pla.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# Investigate a bonus calculation discrepancy using Telepresence intercept
# Section 8.4 — Investigating a Bonus Calculation Discrepancy Mid-Promotion

# Connect Telepresence to staging
telepresence connect --kubeconfig ~/.kube/ops-host-staging-config

# Intercept bonus-engine — all bonus calculation requests route to your laptop
telepresence intercept bonus-engine \
    --namespace casino-staging \
    --port 8003:8003 \
    --env-file /tmp/bonus-engine-staging-env.txt

# Source staging environment (real Redis config, real database connection)
source /tmp/bonus-engine-staging-env.txt

# Start bonus-engine locally with a Python debugger break on the promotion calc
# Add a breakpoint to bonus-engine/app/services/promotions.py line 142
# (the deposit_match_calculation function)
cd bonus-engine
python3 -c "
import debugpy
debugpy.listen(5678)
debugpy.wait_for_client()  # Will block until VS Code attaches
print('debugpy: waiting for debugger attach on port 5678...')
" &

uvicorn app:app --host 0.0.0.0 --port 8003 --reload

# In VS Code: Run → Attach to Python Debugger → localhost:5678
# Set breakpoint in promotions.py at the deposit_match_calculation function

# Trigger a test bonus calculation via the staging player portal
# or directly via the API
curl -s -X POST http://localhost:8003/api/v1/bonus/calculate \
    -H "Content-Type: application/json" \
    -d '{
        "player_id": "staging-player-with-discrepancy-001",
        "promotion_id": "deposit-match-superbowl-2026",
        "deposit_amount": 150.00,
        "currency": "EUR"
    }'

# Debugger breaks at the calculation function
# Inspect: player.tier, promotion.match_percentage, promotion.tier_overrides
# Found: the tier_overrides dict used player.legacy_tier (string) instead of
#        player.tier_id (integer) — they matched for most players but not for
#        players migrated from the old platform who had non-standard legacy tier names

# Fix: bonus-engine/app/services/promotions.py line 142
# Change: match_pct = promotion.tier_overrides.get(player.legacy_tier, default_pct)
# To:     match_pct = promotion.tier_overrides.get(str(player.tier_id), default_pct)

# uvicorn hot-reloads, re-run the curl — verify fix

# Stop intercept
telepresence leave bonus-engine
