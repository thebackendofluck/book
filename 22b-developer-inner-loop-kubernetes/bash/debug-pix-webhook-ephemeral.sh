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

# Trace a PIX payment webhook timeout using an ephemeral container
# Section 8.2 — Tracing a PIX Payment Webhook Timeout Without Restarting the Payment Service
# admin@ops-server

# Find the current wallet-service pod
WALLET_POD=$(kubectl get pod -n casino-staging \
    -l app=wallet-service \
    -o jsonpath='{.items[0].metadata.name}')
echo "Target pod: $WALLET_POD"

# Attach ephemeral debug container
kubectl debug "$WALLET_POD" \
    --namespace casino-staging \
    --image registry.ops-host.local:5000/casino/debug-tools:latest \
    --image-pull-policy IfNotPresent \
    --target wallet-service \
    -it \
    -- bash

# Inside the ephemeral container:

# 1. Check current active connections to understand baseline load
ss -tnp | grep ESTABLISHED | wc -l

# 2. Monitor the webhook endpoint in real-time
# In one terminal window, capture incoming POST requests to /webhooks/pix
tcpdump -i eth0 -A \
    'tcp dst port 8001 and (tcp[((tcp[12:1] & 0xf0) >> 2):4] = 0x504f5354)' \
    -l 2>/dev/null | grep -A5 "POST /webhooks/pix"

# 3. In another terminal window (separate kubectl exec into the same ephemeral container),
# check for TIME_WAIT accumulation (indicator of connection handling problems)
watch -n 1 'ss -tn state time-wait | wc -l'

# 4. Look at the wallet-service process's open file descriptors
# High fd count can indicate a connection pool leak
ls -la /proc/$(pgrep -f uvicorn)/fd | wc -l

# 5. Check if the webhook handler is blocking on a database call
# by watching the active database connections
psql postgresql://casino_ops:***@postgres.casino-staging.svc.cluster.local:5432/casino \
    -c "SELECT pid, state, wait_event_type, wait_event, query_start, query \
        FROM pg_stat_activity \
        WHERE datname = 'casino' \
        AND state != 'idle' \
        ORDER BY query_start;"

# What we found in the actual incident:
# The webhook handler was acquiring a database connection from a pool of 10.
# Under webhook burst (multiple PIX confirmations arriving within 1 second),
# the pool was exhausted and the handlers were waiting for a connection to free up.
# The pool wait exceeded the 5-second webhook acknowledgment timeout.
# Fix: increase database connection pool size from 10 to 50 in wallet-service config.
