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

# rtc-emergency-rollback.sh
# Emergency rollback from RTC to NTP time source.
# Use when RTC system is unavailable or critically degraded.

echo "=== RTC Emergency Rollback to NTP ==="

# Disable RTC service
kubectl scale deployment rtc-service --replicas=0

# Re-enable NTP synchronization
systemctl enable ntp
systemctl start ntp

# Update application configurations
kubectl set env deployment/game-server RTC_ENABLED=false

# Restart affected services
kubectl rollout restart deployment/game-server

# Monitor time synchronization
ntpq -p

echo "NTP fallback completed. Monitor for 30 minutes before declaring success."
