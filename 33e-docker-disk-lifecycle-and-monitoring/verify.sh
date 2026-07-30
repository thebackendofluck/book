#!/usr/bin/env bash
# Companion code for "The Backend of Luck" - Chapter 33e, Docker Disk Lifecycle, Truncation, and the Anatomy of a Disk.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# Verify Chapter 33e deployment.
set -uo pipefail

PASS=0; FAIL=0; WARN=0
ok()   { echo "  OK   $*"; PASS=$((PASS+1)); }
fail() { echo "  FAIL $*"; FAIL=$((FAIL+1)); }
warn() { echo "  WARN $*"; WARN=$((WARN+1)); }

echo "Chapter 33e -- deployment verification"

# 1. logrotate config
if [[ -f /etc/logrotate.d/docker-containers ]]; then
    ok "logrotate config installed"
else
    fail "logrotate config missing"
fi

# 2. daily prune script
if command -v docker-daily-prune.sh >/dev/null 2>&1 || [[ -x /usr/local/sbin/docker-daily-prune.sh ]]; then
    ok "daily prune script installed"
else
    fail "daily prune script missing"
fi

# 3. systemd timer
if systemctl is-enabled docker-daily-prune.timer >/dev/null 2>&1; then
    ok "systemd timer enabled"
    NEXT=$(systemctl list-timers docker-daily-prune.timer --no-pager 2>/dev/null | awk 'NR==2 {print $1, $2}')
    [[ -n "$NEXT" ]] && echo "    next run: $NEXT"
else
    fail "systemd timer not enabled"
fi

# 4. Wazuh rule (optional)
if [[ -f /etc/prometheus-rules-staging/wazuh-disk-rule.xml ]]; then
    ok "Wazuh rule staged at /etc/prometheus-rules-staging/wazuh-disk-rule.xml"
else
    warn "Wazuh rule not found at staging path"
fi

# 5. Prometheus rule (optional)
if [[ -f /etc/prometheus-rules-staging/disk-alerts.yml ]]; then
    ok "Prometheus rules staged"
else
    warn "Prometheus rules not found at staging path"
fi

echo
echo "PASS=$PASS FAIL=$FAIL WARN=$WARN"
[[ "$FAIL" -gt 0 ]] && exit 1
exit 0
