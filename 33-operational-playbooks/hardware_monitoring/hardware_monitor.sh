#!/bin/bash
# Companion code for "The Backend of Luck" - Chapter 33, Operational Playbooks.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# shellcheck disable=SC2155
# Comprehensive Hardware Monitoring Script - Chapter 23: Operational Playbooks
#
# Monitors data centre hardware components including servers (CPU, memory, fans,
# PSUs), NVMe drives (wear level, temperature, error count), traditional disks
# (SMART health, reallocated sectors, pending sectors), network interfaces
# (link status, RX/TX errors), and firewall (rule count, connection count).
#
# Output: CSV file with per-component age, health status, performance metrics,
# and alert level (low/medium/high). Requires: smartmontools, nvme-cli, ipmitool.
#
# Usage: ./hardware_monitor.sh
#
# Part of the iGaming Platform Engineering book.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_FILE="${SCRIPT_DIR}/hardware_monitor_$(date +%Y%m%d_%H%M%S).log"
CSV_OUTPUT="${SCRIPT_DIR}/hardware_metrics_$(date +%Y%m%d).csv"

# Initialize CSV header
echo "timestamp,hostname,component_type,component_id,manufacture_date,age_days,health_status,performance_metrics,alert_level" > "$CSV_OUTPUT"

log() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') - $*" | tee -a "$LOG_FILE"
}

get_manufacture_date() {
    local component=$1
    case $component in
        "server")
            # Get BIOS date as proxy for manufacture date
            dmidecode -t bios | grep "Release Date" | awk '{print $3}' || echo "unknown"
            ;;
        "nvme"|"disk")
            # Get drive manufacture date from SMART data
            smartctl -i "$2" | grep "Date" | head -1 | awk '{print $NF}' || echo "unknown"
            ;;
        "network")
            # Network cards typically don't have manufacture dates, use install date
            ethtool -i "$2" | grep "driver" | awk '{print "driver:"$2}' || echo "unknown"
            ;;
        "firewall")
            # Firewall rules don't have manufacture dates, use configuration date
            echo "config_based"
            ;;
    esac
}

calculate_age_days() {
    local date_str=$1
    if [ "$date_str" = "unknown" ] || [ "$date_str" = "config_based" ]; then
        echo "unknown"
    else
        # Convert date to days since epoch
        local date_epoch=$(date -d "$date_str" +%s 2>/dev/null || echo "0")
        local now_epoch=$(date +%s)
        echo $(( (now_epoch - date_epoch) / 86400 ))
    fi
}

monitor_server() {
    log "Monitoring server components..."

    # CPU monitoring
    local cpu_usage=$(top -bn1 | grep "Cpu(s)" | sed "s/.*, *\([0-9.]*\)%* id.*/\1/" | awk '{print 100 - $1}')
    local cpu_temp=$(sensors | grep "Core 0" | awk '{print $3}' | sed 's/+//' | sed 's/°C//')

    # Memory monitoring
    local mem_usage=$(free | grep Mem | awk '{printf "%.2f", $3/$2 * 100.0}')

    # Power supplies and fans
    local psu_status=$(ipmitool sdr | grep "PSU" | head -1 | awk '{print $NF}')
    local fan_status=$(ipmitool sdr | grep "FAN" | head -1 | awk '{print $NF}')

    local manufacture_date=$(get_manufacture_date "server")
    local age_days=$(calculate_age_days "$manufacture_date")

    local alert_level="low"
    if (( $(echo "$cpu_usage > 90" | bc -l) )) || (( $(echo "$mem_usage > 90" | bc -l) )); then
        alert_level="high"
    elif (( $(echo "$cpu_usage > 75" | bc -l) )) || (( $(echo "$mem_usage > 80" | bc -l) )); then
        alert_level="medium"
    fi

    echo "$(date +%s),$(hostname),server,main,$manufacture_date,$age_days,good,cpu:${cpu_usage}%,mem:${mem_usage}%,temp:${cpu_temp}C,psu:${psu_status},fan:${fan_status},$alert_level" >> "$CSV_OUTPUT"
}

monitor_nvme_disks() {
    log "Monitoring NVMe drives..."

    for drive in /dev/nvme*n*; do
        if [ -b "$drive" ]; then
            local drive_id=$(basename "$drive")
            local manufacture_date=$(get_manufacture_date "nvme" "$drive")

            # Get NVMe health metrics
            local wear_level=$(nvme smart-log "$drive" | grep "percentage_used" | awk '{print $3}')
            local temp=$(nvme smart-log "$drive" | grep "temperature" | awk '{print $3}')
            local error_count=$(nvme smart-log "$drive" | grep "num_err_log_entries" | awk '{print $3}')

            local age_days=$(calculate_age_days "$manufacture_date")

            local alert_level="low"
            if [ "$wear_level" -gt 90 ] || [ "$error_count" -gt 10 ]; then
                alert_level="high"
            elif [ "$wear_level" -gt 75 ] || [ "$error_count" -gt 5 ]; then
                alert_level="medium"
            fi

            echo "$(date +%s),$(hostname),nvme,$drive_id,$manufacture_date,$age_days,good,wear:${wear_level}%,temp:${temp}C,errors:${error_count},$alert_level" >> "$CSV_OUTPUT"
        fi
    done
}

