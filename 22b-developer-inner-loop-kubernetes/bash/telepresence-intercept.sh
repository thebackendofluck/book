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

# Telepresence intercept: route cluster traffic to local process
# Section 3.4 — Telepresence Intercept

# Install Telepresence
brew install datawire/blackbird/telepresence

# Connect to the cluster — this installs the Traffic Manager if not already present
telepresence connect --kubeconfig ~/.kube/ops-host-config

# Verify connectivity — should be able to reach cluster services by DNS
curl http://player-service.casino-staging.svc.cluster.local:8000/health

# Intercept wallet-service — all traffic to wallet-service:8001 routes to localhost:8001
telepresence intercept wallet-service \
  --namespace casino-staging \
  --port 8001:8001 \
  --env-file /tmp/wallet-service-env.txt   # Pulls K8s environment variables

# The env file contains the real database URLs, Redis config, etc.
# Source it and start the local service against the live cluster dependencies
source /tmp/wallet-service-env.txt
cd wallet-service
uvicorn app:app --host 0.0.0.0 --port 8001 --reload

# Now: edit wallet-service/app/services/balance.py
# Changes are live instantly — uvicorn reloads in ~550ms
# Traffic from the cluster routes to your local process

# When done, stop the intercept
telepresence leave wallet-service

# Disconnect
telepresence quit
