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

# suricata-health-check.sh
#
# Monitors Suricata runtime health by parsing stats.log.
# Writes Prometheus node_exporter textfile metrics and sends syslog alerts
# on threshold breaches.
#
# Designed for cron:
#   */5 * * * * /opt/suricata/scripts/suricata-health-check.sh
#
# Output file:  /var/lib/node_exporter/suricata.prom
# Syslog tag:   suricata-health
# Exit codes:   0 = OK / metrics written
#               1 = CRITICAL threshold breached
#               2 = stats.log unreadable or no recent stats block

set -euo pipefail

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
STATS_LOG="${STATS_LOG:-/var/log/suricata/stats.log}"
PROM_OUT="${PROM_OUT:-/var/lib/node_exporter/suricata.prom}"
PROM_TMP="${PROM_OUT}.tmp"
SYSLOG_TAG="suricata-health"

# Threshold constants
KERNEL_DROP_WARN_PCT=0.01    # 0.01 %
KERNEL_DROP_CRIT_PCT=0.10    # 0.10 %

# Baseline packet rate for decoder drop detection (packets/s)
# Overridden by the cached value in BASELINE_FILE if it exists.
BASELINE_FILE="/var/lib/suricata/pkts_baseline"
DECODER_DROP_WARN_RATIO=0.50  # 50 % drop from baseline = WARNING

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
log_syslog() {
    local priority="$1"
    local msg="$2"
    logger -t "${SYSLOG_TAG}" -p "daemon.${priority}" "${msg}"
}

die() {
    log_syslog "err" "$*"
    echo "ERROR: $*" >&2
    exit 2
}

# ---------------------------------------------------------------------------
# Parse most recent stats block from stats.log
# Suricata writes blocks separated by "---"
# ---------------------------------------------------------------------------
if [[ ! -f "${STATS_LOG}" ]]; then
    die "Stats log not found: ${STATS_LOG}"
fi

