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

# Build and push the debug tools image to the internal registry
# Section 5.1 — The Debug Image
# Run from CI (Chapter 23's pipeline) on every merge to main — never ad-hoc.

docker build -t registry.ops-host.local:5000/casino/debug-tools:latest \
    -f Dockerfile.debug .
docker push registry.ops-host.local:5000/casino/debug-tools:latest
