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

# Network capture for payment timeout diagnosis using an ephemeral debug container
# Section 5.3 — Network Capture for Payment Timeout Diagnosis
# Run these commands from inside the ephemeral container on the wallet-service pod.

# First, identify the payment gateway IP from DNS
nslookup api.payment-gateway.com
# Server:         10.96.0.10
# Address:        10.96.0.10#53
# Name:   api.payment-gateway.com
# Address: 162.255.44.12

# Capture the TCP stream to payment gateway port 443
# Write to a file we will exfiltrate with kubectl cp
tcpdump -i eth0 \
    host 162.255.44.12 and port 443 \
    -w /tmp/payment-capture.pcap \
    -c 500    # Stop after 500 packets

# In another terminal, trigger some test transactions from the player-facing UI
# so we can capture the pattern

# Copy the capture out of the pod
kubectl cp casino-prod/wallet-service-7d8b9f4c6-xk2pq:/tmp/payment-capture.pcap \
    --container debugger-xyz \
    ./payment-capture.pcap

# Analyze with tshark (or Wireshark)
tshark -r payment-capture.pcap \
    -Y "tls.handshake" \
    -T fields \
    -e frame.time \
    -e ip.src \
    -e ip.dst \
    -e tls.handshake.type \
    -e tls.alert.desc
