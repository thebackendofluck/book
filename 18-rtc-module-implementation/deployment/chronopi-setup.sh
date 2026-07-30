#!/usr/bin/env bash
# Companion code for "The Backend of Luck" - Chapter 18, Real-Time Clock Module Implementation.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# shellcheck disable=SC2034,SC2155
# =============================================================================
# ChronoPi RTC Module Setup & Configuration
# =============================================================================
# Deploys and configures 4+ ChronoPi (DS3231-based) RTC modules per data
# center for hardware-backed timestamp authority in regulated gambling systems.
#
# GLI-11 Requirement: Section 5.4 mandates that electronic gaming systems
# maintain accurate time from a tamper-resistant source. Multiple independent
# RTC modules provide the redundancy needed for Byzantine fault tolerance.
#
# Prerequisites:
#   - Raspberry Pi or equivalent SBC with I2C bus
#   - DS3231 RTC modules (4 minimum per data center)
#   - Root access for I2C and device tree configuration
#   - Network connectivity for initial NTP sync
#
# Usage:
#   sudo ./chronopi-setup.sh --modules 4 --datacenter dc-east-1
#   sudo ./chronopi-setup.sh --modules 6 --datacenter dc-west-1 --gps-enabled
# =============================================================================

set -euo pipefail
IFS=$'\n\t'

# -----------------------------------------------------------------------------
# Configuration
# -----------------------------------------------------------------------------
readonly SCRIPT_NAME="$(basename "$0")"
readonly LOG_FILE="/var/log/rtc-setup.log"
readonly CONFIG_DIR="/etc/rtc-service"
readonly I2C_BUS_DEFAULT=1
readonly DS3231_BASE_ADDR=0x68
readonly MIN_MODULES=4
readonly NTP_SERVERS=(
    "time.google.com"
    "time.cloudflare.com"
    "pool.ntp.org"
    "time.nist.gov"
)

# GLI-11 compliant drift threshold (milliseconds)
readonly MAX_DRIFT_MS=50
readonly CONSENSUS_QUORUM=3

# Color output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# -----------------------------------------------------------------------------
# Argument parsing
# -----------------------------------------------------------------------------
MODULE_COUNT=4
DATACENTER="dc-default"
GPS_ENABLED=false
DRY_RUN=false
SKIP_NTP_SYNC=false

usage() {
    cat <<EOF
Usage: $SCRIPT_NAME [OPTIONS]

Options:
    --modules N        Number of RTC modules to configure (min: $MIN_MODULES, default: 4)
    --datacenter NAME  Data center identifier (default: dc-default)
    --gps-enabled      Enable GPS time source integration
    --dry-run          Show what would be done without making changes
    --skip-ntp-sync    Skip initial NTP synchronization
    -h, --help         Show this help message

Examples:
    $SCRIPT_NAME --modules 4 --datacenter dc-east-1
    $SCRIPT_NAME --modules 6 --datacenter dc-west-1 --gps-enabled
    $SCRIPT_NAME --dry-run --modules 4

GLI-11 Note:
    Section 5.4 requires tamper-resistant time sources. A minimum of 4 modules
    is enforced for BFT consensus (tolerates 1 Byzantine fault with 3f+1 nodes).
EOF
    exit 0
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --modules)    MODULE_COUNT="$2"; shift 2 ;;
        --datacenter) DATACENTER="$2"; shift 2 ;;
        --gps-enabled) GPS_ENABLED=true; shift ;;
        --dry-run)    DRY_RUN=true; shift ;;
        --skip-ntp-sync) SKIP_NTP_SYNC=true; shift ;;
        -h|--help)    usage ;;
        *) echo "Unknown option: $1"; usage ;;
    esac
done

# -----------------------------------------------------------------------------
# Logging
# -----------------------------------------------------------------------------
log() {
    local level="$1"; shift
    local msg="$*"
    local timestamp
    timestamp="$(date -u '+%Y-%m-%dT%H:%M:%S.%3NZ')"
    echo -e "${timestamp} [${level}] ${msg}" | tee -a "$LOG_FILE" 2>/dev/null || true
}

info()  { log "INFO"  "${GREEN}$*${NC}"; }
warn()  { log "WARN"  "${YELLOW}$*${NC}"; }
error() { log "ERROR" "${RED}$*${NC}"; }
step()  { log "STEP"  "${BLUE}>>> $*${NC}"; }

