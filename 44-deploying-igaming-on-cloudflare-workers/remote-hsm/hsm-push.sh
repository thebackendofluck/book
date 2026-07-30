#!/bin/bash
# Companion code for "The Backend of Luck" - Chapter 44, Deploying iGaming Platforms on Cloudflare Workers.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

TMPFILE=$(mktemp)
/usr/bin/python3 /opt/hsm-poller.py > "$TMPFILE" 2>/dev/null
scp -q -i /root/.ssh/hsm-poller -o StrictHostKeyChecking=no -o ConnectTimeout=5 "$TMPFILE" root@203.0.113.1:/tmp/hsm-status.json 2>/dev/null
ssh -i /root/.ssh/hsm-poller -o StrictHostKeyChecking=no -o ConnectTimeout=5 root@203.0.113.1 "cat /tmp/hsm-status.json | docker exec -i new-casino-redis redis-cli -x SET hsm:status && docker exec new-casino-redis redis-cli EXPIRE hsm:status 600" > /dev/null 2>&1
rm -f "$TMPFILE"
