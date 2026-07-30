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

# chaos_test.sh — Weekly chaos testing for cluster switchover resilience

CHAOS_SCENARIO="${1:?Usage: $0 <scenario>}"
NEW_COLOR="${2:?}"

case "$CHAOS_SCENARIO" in
    kill-green-mid-switch)
        # Start switchover, then kill the new cluster's API server mid-flight
        echo "Starting switchover..."
        /opt/casino/scripts/switchover.sh blue &
        SWITCH_PID=$!
        sleep 5  # Let it get past pre-flight but before HAProxy switch

        echo "Killing Green API server..."
        ssh root@10.0.10.20 "systemctl stop k3s"

        wait $SWITCH_PID
        echo "Switchover exit code: $?"
        echo "HAProxy active backend:"
        echo "show stat" | socat stdio /run/haproxy/admin.sock | grep "casino_active," | awk -F, '{print $1,$2,$18}'
        ;;

    kill-redis-during-switch)
        # Simulate Redis going down exactly at switchover moment
        echo "Starting switchover in background..."
        /opt/casino/scripts/switchover.sh blue &
        sleep 8  # Time the Redis kill for the moment Green is being validated

        echo "Killing Redis..."
        ssh root@10.0.10.101 "systemctl stop redis"
        sleep 15
        echo "Restoring Redis..."
        ssh root@10.0.10.101 "systemctl start redis"

        wait
        echo "Checking player session recovery..."
        ;;

    split-brain-test)
        # Manually set HAProxy to route to both clusters simultaneously
        # Verify that PostgreSQL advisory lock prevents dual writes
        echo "WARNING: This test intentionally creates split-brain. Run only in test env."
        read -p "Confirm test environment (type YES): " confirm
        [[ "$confirm" == "YES" ]] || exit 1

        echo "set weight casino_active/blue 100"  | socat stdio /run/haproxy/admin.sock
        echo "set weight casino_active/green 100" | socat stdio /run/haproxy/admin.sock

        echo "Both backends active. Running transaction test to check for duplicate writes..."
        python3 /opt/casino/tests/dual_write_detector.py

        # Restore
        echo "set weight casino_active/green 0" | socat stdio /run/haproxy/admin.sock
        ;;

    *)
        echo "Unknown chaos scenario: $CHAOS_SCENARIO"
        exit 1
        ;;
esac
