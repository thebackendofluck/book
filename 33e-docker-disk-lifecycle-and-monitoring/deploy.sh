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

# Deploy Chapter 33e: Docker disk lifecycle, truncation, monitoring.
# Idempotent. Safe to re-run.
#
# Usage:
#   ./deploy.sh             # deploy with current config.env
#   ./deploy.sh --dry-run   # show what would change, don't apply
#   ./deploy.sh --uninstall # remove what was installed
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
DRY_RUN=0
UNINSTALL=0

for arg in "$@"; do
    case "$arg" in
        --dry-run) DRY_RUN=1 ;;
        --uninstall) UNINSTALL=1 ;;
    esac
done

# Load config — prefer config.env, fall back to config.env.example
if [[ -f "$HERE/config.env" ]]; then
    # shellcheck disable=SC1091
    source "$HERE/config.env"
elif [[ -f "$HERE/config.env.example" ]]; then
    echo "INFO: config.env not found, using defaults from config.env.example"
    # shellcheck disable=SC1091
    source "$HERE/config.env.example"
else
    echo "ERROR: no config.env or config.env.example"
    exit 65
fi

INSTALL_BIN_DIR="${INSTALL_BIN_DIR:-/usr/local/sbin}"

run() {
    echo "-> $*"
    [[ "$DRY_RUN" -eq 1 ]] && return 0
    "$@"
}

require_root() {
    if [[ "$EUID" -ne 0 ]] && ! sudo -n true 2>/dev/null; then
        echo "ERROR: needs root or passwordless sudo"
        exit 77
    fi
}

# ---------- UNINSTALL path ----------
if [[ "$UNINSTALL" -eq 1 ]]; then
    require_root
    echo "Uninstalling Chapter 33e artefacts..."
    run sudo systemctl disable --now docker-daily-prune.timer 2>/dev/null || true
    run sudo rm -f /etc/systemd/system/docker-daily-prune.service
    run sudo rm -f /etc/systemd/system/docker-daily-prune.timer
    run sudo rm -f "$INSTALL_BIN_DIR/docker-daily-prune.sh"
    run sudo rm -f /etc/logrotate.d/docker-containers
    run sudo systemctl daemon-reload
    echo "Note: Prometheus rules + Wazuh rule are not auto-uninstalled (depends on your monitoring layout)."
    exit 0
fi

# ---------- INSTALL path ----------
[[ "$DRY_RUN" -eq 0 ]] && require_root

echo "1/5 -- install logrotate config for Docker JSON logs"
run sudo install -m 0644 -o root -g root \
    "$HERE/docker-containers.logrotate" \
    /etc/logrotate.d/docker-containers
run sudo logrotate --debug /etc/logrotate.d/docker-containers >/dev/null

echo "2/5 -- install daily prune script"
run sudo install -m 0755 -o root -g root \
    "$HERE/docker-daily-prune.sh" \
    "$INSTALL_BIN_DIR/docker-daily-prune.sh"

echo "3/5 -- install systemd service + timer"
TMP_SVC=$(mktemp)
TMP_TMR=$(mktemp)
trap 'rm -f "$TMP_SVC" "$TMP_TMR"' EXIT
cat > "$TMP_SVC" <<EOF
[Unit]
Description=Docker daily prune (image + system + volume)
After=docker.service
Wants=docker.service

[Service]
Type=oneshot
ExecStart=$INSTALL_BIN_DIR/docker-daily-prune.sh
Nice=10
IOSchedulingClass=best-effort
IOSchedulingPriority=7
EOF
cat > "$TMP_TMR" <<EOF
[Unit]
Description=Run docker-daily-prune nightly

[Timer]
OnCalendar=*-*-* 04:00:00 UTC
Persistent=true
RandomizedDelaySec=20min

[Install]
WantedBy=timers.target
EOF
run sudo install -m 0644 -o root -g root "$TMP_SVC" /etc/systemd/system/docker-daily-prune.service
run sudo install -m 0644 -o root -g root "$TMP_TMR" /etc/systemd/system/docker-daily-prune.timer
run sudo systemctl daemon-reload
run sudo systemctl enable --now docker-daily-prune.timer

echo "4/5 -- copy Prometheus rules to staging path"
STAGING_DIR=/etc/prometheus-rules-staging
run sudo install -m 0755 -d "$STAGING_DIR"
run sudo install -m 0644 -o root -g root \
    "$HERE/disk-alerts.yml" \
    "$STAGING_DIR/disk-alerts.yml"
echo "  Mount this file into your Prometheus container at /etc/prometheus/disk-alerts.yml"
echo "  and reference it from the rule_files: section of prometheus.yml"

echo "5/5 -- copy Wazuh local rule to staging path"
run sudo install -m 0644 -o root -g root \
    "$HERE/wazuh-disk-rule.xml" \
    "$STAGING_DIR/wazuh-disk-rule.xml"
echo "  Append this rule to your Wazuh manager local_rules.xml"
echo "  Adjust rule_id 100700 if it conflicts with your existing rules."

echo
echo "DONE. Verify with: $HERE/verify.sh"
