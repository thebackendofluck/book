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

# Skaffold commands for Kubernetes inner-loop development
# Section 3.3 — Skaffold Configuration

# Start dev mode with file sync
skaffold dev --namespace casino-dev

# One-shot deploy (useful for integration testing a branch)
skaffold run --namespace casino-dev

# Deploy against CI profile to internal registry
skaffold run --profile ci

# Tail logs for a specific service while iterating
skaffold dev --namespace casino-dev --tail
