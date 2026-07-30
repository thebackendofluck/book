#!/usr/bin/env bash
# Companion code for "The Backend of Luck" - Chapter 24i, Blue-Green Cluster Switching for iGaming Kubernetes Environm.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# dns_switchover.sh — Update Route 53 to point to new cluster's LB IP
set -euo pipefail

HOSTED_ZONE_ID="${ROUTE53_ZONE_ID:?}"
HOSTNAME="${1:?Usage: $0 <hostname> <new_ip>}"
NEW_IP="${2:?Usage: $0 <hostname> <new_ip>}"

aws route53 change-resource-record-sets \
    --hosted-zone-id "$HOSTED_ZONE_ID" \
    --change-batch "$(cat <<EOF
{
    "Changes": [{
        "Action": "UPSERT",
        "ResourceRecordSet": {
            "Name": "${HOSTNAME}",
            "Type": "A",
            "TTL": 30,
            "ResourceRecords": [{"Value": "${NEW_IP}"}]
        }
    }]
}
EOF
)"

echo "DNS updated: ${HOSTNAME} → ${NEW_IP}"
echo "Wait 30s for TTL propagation before expecting all clients to resolve to new IP."
