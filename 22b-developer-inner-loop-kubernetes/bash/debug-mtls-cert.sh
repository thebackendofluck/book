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

# Diagnose mTLS certificate failures without restarting the pod
# Section 5.4 — Debugging a Certificate Validation Failure Without Restarting
# Run from inside an ephemeral container (--target wallet-service) on the wallet-service pod.

# Check the certificate that wallet-service is presenting
# The cert is mounted at /etc/certs/ based on Chapter 24h's cert-manager setup
openssl x509 -in /proc/$(pgrep -f uvicorn)/root/etc/certs/tls.crt \
    -text -noout | grep -E "Subject:|Issuer:|Not After:|DNS:"

# Test the mTLS handshake manually from inside the pod network namespace
openssl s_client \
    -connect compliance-reporter.casino-prod.svc.cluster.local:8004 \
    -cert /proc/$(pgrep -f uvicorn)/root/etc/certs/tls.crt \
    -key /proc/$(pgrep -f uvicorn)/root/etc/certs/tls.key \
    -CAfile /proc/$(pgrep -f uvicorn)/root/etc/certs/ca.crt \
    -verify_return_error \
    -brief

# If the handshake succeeds but the service still rejects — check the CN/SAN
# compliance-reporter may be validating the client certificate CN against a whitelist
openssl x509 -in /proc/$(pgrep -f uvicorn)/root/etc/certs/tls.crt \
    -noout -subject -issuer

# Check certificate expiry — the most common cause of intermittent mTLS failures
openssl x509 -in /proc/$(pgrep -f uvicorn)/root/etc/certs/tls.crt \
    -noout -enddate
# notAfter=Apr 15 12:00:00 2026 GMT