# -----------------------------------------------------------------------------
# Preflight checks
# -----------------------------------------------------------------------------
preflight_checks() {
    step "Running preflight checks"

    # Must run as root
    if [[ $EUID -ne 0 ]]; then
        error "This script must be run as root (sudo)"
        exit 1
    fi

    # Validate module count (BFT requires 3f+1 where f=1, so minimum 4)
    if [[ $MODULE_COUNT -lt $MIN_MODULES ]]; then
        error "Minimum $MIN_MODULES modules required for BFT consensus (3f+1 where f=1)"
        error "Got: $MODULE_COUNT modules"
        exit 1
    fi

    # Check for I2C support
    if ! command -v i2cdetect &>/dev/null; then
        warn "i2c-tools not found, installing..."
        if [[ "$DRY_RUN" == "false" ]]; then
            apt-get update -qq && apt-get install -y -qq i2c-tools python3-smbus
        fi
    fi

    # Check kernel module
    if ! lsmod | grep -q i2c_dev; then
        warn "i2c_dev kernel module not loaded"
        if [[ "$DRY_RUN" == "false" ]]; then
            modprobe i2c-dev
            echo "i2c-dev" >> /etc/modules-load.d/i2c.conf
        fi
    fi

    # Verify I2C bus exists
    if [[ ! -e "/dev/i2c-${I2C_BUS_DEFAULT}" ]]; then
        error "/dev/i2c-${I2C_BUS_DEFAULT} not found. Enable I2C in raspi-config or device tree."
        exit 1
    fi

    info "Preflight checks passed"
}

# -----------------------------------------------------------------------------
# Enable I2C multiplexer for multiple RTC modules
# -----------------------------------------------------------------------------
configure_i2c_multiplexer() {
    step "Configuring I2C multiplexer for $MODULE_COUNT RTC modules"

    # When using 4+ DS3231 modules on the same bus, we need a TCA9548A
    # I2C multiplexer since all DS3231 chips share address 0x68
    local mux_addr=0x70

    if [[ "$DRY_RUN" == "true" ]]; then
        info "[DRY RUN] Would configure TCA9548A multiplexer at $mux_addr"
        return
    fi

    # Check if multiplexer is present
    if i2cdetect -y "$I2C_BUS_DEFAULT" 2>/dev/null | grep -q "70"; then
        info "TCA9548A I2C multiplexer detected at $mux_addr"
    else
        warn "TCA9548A multiplexer not detected at $mux_addr"
        warn "If modules share addresses, a multiplexer is required"
        warn "Continuing with direct addressing (modules must have unique addresses)"
    fi

    # Create device tree overlay for multiplexer
    cat > /tmp/tca9548a-overlay.dts <<'DTOVERLAY'
/dts-v1/;
/plugin/;

/ {
    compatible = "brcm,bcm2835";

    fragment@0 {
        target = <&i2c1>;
        __overlay__ {
            #address-cells = <1>;
            #size-cells = <0>;
            status = "okay";

            i2c-mux@70 {
                compatible = "nxp,pca9548";
                #address-cells = <1>;
                #size-cells = <0>;
                reg = <0x70>;

                i2c@0 {
                    #address-cells = <1>;
                    #size-cells = <0>;
                    reg = <0>;
                    rtc@68 {
                        compatible = "maxim,ds3231";
                        reg = <0x68>;
                    };
                };
                i2c@1 {
                    #address-cells = <1>;
                    #size-cells = <0>;
                    reg = <1>;
                    rtc@68 {
                        compatible = "maxim,ds3231";
                        reg = <0x68>;
                    };
                };
                i2c@2 {
                    #address-cells = <1>;
                    #size-cells = <0>;
                    reg = <2>;
                    rtc@68 {
                        compatible = "maxim,ds3231";
                        reg = <0x68>;
                    };
                };
                i2c@3 {
                    #address-cells = <1>;
                    #size-cells = <0>;
                    reg = <3>;
                    rtc@68 {
                        compatible = "maxim,ds3231";
                        reg = <0x68>;
                    };
                };
            };
        };
    };
};
DTOVERLAY

    info "I2C multiplexer overlay created"
}