monitor_traditional_disks() {
    log "Monitoring traditional disks..."

    for drive in /dev/sd*; do
        if [ -b "$drive" ]; then
            local drive_id=$(basename "$drive")
            local manufacture_date=$(get_manufacture_date "disk" "$drive")

            # Get SMART health metrics
            local health_status=$(smartctl -H "$drive" | grep "SMART overall-health" | awk '{print $NF}')
            local reallocated_sectors=$(smartctl -A "$drive" | grep "Reallocated_Sector_Ct" | awk '{print $NF}')
            local pending_sectors=$(smartctl -A "$drive" | grep "Pending_Sector" | awk '{print $NF}')
            local uncorrectable_errors=$(smartctl -A "$drive" | grep "Uncorrectable_Error_Ct" | awk '{print $NF}')

            local age_days=$(calculate_age_days "$manufacture_date")

            local alert_level="low"
            if [ "$health_status" != "PASSED" ] || [ "$reallocated_sectors" -gt 100 ] || [ "$pending_sectors" -gt 10 ]; then
                alert_level="high"
            elif [ "$reallocated_sectors" -gt 50 ] || [ "$pending_sectors" -gt 5 ]; then
                alert_level="medium"
            fi

            echo "$(date +%s),$(hostname),disk,$drive_id,$manufacture_date,$age_days,$health_status,realloc:${reallocated_sectors},pending:${pending_sectors},errors:${uncorrectable_errors},$alert_level" >> "$CSV_OUTPUT"
        fi
    done
}

monitor_network_interfaces() {
    log "Monitoring network interfaces..."

    for iface in $(ip link show | grep -E "^[0-9]+:" | awk -F: '{print $2}' | tr -d ' ' | grep -v lo); do
        local manufacture_date=$(get_manufacture_date "network" "$iface")

        # Get network statistics
        local rx_errors=$(ip -s link show "$iface" | grep "RX:" -A 1 | tail -1 | awk '{print $3}')
        local tx_errors=$(ip -s link show "$iface" | grep "TX:" -A 1 | tail -1 | awk '{print $3}')
        local link_status=$(ip link show "$iface" | grep -o "state [A-Z]*" | awk '{print $2}')

        local age_days=$(calculate_age_days "$manufacture_date")

        local alert_level="low"
        if [ "$link_status" != "UP" ] || [ "$rx_errors" -gt 1000 ] || [ "$tx_errors" -gt 1000 ]; then
            alert_level="high"
        elif [ "$rx_errors" -gt 500 ] || [ "$tx_errors" -gt 500 ]; then
            alert_level="medium"
        fi

        echo "$(date +%s),$(hostname),network,$iface,$manufacture_date,$age_days,$link_status,rx_errors:${rx_errors},tx_errors:${tx_errors},$alert_level" >> "$CSV_OUTPUT"
    done
}

monitor_firewall() {
    log "Monitoring firewall status..."

    # Check if iptables or firewalld is running
    if systemctl is-active --quiet iptables 2>/dev/null || systemctl is-active --quiet firewalld 2>/dev/null; then
        local fw_status="active"
        local rule_count=$(iptables -L 2>/dev/null | wc -l || firewall-cmd --list-all 2>/dev/null | wc -l || echo "0")
        local conn_count=$(netstat -tun | wc -l)
    else
        local fw_status="inactive"
        local rule_count="0"
        local conn_count="0"
    fi

    local manufacture_date=$(get_manufacture_date "firewall")
    local age_days=$(calculate_age_days "$manufacture_date")

    local alert_level="low"
    if [ "$fw_status" != "active" ]; then
        alert_level="high"
    fi

    echo "$(date +%s),$(hostname),firewall,main,$manufacture_date,$age_days,$fw_status,rules:${rule_count},connections:${conn_count},$alert_level" >> "$CSV_OUTPUT"
}

main() {
    log "Starting comprehensive hardware monitoring..."

    monitor_server
    monitor_nvme_disks
    monitor_traditional_disks
    monitor_network_interfaces
    monitor_firewall

    log "Hardware monitoring completed. Results saved to $CSV_OUTPUT"
}

# Run main function
main
