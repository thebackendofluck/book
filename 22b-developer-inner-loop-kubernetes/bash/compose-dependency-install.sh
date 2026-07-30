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

# Install a Python dependency into a running dev container without rebuilding
# Section 2.5 — Handling Python Dependency Changes
# Only acceptable for short-lived dev sessions. Always update requirements.txt.

# Install without rebuilding — only acceptable for short-lived dev sessions
docker compose -f docker-compose.dev.yml exec wallet-service \
  pip install httpx==0.27.0

# Immediately add to requirements.txt so the next `build` picks it up
echo "httpx==0.27.0" >> wallet-service/requirements.txt

# Verify inside the container
docker compose -f docker-compose.dev.yml exec wallet-service \
  pip show httpx
