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

# Debug a game provider integration using mitmproxy as a recording reverse proxy
# Section 8.1 — Debugging a Game Provider Integration: Slot API Returning Unexpected RTP

# Step 1: Start the full Compose stack
docker compose -f docker-compose.dev.yml up -d

# Step 2: Replace the game-provider-mock with mitmproxy to record real traffic
# This assumes you have a dev account with the live provider's sandbox
docker run --rm -d \
    --network casino_default \
    --name game-provider-proxy \
    -p 8089:8080 \
    -p 8081:8081 \
    mitmproxy/mitmproxy \
    mitmweb \
        --web-host 0.0.0.0 \
        --web-port 8081 \
        --listen-host 0.0.0.0 \
        --listen-port 8080 \
        --mode reverse:https://sandbox.provider.com

# Step 3: Point game-engine at the proxy instead of the mock
docker compose -f docker-compose.dev.yml exec game-engine \
    sh -c 'GAME_PROVIDER_URL=http://game-provider-proxy:8080 uvicorn app:app --host 0.0.0.0 --port 8002 --log-level debug'

# Step 4: Trigger a spin via the game-engine API
curl -s -X POST http://localhost:8002/api/v1/game/spin \
    -H "Content-Type: application/json" \
    -d '{
        "player_id": "test-player-001",
        "game_id": "provider-slot-xyz",
        "bet_amount": 100,
        "currency": "EUR"
    }' | python3 -m json.tool

# Step 5: Review the captured request/response in mitmproxy UI at http://localhost:8081
# Look for:
# - The exact JSON payload sent to the provider
# - The raw response including any fields game-engine might be ignoring
# - Response time (to distinguish network latency from calculation errors)

# Step 6: If the response contains unexpected RTP field names or structure,
# update the provider response model in game-engine and hot-reload
# (uvicorn --reload picks up the change in ~550ms)
vim game-engine/app/models/provider_response.py
# The change is hot-reloaded automatically — re-trigger the spin
