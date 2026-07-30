#!/usr/bin/env bash
# Companion code for "The Backend of Luck" - Chapter 24, Security and Compliance.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# suricata-rule-update.sh
#
# Downloads and applies Suricata rule updates from Emerging Threats Open.
# Validates rules before live-reloading the engine; rolls back and alerts
# on failure.  Supports scheduled maintenance blackout windows.
#
# Required environment variables:
#   SLACK_WEBHOOK_URL   - Slack incoming webhook for notifications
#
# Optional environment variables:
#   BLACKOUT_FILE       - Path to newline-delimited file of blackout dates
#                         in YYYY-MM-DD format.  If today's date is listed,
#                         the update is skipped.  Default: unset (no blackout)
#   SURICATA_BIN        - Path to suricata binary (default: /usr/bin/suricata)
#   SURICATA_CONF       - Path to suricata.yaml (default: /etc/suricata/suricata.yaml)
#   SURICATA_RULES_DIR  - Rules directory (default: /var/lib/suricata/rules)
#   SURICATA_PID_FILE   - PID file path (default: /var/run/suricata.pid)
#   UPDATE_LOG          - Update log path (default: /var/log/suricata/rule-update.log)
#
# Exit codes:
#   0 - success (rules updated and reloaded, or blackout window skipped)
#   1 - failure (update, validation, or reload failed)

set -euo pipefail

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
SURICATA_BIN="${SURICATA_BIN:-/usr/bin/suricata}"
SURICATA_CONF="${SURICATA_CONF:-/etc/suricata/suricata.yaml}"
SURICATA_RULES_DIR="${SURICATA_RULES_DIR:-/var/lib/suricata/rules}"
SURICATA_PID_FILE="${SURICATA_PID_FILE:-/var/run/suricata.pid}"
UPDATE_LOG="${UPDATE_LOG:-/var/log/suricata/rule-update.log}"
BLACKOUT_FILE="${BLACKOUT_FILE:-}"
SLACK_WEBHOOK_URL="${SLACK_WEBHOOK_URL:-}"

TIMESTAMP=$(date -Iseconds)
TODAY=$(date +%Y-%m-%d)

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
log() {
    local level="$1"; shift
    echo "${TIMESTAMP} [${level}] $*" | tee -a "${UPDATE_LOG}"
    logger -t "suricata-rule-update" -p "daemon.${level,,}" "$*" 2>/dev/null || true
}

# ---------------------------------------------------------------------------
# Slack notification helper
# ---------------------------------------------------------------------------
slack_notify() {
    local color="$1"   # good | warning | danger
    local title="$2"
    local body="$3"

    if [[ -z "${SLACK_WEBHOOK_URL}" ]]; then
        log "warning" "SLACK_WEBHOOK_URL not set – skipping Slack notification"
        return
    fi

    local payload
    payload=$(cat <<EOF
{
  "username": "Suricata Rule Updater",
  "icon_emoji": ":rolled_up_newspaper:",
  "attachments": [
    {
      "color": "${color}",
      "title": "${title}",
      "text": "${body}",
      "footer": "iGaming IDS | $(hostname) | ${TIMESTAMP}"
    }
  ]
}
EOF
)
    curl --silent --max-time 10 -X POST \
        -H 'Content-type: application/json' \
        --data "${payload}" \
        "${SLACK_WEBHOOK_URL}" \
        >> "${UPDATE_LOG}" 2>&1 || log "warning" "Slack POST failed (non-fatal)"
}

# ---------------------------------------------------------------------------
# Cleanup on exit
# ---------------------------------------------------------------------------
BACKUP_DIR=""
cleanup() {
    local exit_code=$?
    if [[ -n "${BACKUP_DIR}" && -d "${BACKUP_DIR}" && "${exit_code}" -eq 0 ]]; then
        rm -rf "${BACKUP_DIR}"
    fi
}
trap cleanup EXIT

# ---------------------------------------------------------------------------
# Blackout window check
# ---------------------------------------------------------------------------
if [[ -n "${BLACKOUT_FILE}" && -f "${BLACKOUT_FILE}" ]]; then
    if grep -qx "${TODAY}" "${BLACKOUT_FILE}"; then
        log "info" "Blackout date ${TODAY} matched in ${BLACKOUT_FILE} – skipping rule update"
        slack_notify "warning" \
            "Suricata Rule Update Skipped – Blackout Window" \
            "Today (${TODAY}) is listed in the maintenance blackout file. No update was applied."
        exit 0
    fi
fi

