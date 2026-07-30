#!/bin/bash
# Companion code for "The Backend of Luck" - Chapter 18, Real-Time Clock Module Implementation.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# rtc-monthly-check.sh
# Monthly RTC health check for iGaming casino operations.
# Verifies battery levels, NTP sync, drift metrics, and hardware diagnostics.

echo "=== RTC Monthly Health Check ==="
echo "Date: $(date)"

# Check battery levels
echo "Battery Status:"
kubectl exec rtc-pod-0 -- rtc-cli battery-status

# Verify time synchronization
echo "NTP Sync Status:"
kubectl exec rtc-pod-0 -- ntpq -p

# Check drift metrics
echo "Drift Metrics (last 24h):"
curl -s "http://prometheus:9090/api/v1/query?query=avg_over_time(rtc_drift_ms[24h])"

# Hardware diagnostics
echo "Hardware Diagnostics:"
kubectl exec rtc-pod-0 -- rtc-cli hw-diag

echo "=== Check Complete ==="
