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

# Attach an ephemeral debug container to a running production pod
# Section 5.2 — Attaching to a Running Production Pod
# admin@ops-server (or from a machine with kubeconfig pointing to prod)

# Find the pod you want to debug
kubectl get pods -n casino-prod -l app=wallet-service
# NAME                              READY   STATUS    RESTARTS   AGE
# wallet-service-7d8b9f4c6-xk2pq   1/1     Running   0          2d
# wallet-service-7d8b9f4c6-mn9rz   1/1     Running   0          2d

# Attach an ephemeral container — shares network namespace with the target pod
kubectl debug wallet-service-7d8b9f4c6-xk2pq \
    --namespace casino-prod \
    --image registry.ops-host.local:5000/casino/debug-tools:latest \
    --image-pull-policy IfNotPresent \
    --target wallet-service \
    -it \
    -- bash

# You are now inside the ephemeral container, in the same network namespace
# The wallet-service process is visible in /proc

# Check what the service is actually listening on
ss -tlnp

# Check outbound connections to the payment gateway
ss -tnp | grep 443

# See the process tree of the wallet-service container
ps aux

# Attach to the running Python process with pdb (requires --target flag above)
# Find the uvicorn PID
PID=$(pgrep -f "uvicorn")
echo "uvicorn PID: $PID"
