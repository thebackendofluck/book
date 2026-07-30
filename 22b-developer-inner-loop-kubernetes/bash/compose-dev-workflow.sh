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

# docker-compose.dev.yml hot-reload workflow
# Section 2.4 — Starting and iterating

# First time — build all dev images
docker compose -f docker-compose.dev.yml build

# Start everything
docker compose -f docker-compose.dev.yml up

# Now edit wallet-service/app/services/balance.py — uvicorn reloads in ~550ms
# Watch the reload in the wallet-service log stream
docker compose -f docker-compose.dev.yml logs -f wallet-service

# Adding a new dependency to wallet-service
echo "httpx==0.27.0" >> wallet-service/requirements.txt
# Must rebuild — no way around this
docker compose -f docker-compose.dev.yml build wallet-service
docker compose -f docker-compose.dev.yml up -d --no-deps wallet-service

# Run a quick test against the hot-reloaded service
curl -s -X POST http://localhost:8001/api/v1/balance/credit \
  -H "Content-Type: application/json" \
  -d '{"player_id": "test-001", "amount": 100.00, "currency": "BRL", "reference": "test-txn-001"}' \
  | python3 -m json.tool