# ---------------------------------------------------------------------------
# Pre-update: count existing rules for delta reporting
# ---------------------------------------------------------------------------
RULES_BEFORE=0
if [[ -d "${SURICATA_RULES_DIR}" ]]; then
    RULES_BEFORE=$(grep -rh '^alert\|^drop\|^pass\|^reject' "${SURICATA_RULES_DIR}"/*.rules 2>/dev/null | wc -l || echo 0)
fi
log "info" "Rule count before update: ${RULES_BEFORE}"

# ---------------------------------------------------------------------------
# Backup current rules
# ---------------------------------------------------------------------------
BACKUP_DIR=$(mktemp -d /tmp/suricata-rules-backup-XXXXXX)
log "info" "Backing up current rules to ${BACKUP_DIR}"
if [[ -d "${SURICATA_RULES_DIR}" ]]; then
    cp -r "${SURICATA_RULES_DIR}/." "${BACKUP_DIR}/" 2>/dev/null || true
fi

# ---------------------------------------------------------------------------
# Run suricata-update
# ---------------------------------------------------------------------------
log "info" "Running suricata-update with Emerging Threats Open"

UPDATE_OUTPUT=$(mktemp /tmp/suricata-update-output-XXXXXX)

if ! suricata-update \
        --suricata "${SURICATA_BIN}" \
        --suricata-conf "${SURICATA_CONF}" \
        --output "${SURICATA_RULES_DIR}" \
        --no-merge \
        2>&1 | tee "${UPDATE_OUTPUT}" | tee -a "${UPDATE_LOG}"; then

    log "err" "suricata-update failed – restoring backup"
    cp -r "${BACKUP_DIR}/." "${SURICATA_RULES_DIR}/" 2>/dev/null || true
    slack_notify "danger" \
        ":x: Suricata Rule Update FAILED on $(hostname)" \
        "suricata-update exited with an error. Previous rules restored. Check ${UPDATE_LOG} for details."
    rm -f "${UPDATE_OUTPUT}"
    exit 1
fi

# ---------------------------------------------------------------------------
# Extract rule counts from suricata-update output
# ---------------------------------------------------------------------------
RULES_AFTER=$(grep -rh '^alert\|^drop\|^pass\|^reject' "${SURICATA_RULES_DIR}"/*.rules 2>/dev/null | wc -l || echo 0)
RULES_NEW=$(grep -oc 'Added rules:' "${UPDATE_OUTPUT}" 2>/dev/null || echo 0)
RULES_MODIFIED=$(grep -oc 'Modified rules:' "${UPDATE_OUTPUT}" 2>/dev/null || echo 0)
RULES_REMOVED=$(grep -oc 'Removed rules:' "${UPDATE_OUTPUT}" 2>/dev/null || echo 0)

# More robust extraction if update writes summary lines
if grep -q 'Added rules:' "${UPDATE_OUTPUT}"; then
    RULES_NEW=$(awk '/Added rules:/ {print $NF}' "${UPDATE_OUTPUT}" | tail -1)
fi
if grep -q 'Modified rules:' "${UPDATE_OUTPUT}"; then
    RULES_MODIFIED=$(awk '/Modified rules:/ {print $NF}' "${UPDATE_OUTPUT}" | tail -1)
fi
if grep -q 'Removed rules:' "${UPDATE_OUTPUT}"; then
    RULES_REMOVED=$(awk '/Removed rules:/ {print $NF}' "${UPDATE_OUTPUT}" | tail -1)
fi
rm -f "${UPDATE_OUTPUT}"

log "info" "Rule counts – total: ${RULES_AFTER}, before: ${RULES_BEFORE}, new: ${RULES_NEW}, modified: ${RULES_MODIFIED}, removed: ${RULES_REMOVED}"

# ---------------------------------------------------------------------------
# Validate updated rules with suricata -T (test mode)
# ---------------------------------------------------------------------------
log "info" "Validating rules with: ${SURICATA_BIN} -T -c ${SURICATA_CONF}"

VALIDATION_OUTPUT=$(mktemp /tmp/suricata-validate-XXXXXX)

if ! "${SURICATA_BIN}" -T \
        -c "${SURICATA_CONF}" \
        --set "default-rule-path=${SURICATA_RULES_DIR}" \
        2>&1 | tee "${VALIDATION_OUTPUT}" | tee -a "${UPDATE_LOG}"; then

    log "err" "Rule validation FAILED – rolling back to previous rule set"

    # Restore backup
    cp -r "${BACKUP_DIR}/." "${SURICATA_RULES_DIR}/" 2>/dev/null || true

    VALIDATION_ERRORS=$(grep -i 'error\|failed\|invalid' "${VALIDATION_OUTPUT}" | head -5 | tr '\n' ' ')
    slack_notify "danger" \
        ":x: Suricata Rule Validation FAILED on $(hostname)" \
        "suricata -T returned errors. Previous rules restored. Errors: ${VALIDATION_ERRORS:-see log}. Log: ${UPDATE_LOG}"
    rm -f "${VALIDATION_OUTPUT}"
    exit 1
fi

rm -f "${VALIDATION_OUTPUT}"
log "info" "Rule validation passed"

# ---------------------------------------------------------------------------
# Live reload via SIGUSR2
# ---------------------------------------------------------------------------
if [[ -f "${SURICATA_PID_FILE}" ]]; then
    SURICATA_PID=$(cat "${SURICATA_PID_FILE}")
    if kill -0 "${SURICATA_PID}" 2>/dev/null; then
        log "info" "Sending SIGUSR2 to Suricata PID ${SURICATA_PID} for live rule reload"
        kill -SIGUSR2 "${SURICATA_PID}"
        # Brief pause to let Suricata acknowledge the reload
        sleep 2
        log "info" "SIGUSR2 sent – rules reloading in background"
    else
        log "warning" "PID ${SURICATA_PID} from ${SURICATA_PID_FILE} is not running – skipping SIGUSR2"
    fi
else
    log "warning" "PID file ${SURICATA_PID_FILE} not found – skipping SIGUSR2 (is Suricata running?)"
fi

# ---------------------------------------------------------------------------
# Success notification
# ---------------------------------------------------------------------------
DELTA=$((RULES_AFTER - RULES_BEFORE))
DELTA_STR=""
if [[ "${DELTA}" -gt 0 ]]; then
    DELTA_STR="+${DELTA}"
elif [[ "${DELTA}" -lt 0 ]]; then
    DELTA_STR="${DELTA}"
else
    DELTA_STR="±0"
fi

slack_notify "good" \
    ":white_check_mark: Suricata Rules Updated Successfully on $(hostname)" \
    "Total rules: ${RULES_AFTER} (${DELTA_STR})\nNew: ${RULES_NEW} | Modified: ${RULES_MODIFIED} | Removed: ${RULES_REMOVED}\nLive reload via SIGUSR2 sent."

log "info" "Rule update complete – total rules: ${RULES_AFTER} (delta: ${DELTA_STR})"
exit 0