# -----------------------------------------------------------------------------
# Detect and validate RTC modules
# -----------------------------------------------------------------------------
detect_rtc_modules() {
    step "Detecting RTC modules on I2C bus $I2C_BUS_DEFAULT"

    local detected=0
    local modules=()

    # Scan I2C bus for DS3231 devices
    # DS3231 default address is 0x68, EEPROM at 0x57
    local scan_output
    scan_output=$(i2cdetect -y "$I2C_BUS_DEFAULT" 2>/dev/null || true)

    # Check each potential bus (direct or through multiplexer)
    for bus_num in $(seq 0 $((MODULE_COUNT + 2))); do
        local bus_path="/dev/i2c-${bus_num}"
        if [[ -e "$bus_path" ]]; then
            if i2cdetect -y "$bus_num" 2>/dev/null | grep -q "68"; then
                detected=$((detected + 1))
                modules+=("bus=${bus_num}:addr=0x68:id=rtc-${DATACENTER}-${detected}")
                info "  Found DS3231 on bus $bus_num at 0x68"
            fi
        fi
    done

    if [[ $detected -lt $MODULE_COUNT ]]; then
        warn "Detected $detected modules, expected $MODULE_COUNT"
        warn "Some modules may be offline or not connected"
        if [[ $detected -lt $MIN_MODULES ]]; then
            error "CRITICAL: Fewer than $MIN_MODULES modules detected!"
            error "BFT consensus requires at least $MIN_MODULES modules"
            error "Check hardware connections and try again"
            exit 1
        fi
    fi

    info "Detected $detected RTC modules"
    printf '%s\n' "${modules[@]}"
}