# Extract the last complete stats block (between two "---" separators)
STATS_BLOCK=$(awk '
    /^---/ { block = ""; in_block = 1; next }
    in_block { block = block "\n" $0 }
    END { print block }
' "${STATS_LOG}")

if [[ -z "${STATS_BLOCK}" ]]; then
    die "No stats block found in ${STATS_LOG}"
fi

# ---------------------------------------------------------------------------
# Field extraction helper – returns 0 if field not found
# ---------------------------------------------------------------------------
get_stat() {
    local field="$1"
    echo "${STATS_BLOCK}" | awk -F'|' -v f="${field}" '
        $2 ~ f { gsub(/[[:space:]]/, "", $3); print $3+0; found=1; exit }
        END { if (!found) print 0 }
    '
}

# ---------------------------------------------------------------------------
# Collect key counters
# ---------------------------------------------------------------------------
DECODER_PKTS=$(get_stat "decoder.pkts")
KERNEL_PKTS=$(get_stat "capture.kernel_packets")
KERNEL_DROPS=$(get_stat "capture.kernel_drops")
FLOW_MEMCAP=$(get_stat "flow.memcap")
STREAM_MEMCAP=$(get_stat "stream.memcap")
DECODER_IPV4=$(get_stat "decoder.ipv4")
CPU_PCT=$(get_stat "threads.W#01-pkt.cpu_pct")   # approximate; 0 if absent

# ---------------------------------------------------------------------------
# Compute kernel drop percentage
# ---------------------------------------------------------------------------
KERNEL_DROP_PCT=0
if [[ "${KERNEL_PKTS}" -gt 0 ]]; then
    KERNEL_DROP_PCT=$(awk "BEGIN { printf \"%.6f\", (${KERNEL_DROPS}/${KERNEL_PKTS})*100 }")
fi

# ---------------------------------------------------------------------------
# Baseline comparison for decoder packet rate
# ---------------------------------------------------------------------------
DECODER_DROP_WARN=0
if [[ -f "${BASELINE_FILE}" ]]; then
    BASELINE=$(cat "${BASELINE_FILE}")
    if [[ "${BASELINE}" -gt 0 && "${DECODER_PKTS}" -gt 0 ]]; then
        # Flag if current rate is less than (1 - ratio) * baseline
        DECODER_DROP_WARN=$(awk "BEGIN {
            ratio = ${DECODER_DROP_WARN_RATIO};
            threshold = ${BASELINE} * (1 - ratio);
            print (${DECODER_PKTS} < threshold) ? 1 : 0
        }")
    fi
fi
# Update baseline (rolling – use current value)
echo "${DECODER_PKTS}" > "${BASELINE_FILE}"

# ---------------------------------------------------------------------------
# Threshold evaluation
# ---------------------------------------------------------------------------
EXIT_CODE=0
CRITICAL_MESSAGES=()
WARNING_MESSAGES=()

# kernel_drops: WARNING
DROP_WARN=$(awk "BEGIN { print (${KERNEL_DROP_PCT} > ${KERNEL_DROP_WARN_PCT}) ? 1 : 0 }")
DROP_CRIT=$(awk "BEGIN { print (${KERNEL_DROP_PCT} > ${KERNEL_DROP_CRIT_PCT}) ? 1 : 0 }")

if [[ "${DROP_CRIT}" -eq 1 ]]; then
    msg="CRITICAL: kernel_drops at ${KERNEL_DROP_PCT}% (threshold ${KERNEL_DROP_CRIT_PCT}%)"
    CRITICAL_MESSAGES+=("${msg}")
    log_syslog "crit" "${msg}"
    EXIT_CODE=1
elif [[ "${DROP_WARN}" -eq 1 ]]; then
    msg="WARNING: kernel_drops at ${KERNEL_DROP_PCT}% (threshold ${KERNEL_DROP_WARN_PCT}%)"
    WARNING_MESSAGES+=("${msg}")
    log_syslog "warning" "${msg}"
fi

# flow.memcap – any non-zero is CRITICAL
if [[ "${FLOW_MEMCAP}" -gt 0 ]]; then
    msg="CRITICAL: flow.memcap exception count=${FLOW_MEMCAP} – memory cap exceeded for flows"
    CRITICAL_MESSAGES+=("${msg}")
    log_syslog "crit" "${msg}"
    EXIT_CODE=1
fi

# stream.memcap – any non-zero is CRITICAL
if [[ "${STREAM_MEMCAP}" -gt 0 ]]; then
    msg="CRITICAL: stream.memcap exception count=${STREAM_MEMCAP} – memory cap exceeded for streams"
    CRITICAL_MESSAGES+=("${msg}")
    log_syslog "crit" "${msg}"
    EXIT_CODE=1
fi

# decoder.pkts rate drop > 50% from baseline
if [[ "${DECODER_DROP_WARN}" -eq 1 ]]; then
    msg="WARNING: decoder.pkts dropped >50% from baseline (current=${DECODER_PKTS})"
    WARNING_MESSAGES+=("${msg}")
    log_syslog "warning" "${msg}"
fi

# ---------------------------------------------------------------------------
# Write Prometheus textfile metrics (atomic rename)
# ---------------------------------------------------------------------------
mkdir -p "$(dirname "${PROM_OUT}")"

{
    echo "# HELP suricata_kernel_drops_total Packets dropped by kernel capture interface"
    echo "# TYPE suricata_kernel_drops_total counter"
    echo "suricata_kernel_drops_total ${KERNEL_DROPS}"

    echo "# HELP suricata_kernel_packets_total Total packets seen by kernel"
    echo "# TYPE suricata_kernel_packets_total counter"
    echo "suricata_kernel_packets_total ${KERNEL_PKTS}"

    echo "# HELP suricata_kernel_drop_pct Kernel drop percentage (0-100)"
    echo "# TYPE suricata_kernel_drop_pct gauge"
    echo "suricata_kernel_drop_pct ${KERNEL_DROP_PCT}"

    echo "# HELP suricata_flow_memcap_total Flow memory cap exceptions"
    echo "# TYPE suricata_flow_memcap_total counter"
    echo "suricata_flow_memcap_total ${FLOW_MEMCAP}"

    echo "# HELP suricata_stream_memcap_total Stream memory cap exceptions"
    echo "# TYPE suricata_stream_memcap_total counter"
    echo "suricata_stream_memcap_total ${STREAM_MEMCAP}"

    echo "# HELP suricata_decoder_packets_total Packets decoded"
    echo "# TYPE suricata_decoder_packets_total counter"
    echo "suricata_decoder_packets_total ${DECODER_PKTS}"

    echo "# HELP suricata_decoder_ipv4_total IPv4 packets decoded"
    echo "# TYPE suricata_decoder_ipv4_total counter"
    echo "suricata_decoder_ipv4_total ${DECODER_IPV4}"

    echo "# HELP suricata_cpu_utilization_pct Approximate Suricata CPU utilisation (%)"
    echo "# TYPE suricata_cpu_utilization_pct gauge"
    echo "suricata_cpu_utilization_pct ${CPU_PCT}"

    echo "# HELP suricata_health_check_exit_code Last health check exit code (0=OK, 1=CRITICAL)"
    echo "# TYPE suricata_health_check_exit_code gauge"
    echo "suricata_health_check_exit_code ${EXIT_CODE}"

    echo "# HELP suricata_health_check_timestamp_seconds Unix timestamp of last health check run"
    echo "# TYPE suricata_health_check_timestamp_seconds gauge"
    echo "suricata_health_check_timestamp_seconds $(date +%s)"
} > "${PROM_TMP}"

mv "${PROM_TMP}" "${PROM_OUT}"

# ---------------------------------------------------------------------------
# Summary output to stdout (cron will capture this)
# ---------------------------------------------------------------------------
echo "suricata-health-check: $(date -Iseconds)"
echo "  kernel_drops_pct : ${KERNEL_DROP_PCT}"
echo "  flow_memcap      : ${FLOW_MEMCAP}"
echo "  stream_memcap    : ${STREAM_MEMCAP}"
echo "  decoder_pkts     : ${DECODER_PKTS}"
echo "  cpu_pct          : ${CPU_PCT}"
for m in "${CRITICAL_MESSAGES[@]+"${CRITICAL_MESSAGES[@]}"}"; do echo "  [!] ${m}"; done
for m in "${WARNING_MESSAGES[@]+"${WARNING_MESSAGES[@]}"}"; do  echo "  [W] ${m}"; done
[[ "${EXIT_CODE}" -eq 0 ]] && echo "  Status: OK"

exit "${EXIT_CODE}"