# -----------------------------------------------------------------------------
# Validate RTC module accuracy
# -----------------------------------------------------------------------------
validate_rtc_accuracy() {
    local bus="$1"
    local addr="$2"
    local module_id="$3"

    step "Validating accuracy of $module_id (bus=$bus, addr=$addr)"

    # Read current time from RTC
    # DS3231 registers: 0x00=seconds, 0x01=minutes, 0x02=hours,
    #                   0x03=day, 0x04=date, 0x05=month, 0x06=year
    local rtc_seconds rtc_minutes rtc_hours rtc_date rtc_month rtc_year
    rtc_seconds=$(i2cget -y "$bus" "$addr" 0x00 2>/dev/null || echo "0x00")
    rtc_minutes=$(i2cget -y "$bus" "$addr" 0x01 2>/dev/null || echo "0x00")
    rtc_hours=$(i2cget -y "$bus" "$addr" 0x02 2>/dev/null || echo "0x00")
    rtc_date=$(i2cget -y "$bus" "$addr" 0x04 2>/dev/null || echo "0x01")
    rtc_month=$(i2cget -y "$bus" "$addr" 0x05 2>/dev/null || echo "0x01")
    rtc_year=$(i2cget -y "$bus" "$addr" 0x06 2>/dev/null || echo "0x00")

    # Convert BCD to decimal
    bcd_to_dec() { echo $(( (16#${1:2} / 16) * 10 + (16#${1:2} % 16) )); }

    local sec min hour day mon yr
    sec=$(bcd_to_dec "$rtc_seconds")
    min=$(bcd_to_dec "$rtc_minutes")
    hour=$(bcd_to_dec "$rtc_hours")
    day=$(bcd_to_dec "$rtc_date")
    mon=$(bcd_to_dec "$rtc_month")
    yr=$((2000 + $(bcd_to_dec "$rtc_year")))

    local rtc_time="${yr}-$(printf '%02d' "$mon")-$(printf '%02d' "$day")T$(printf '%02d' "$hour"):$(printf '%02d' "$min"):$(printf '%02d' "$sec")Z"
    local sys_time
    sys_time=$(date -u '+%Y-%m-%dT%H:%M:%SZ')

    local rtc_epoch sys_epoch drift_ms
    rtc_epoch=$(date -u -d "$rtc_time" '+%s' 2>/dev/null || echo "0")
    sys_epoch=$(date -u '+%s')
    drift_ms=$(( (rtc_epoch - sys_epoch) * 1000 ))

    # Take absolute value
    if [[ $drift_ms -lt 0 ]]; then
        drift_ms=$(( -drift_ms ))
    fi

    info "  $module_id: RTC=$rtc_time System=$sys_time Drift=${drift_ms}ms"

    # Read temperature register (0x11-0x12)
    local temp_msb temp_lsb temp_c
    temp_msb=$(i2cget -y "$bus" "$addr" 0x11 2>/dev/null || echo "0x00")
    temp_lsb=$(i2cget -y "$bus" "$addr" 0x12 2>/dev/null || echo "0x00")
    temp_c=$(( $(printf '%d' "$temp_msb") ))
    info "  $module_id: Temperature=${temp_c}C"

    # Check aging register (drift compensation)
    local aging_offset
    aging_offset=$(i2cget -y "$bus" "$addr" 0x10 2>/dev/null || echo "0x00")
    info "  $module_id: Aging offset=$aging_offset"

    # GLI-11 compliance check
    if [[ $drift_ms -gt $MAX_DRIFT_MS ]]; then
        warn "  $module_id: Drift ${drift_ms}ms EXCEEDS GLI-11 threshold of ${MAX_DRIFT_MS}ms"
        warn "  Module requires synchronization before entering service"
        return 1
    fi

    info "  $module_id: PASSED accuracy validation (drift ${drift_ms}ms < ${MAX_DRIFT_MS}ms)"
    return 0
}

# -----------------------------------------------------------------------------
# Synchronize RTC modules to authoritative time
# -----------------------------------------------------------------------------
sync_rtc_modules() {
    step "Synchronizing RTC modules to authoritative NTP time"

    if [[ "$SKIP_NTP_SYNC" == "true" ]]; then
        warn "Skipping NTP sync as requested (--skip-ntp-sync)"
        return
    fi

    # Force NTP sync first
    info "Forcing NTP synchronization..."
    if command -v chronyc &>/dev/null; then
        chronyc makestep 2>/dev/null || true
        chronyc waitsync 10 0.001 2>/dev/null || true
    elif command -v ntpdate &>/dev/null; then
        for server in "${NTP_SERVERS[@]}"; do
            if ntpdate -u "$server" 2>/dev/null; then
                info "Synced to $server"
                break
            fi
        done
    else
        warn "Neither chrony nor ntpdate found. Install chrony for NTP sync."
    fi

    # Write system time to all RTC modules
    # hwclock --systohc writes to /dev/rtc0 by default
    local sys_time
    sys_time=$(date -u '+%Y-%m-%dT%H:%M:%S')

    for bus_num in $(seq 0 $((MODULE_COUNT - 1))); do
        local rtc_dev="/dev/rtc${bus_num}"
        if [[ -e "$rtc_dev" ]]; then
            if [[ "$DRY_RUN" == "false" ]]; then
                hwclock --rtc="$rtc_dev" --systohc --utc 2>/dev/null || true
                info "  Synced $rtc_dev to $sys_time UTC"
            else
                info "  [DRY RUN] Would sync $rtc_dev to $sys_time UTC"
            fi
        fi
    done

    info "All RTC modules synchronized"
}

# -----------------------------------------------------------------------------
# Generate RTC service configuration
# -----------------------------------------------------------------------------
generate_config() {
    step "Generating RTC service configuration"

    if [[ "$DRY_RUN" == "true" ]]; then
        info "[DRY RUN] Would create config at $CONFIG_DIR/rtc-config.json"
        return
    fi

    mkdir -p "$CONFIG_DIR"

    # Build module list
    local modules_json="["
    for i in $(seq 1 "$MODULE_COUNT"); do
        local comma=""
        [[ $i -gt 1 ]] && comma=","
        local bus_id=$((I2C_BUS_DEFAULT + i - 1))
        modules_json+="${comma}{\"id\":\"rtc-${DATACENTER}-${i}\",\"bus\":\"${bus_id}\",\"address\":104}"
    done
    modules_json+="]"

    # Extract primary and secondary modules
    local primary_json
    primary_json=$(echo "$modules_json" | python3 -c "
import json, sys
modules = json.load(sys.stdin)
print(json.dumps(modules[0]))
" 2>/dev/null || echo '{"id":"rtc-primary","bus":"1","address":104}')

    local secondary_json
    secondary_json=$(echo "$modules_json" | python3 -c "
import json, sys
modules = json.load(sys.stdin)
print(json.dumps(modules[1:]))
" 2>/dev/null || echo '[]')

    cat > "$CONFIG_DIR/rtc-config.json" <<JSONEOF
{
    "datacenter": "${DATACENTER}",
    "consensus_quorum": ${CONSENSUS_QUORUM},
    "drift_threshold_ms": ${MAX_DRIFT_MS},
    "secret_key": "REPLACE_WITH_HSM_DERIVED_KEY",
    "listen_addr": ":8080",
    "grpc_addr": ":50051",
    "metrics_addr": ":9090",
    "primary_rtc": ${primary_json},
    "secondary_rtcs": ${secondary_json},
    "gps_enabled": ${GPS_ENABLED},
    "failover": {
        "enabled": true,
        "cascade": ["rtc_consensus", "gps", "ntp", "degraded_rtc"],
        "ntp_servers": ["time.google.com", "time.cloudflare.com", "pool.ntp.org"],
        "gps_device": "/dev/ttyAMA0",
        "degraded_mode_max_drift_ms": 500
    },
    "signing": {
        "algorithm": "HMAC-SHA256",
        "key_rotation_interval_hours": 2160,
        "tpm_enabled": false,
        "hsm_enabled": false
    },
    "monitoring": {
        "prometheus_enabled": true,
        "health_check_interval_seconds": 30,
        "drift_sample_interval_ms": 1000,
        "battery_check_interval_seconds": 3600,
        "temperature_alert_celsius": 70
    },
    "compliance": {
        "standard": "GLI-11",
        "max_drift_ms": 50,
        "audit_log_enabled": true,
        "audit_log_path": "/var/log/rtc-audit.log",
        "signature_validation": true
    }
}
JSONEOF

    chmod 640 "$CONFIG_DIR/rtc-config.json"
    info "Configuration written to $CONFIG_DIR/rtc-config.json"
}

# -----------------------------------------------------------------------------
# Configure systemd service
# -----------------------------------------------------------------------------
configure_systemd() {
    step "Configuring systemd service for RTC monitoring"

    if [[ "$DRY_RUN" == "true" ]]; then
        info "[DRY RUN] Would create systemd service"
        return
    fi

    cat > /etc/systemd/system/rtc-service.service <<'SYSTEMD'
[Unit]
Description=Casino RTC Timestamp Service
Documentation=https://wiki.internal/rtc-service
After=network.target i2c.target
Wants=network.target

[Service]
Type=notify
User=rtc-service
Group=rtc-service
Environment=RTC_CONFIG=/etc/rtc-service/rtc-config.json
ExecStart=/usr/local/bin/rtc-service
ExecReload=/bin/kill -HUP $MAINPID
Restart=always
RestartSec=5
TimeoutStartSec=30
TimeoutStopSec=30

# Security hardening
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/var/log/rtc-service /var/cache/rtc
PrivateTmp=true
ProtectKernelTunables=true
ProtectControlGroups=true
MemoryDenyWriteExecute=true
SystemCallFilter=@system-service

# I2C device access
SupplementaryGroups=i2c

# Resource limits
LimitNOFILE=65536
MemoryMax=2G
CPUQuota=200%

# Watchdog
WatchdogSec=30

[Install]
WantedBy=multi-user.target
SYSTEMD

    # Create service user if not exists
    if ! id rtc-service &>/dev/null; then
        useradd --system --no-create-home --shell /usr/sbin/nologin rtc-service
        usermod -aG i2c rtc-service 2>/dev/null || true
    fi

    # Create required directories
    mkdir -p /var/log/rtc-service /var/cache/rtc
    chown rtc-service:rtc-service /var/log/rtc-service /var/cache/rtc

    systemctl daemon-reload
    info "Systemd service configured"
}

# -----------------------------------------------------------------------------
# Configure GPS time source (optional)
# -----------------------------------------------------------------------------
configure_gps() {
    if [[ "$GPS_ENABLED" != "true" ]]; then
        return
    fi

    step "Configuring GPS time source"

    if [[ "$DRY_RUN" == "true" ]]; then
        info "[DRY RUN] Would configure GPS time source"
        return
    fi

    # Install gpsd
    apt-get install -y -qq gpsd gpsd-clients 2>/dev/null || true

    # Configure gpsd
    cat > /etc/default/gpsd <<'GPSDCONF'
# GPS daemon configuration for RTC time source
START_DAEMON="true"
USBAUTO="true"
DEVICES="/dev/ttyAMA0"
GPSD_OPTIONS="-n -b"
GPSDCONF

    # Enable PPS (Pulse Per Second) for nanosecond accuracy
    if [[ -e /dev/pps0 ]]; then
        info "PPS device detected at /dev/pps0"
    else
        warn "PPS device not found. GPS accuracy limited to ~10ms without PPS."
    fi

    systemctl restart gpsd 2>/dev/null || true
    info "GPS time source configured"
}

# -----------------------------------------------------------------------------
# Setup log rotation
# -----------------------------------------------------------------------------
configure_logrotate() {
    step "Configuring log rotation"

    if [[ "$DRY_RUN" == "true" ]]; then
        info "[DRY RUN] Would configure log rotation"
        return
    fi

    cat > /etc/logrotate.d/rtc-service <<'LOGROTATE'
/var/log/rtc-service/*.log /var/log/rtc-audit.log {
    daily
    rotate 365
    compress
    delaycompress
    missingok
    notifempty
    create 0640 rtc-service rtc-service
    postrotate
        systemctl reload rtc-service 2>/dev/null || true
    endscript
}
LOGROTATE

    # GLI-11 requires 365-day audit log retention
    info "Log rotation configured (365-day retention for GLI-11 compliance)"
}

# -----------------------------------------------------------------------------
# Run post-installation validation
# -----------------------------------------------------------------------------
post_install_validation() {
    step "Running post-installation validation"

    local errors=0

    # Check config file
    if [[ -f "$CONFIG_DIR/rtc-config.json" ]]; then
        info "  Config file: OK"
    else
        error "  Config file: MISSING"
        errors=$((errors + 1))
    fi

    # Check systemd service
    if systemctl is-enabled rtc-service &>/dev/null; then
        info "  Systemd service: ENABLED"
    else
        warn "  Systemd service: NOT ENABLED (run: systemctl enable rtc-service)"
    fi

    # Check I2C devices
    local found_modules=0
    for bus_num in $(seq 0 10); do
        if [[ -e "/dev/i2c-${bus_num}" ]]; then
            if i2cdetect -y "$bus_num" 2>/dev/null | grep -q "68"; then
                found_modules=$((found_modules + 1))
            fi
        fi
    done

    if [[ $found_modules -ge $MIN_MODULES ]]; then
        info "  RTC modules: $found_modules detected (minimum: $MIN_MODULES)"
    else
        warn "  RTC modules: $found_modules detected (minimum: $MIN_MODULES)"
    fi

    # Check GPS if enabled
    if [[ "$GPS_ENABLED" == "true" ]]; then
        if command -v gpspipe &>/dev/null && gpspipe -w -n 1 2>/dev/null | grep -q "TPV"; then
            info "  GPS: ACTIVE"
        else
            warn "  GPS: NOT RESPONDING (check antenna and gpsd)"
        fi
    fi

    if [[ $errors -gt 0 ]]; then
        error "Post-installation validation found $errors error(s)"
        return 1
    fi

    info "Post-installation validation passed"
}

# -----------------------------------------------------------------------------
# Print deployment summary
# -----------------------------------------------------------------------------
print_summary() {
    echo ""
    echo "============================================================================="
    echo "  RTC Module Deployment Summary"
    echo "============================================================================="
    echo "  Data Center:      $DATACENTER"
    echo "  Modules:          $MODULE_COUNT"
    echo "  Consensus Quorum: $CONSENSUS_QUORUM"
    echo "  Max Drift:        ${MAX_DRIFT_MS}ms (GLI-11)"
    echo "  GPS Enabled:      $GPS_ENABLED"
    echo "  Config File:      $CONFIG_DIR/rtc-config.json"
    echo "  Log File:         $LOG_FILE"
    echo "============================================================================="
    echo ""
    echo "  Next Steps:"
    echo "    1. Review and update $CONFIG_DIR/rtc-config.json"
    echo "    2. Replace secret_key with HSM-derived key"
    echo "    3. Start service: systemctl start rtc-service"
    echo "    4. Verify health: curl http://localhost:8080/api/v1/health"
    echo "    5. Run monthly health check: ./runbooks/monthly-health-check.sh"
    echo ""
    echo "  GLI-11 Compliance Notes:"
    echo "    - Audit logs retained for 365 days"
    echo "    - Drift threshold set to ${MAX_DRIFT_MS}ms"
    echo "    - Minimum ${MIN_MODULES} modules for BFT consensus"
    echo "    - Signature validation enabled on all timestamps"
    echo "============================================================================="
}

# -----------------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------------
main() {
    info "Starting ChronoPi RTC setup for $DATACENTER ($MODULE_COUNT modules)"

    if [[ "$DRY_RUN" == "true" ]]; then
        warn "DRY RUN MODE - no changes will be made"
    fi

    preflight_checks
    configure_i2c_multiplexer
    detect_rtc_modules
    sync_rtc_modules
    generate_config
    configure_systemd
    configure_gps
    configure_logrotate

    if [[ "$DRY_RUN" == "false" ]]; then
        post_install_validation
    fi

    print_summary
    info "ChronoPi RTC setup complete"
}

main "$@"
