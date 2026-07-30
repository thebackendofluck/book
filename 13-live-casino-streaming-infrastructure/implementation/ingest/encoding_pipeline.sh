#!/usr/bin/env bash
# Companion code for "The Backend of Luck" - Chapter 13, Live Casino Streaming Infrastructure.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# shellcheck disable=SC2001,SC2004,SC2034
# =============================================================================
# Live Casino Encoding Pipeline with Redundancy
# Chapter 6 - Live Casino Streaming Infrastructure
#
# Purpose: Production FFmpeg encoding pipeline for live casino tables with:
#   - Primary + standby encoder failover
#   - GPU-accelerated encoding (NVENC) with CPU fallback
#   - Multi-camera switching (wide, close-up, overhead, dealer)
#   - SRT + RTMP dual-output for redundancy
#   - Health monitoring and automatic restart
#
# Prerequisites:
#   - FFmpeg 6.x+ with NVENC, libsrt, libx264
#   - NVIDIA GPU with NVENC support (or CPU fallback)
#   - SDI/HDMI capture cards (Blackmagic DeckLink or similar)
#   - systemd for process management
#
# Usage:
#   ./encoding_pipeline.sh --table-id 42 --studio malta --camera wide
#   ./encoding_pipeline.sh --table-id 42 --studio malta --camera all --gpu 0
# =============================================================================

set -euo pipefail

# --- Configuration ---
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_DIR="/var/log/live-casino/encoding"
PID_DIR="/var/run/live-casino"
RECORDING_DIR="/mnt/recordings/live-casino"

# Ingest endpoints (primary + secondary)
PRIMARY_RTMP="rtmp://ingest-primary.livecasino.internal:1935/live-casino"
SECONDARY_RTMP="rtmp://ingest-secondary.livecasino.internal:1935/live-casino"
PRIMARY_SRT="srt://ingest-primary.livecasino.internal:9000"
SRT_PASSPHRASE="${SRT_PASSPHRASE:?set SRT_PASSPHRASE}"

# Encoding defaults
DEFAULT_RESOLUTION="1920x1080"
DEFAULT_FPS="30"
DEFAULT_BITRATE="4500k"
DEFAULT_PRESET="veryfast"
MAX_RESTART_ATTEMPTS=5
HEALTH_CHECK_INTERVAL=10

# Camera input devices (Blackmagic DeckLink)
declare -A CAMERA_DEVICES=(
    ["wide"]="/dev/video0"
    ["closeup"]="/dev/video1"
    ["overhead"]="/dev/video2"
    ["dealer"]="/dev/video3"
)

# --- Argument Parsing ---
TABLE_ID=""
STUDIO=""
CAMERA="wide"
GPU_ID=""
DRY_RUN=false

usage() {
    cat <<EOF
Usage: $(basename "$0") [OPTIONS]

Options:
  --table-id ID     Table identifier (required, e.g., 42)
  --studio NAME     Studio location (required, e.g., malta, riga, manila)
  --camera NAME     Camera angle: wide|closeup|overhead|dealer|all (default: wide)
  --gpu ID          GPU device ID for NVENC (omit for CPU encoding)
  --resolution RES  Output resolution (default: ${DEFAULT_RESOLUTION})
  --fps FPS         Frame rate (default: ${DEFAULT_FPS})
  --bitrate RATE    Video bitrate (default: ${DEFAULT_BITRATE})
  --dry-run         Print FFmpeg command without executing
  -h, --help        Show this help

Examples:
  # Single camera, GPU encoding
  $(basename "$0") --table-id 42 --studio malta --camera wide --gpu 0

  # All cameras for a table (spawns 4 encoder processes)
  $(basename "$0") --table-id 42 --studio malta --camera all --gpu 0

  # CPU-only fallback
  $(basename "$0") --table-id 42 --studio malta --camera wide
EOF
    exit 0
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --table-id)   TABLE_ID="$2"; shift 2 ;;
        --studio)     STUDIO="$2"; shift 2 ;;
        --camera)     CAMERA="$2"; shift 2 ;;
        --gpu)        GPU_ID="$2"; shift 2 ;;
        --resolution) DEFAULT_RESOLUTION="$2"; shift 2 ;;
        --fps)        DEFAULT_FPS="$2"; shift 2 ;;
        --bitrate)    DEFAULT_BITRATE="$2"; shift 2 ;;
        --dry-run)    DRY_RUN=true; shift ;;
        -h|--help)    usage ;;
        *)            echo "Unknown option: $1"; usage ;;
    esac
done

[[ -z "$TABLE_ID" ]] && { echo "ERROR: --table-id is required"; exit 1; }
[[ -z "$STUDIO" ]] && { echo "ERROR: --studio is required"; exit 1; }

# --- Initialization ---
mkdir -p "$LOG_DIR" "$PID_DIR" "$RECORDING_DIR/${STUDIO}"

STREAM_NAME="${STUDIO}_table${TABLE_ID}"
LOG_FILE="${LOG_DIR}/${STREAM_NAME}.log"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOG_FILE"
}

# --- GPU Detection ---
detect_encoder() {
    if [[ -n "$GPU_ID" ]]; then
        # Verify NVENC availability
        if ffmpeg -hide_banner -encoders 2>/dev/null | grep -q "h264_nvenc"; then
            log "INFO: Using NVENC GPU encoding on device $GPU_ID"
            echo "nvenc"
            return
        else
            log "WARN: NVENC not available, falling back to CPU (libx264)"
        fi
    fi
    log "INFO: Using CPU encoding (libx264)"
    echo "cpu"
}

# --- Build FFmpeg Command ---
build_ffmpeg_cmd() {
    local camera_name="$1"
    local device="${CAMERA_DEVICES[$camera_name]:-/dev/video0}"
    local stream_key="${STREAM_NAME}_${camera_name}"
    local encoder_type
    encoder_type=$(detect_encoder)

    local cmd="ffmpeg -hide_banner -nostdin"

    # Input: SDI/HDMI capture device
    cmd+=" -f v4l2 -input_format yuyv422 -video_size 3840x2160 -framerate 60"
    cmd+=" -thread_queue_size 1024 -i ${device}"

    # Audio input (embedded SDI audio or separate)
    cmd+=" -f alsa -channels 2 -sample_rate 48000"
    cmd+=" -thread_queue_size 512 -i hw:${camera_name}"

    # --- Video encoding ---
    if [[ "$encoder_type" == "nvenc" ]]; then
        # NVIDIA NVENC - GPU-accelerated
        cmd+=" -c:v h264_nvenc"
        cmd+=" -gpu ${GPU_ID}"
        cmd+=" -preset p4"                    # Low-latency preset
        cmd+=" -tune ll"                      # Low-latency tuning
        cmd+=" -rc cbr"                       # Constant bitrate for streaming
        cmd+=" -b:v ${DEFAULT_BITRATE}"
        cmd+=" -maxrate $(echo "${DEFAULT_BITRATE}" | sed 's/k//')000"
        cmd+=" -bufsize $(( $(echo "${DEFAULT_BITRATE}" | sed 's/k//') * 2 ))k"
        cmd+=" -profile:v high"
        cmd+=" -level 4.1"
        cmd+=" -g $(( ${DEFAULT_FPS} * 2 ))"  # GOP = 2 seconds
        cmd+=" -bf 0"                         # No B-frames (lower latency)
        cmd+=" -zerolatency 1"
    else
        # CPU libx264 fallback
        cmd+=" -c:v libx264"
        cmd+=" -preset ${DEFAULT_PRESET}"
        cmd+=" -tune zerolatency"
        cmd+=" -profile:v high"
        cmd+=" -level 4.1"
        cmd+=" -b:v ${DEFAULT_BITRATE}"
        cmd+=" -maxrate $(echo "${DEFAULT_BITRATE}" | sed 's/k//')000"
        cmd+=" -bufsize $(( $(echo "${DEFAULT_BITRATE}" | sed 's/k//') * 2 ))k"
        cmd+=" -g $(( ${DEFAULT_FPS} * 2 ))"
        cmd+=" -bf 0"
        cmd+=" -sc_threshold 0"
        cmd+=" -threads 4"
    fi

    # Scale to output resolution
    cmd+=" -vf \"scale=${DEFAULT_RESOLUTION/x/:},fps=${DEFAULT_FPS}\""

    # --- Audio encoding ---
    cmd+=" -c:a aac -b:a 128k -ar 48000 -ac 2"

    # --- Output 1: Primary RTMP ingest ---
    cmd+=" -f flv \"${PRIMARY_RTMP}/${stream_key}\""

    # --- Output 2: Secondary RTMP ingest (redundancy) ---
    cmd+=" -f flv \"${SECONDARY_RTMP}/${stream_key}\""

    # --- Output 3: SRT backup path ---
    cmd+=" -f mpegts \"${PRIMARY_SRT}?streamid=${stream_key}&passphrase=${SRT_PASSPHRASE}&latency=120000\""

    echo "$cmd"
}

# --- Health Check ---
check_encoder_health() {
    local pid="$1"
    local stream_key="$2"

    if ! kill -0 "$pid" 2>/dev/null; then
        log "ERROR: Encoder process $pid for ${stream_key} is dead"
        return 1
    fi

    # Check if output is flowing (RTMP stats endpoint)
    local stats
    stats=$(curl -s --max-time 3 "http://127.0.0.1:8888/stat" 2>/dev/null || echo "")
    if [[ -n "$stats" ]] && ! echo "$stats" | grep -q "$stream_key"; then
        log "WARN: Stream ${stream_key} not found in RTMP stats"
        return 1
    fi

    # Check FFmpeg stderr for errors
    local recent_log
    recent_log=$(tail -5 "${LOG_DIR}/${stream_key}_ffmpeg.log" 2>/dev/null || echo "")
    if echo "$recent_log" | grep -qi "error\|broken pipe\|connection refused"; then
        log "WARN: FFmpeg errors detected for ${stream_key}"
        return 1
    fi

    return 0
}

# --- Start Encoder with Auto-Restart ---
start_encoder() {
    local camera_name="$1"
    local stream_key="${STREAM_NAME}_${camera_name}"
    local pid_file="${PID_DIR}/${stream_key}.pid"
    local restart_count=0

    log "INFO: Starting encoder for ${stream_key}"

    while [[ $restart_count -lt $MAX_RESTART_ATTEMPTS ]]; do
        local ffmpeg_cmd
        ffmpeg_cmd=$(build_ffmpeg_cmd "$camera_name")

        if [[ "$DRY_RUN" == true ]]; then
            log "DRY RUN: $ffmpeg_cmd"
            return 0
        fi

        log "INFO: Executing FFmpeg (attempt $((restart_count + 1))/${MAX_RESTART_ATTEMPTS})"
        log "CMD: $ffmpeg_cmd"

        # Start FFmpeg in background
        eval "$ffmpeg_cmd" 2>>"${LOG_DIR}/${stream_key}_ffmpeg.log" &
        local pid=$!
        echo "$pid" > "$pid_file"

        log "INFO: Encoder started with PID ${pid}"

        # Send alert to monitoring
        curl -s -X POST "http://monitoring.internal:9093/api/v1/alerts" \
            -H "Content-Type: application/json" \
            -d "{\"labels\":{\"alertname\":\"encoder_started\",\"stream\":\"${stream_key}\",\"studio\":\"${STUDIO}\"},\"annotations\":{\"summary\":\"Encoder started for ${stream_key}\"}}" \
            2>/dev/null || true

        # Monitor loop
        while kill -0 "$pid" 2>/dev/null; do
            sleep "$HEALTH_CHECK_INTERVAL"

            if ! check_encoder_health "$pid" "$stream_key"; then
                log "WARN: Health check failed, stopping encoder"
                kill "$pid" 2>/dev/null || true
                wait "$pid" 2>/dev/null || true
                break
            fi
        done

        restart_count=$((restart_count + 1))
        log "WARN: Encoder exited, restart attempt ${restart_count}/${MAX_RESTART_ATTEMPTS}"

        # Alert on restart
        curl -s -X POST "http://monitoring.internal:9093/api/v1/alerts" \
            -H "Content-Type: application/json" \
            -d "{\"labels\":{\"alertname\":\"encoder_restarted\",\"stream\":\"${stream_key}\",\"studio\":\"${STUDIO}\",\"severity\":\"warning\"},\"annotations\":{\"summary\":\"Encoder restarted (${restart_count}/${MAX_RESTART_ATTEMPTS}) for ${stream_key}\"}}" \
            2>/dev/null || true

        # Brief delay before restart
        sleep 2
    done

    log "CRITICAL: Encoder for ${stream_key} exceeded max restart attempts"

    # Critical alert
    curl -s -X POST "http://monitoring.internal:9093/api/v1/alerts" \
        -H "Content-Type: application/json" \
        -d "{\"labels\":{\"alertname\":\"encoder_failed\",\"stream\":\"${stream_key}\",\"studio\":\"${STUDIO}\",\"severity\":\"critical\"},\"annotations\":{\"summary\":\"Encoder permanently failed for ${stream_key} after ${MAX_RESTART_ATTEMPTS} attempts\"}}" \
        2>/dev/null || true

    return 1
}

# --- Stop Encoder ---
stop_encoder() {
    local camera_name="$1"
    local stream_key="${STREAM_NAME}_${camera_name}"
    local pid_file="${PID_DIR}/${stream_key}.pid"

    if [[ -f "$pid_file" ]]; then
        local pid
        pid=$(cat "$pid_file")
        if kill -0 "$pid" 2>/dev/null; then
            log "INFO: Stopping encoder ${stream_key} (PID ${pid})"
            kill -SIGTERM "$pid"
            wait "$pid" 2>/dev/null || true
        fi
        rm -f "$pid_file"
    fi
}

# --- Signal Handlers ---
cleanup() {
    log "INFO: Received shutdown signal, stopping all encoders"
    if [[ "$CAMERA" == "all" ]]; then
        for cam in "${!CAMERA_DEVICES[@]}"; do
            stop_encoder "$cam"
        done
    else
        stop_encoder "$CAMERA"
    fi
    exit 0
}

trap cleanup SIGTERM SIGINT SIGHUP

# --- Main Execution ---
log "============================================"
log "Live Casino Encoding Pipeline Starting"
log "  Table: ${TABLE_ID}"
log "  Studio: ${STUDIO}"
log "  Camera: ${CAMERA}"
log "  Resolution: ${DEFAULT_RESOLUTION}"
log "  FPS: ${DEFAULT_FPS}"
log "  Bitrate: ${DEFAULT_BITRATE}"
log "  GPU: ${GPU_ID:-CPU mode}"
log "============================================"

if [[ "$CAMERA" == "all" ]]; then
    # Start all cameras for this table in parallel
    pids=()
    for cam in "${!CAMERA_DEVICES[@]}"; do
        start_encoder "$cam" &
        pids+=($!)
        log "INFO: Launched encoder for camera '${cam}'"
    done

    # Wait for all encoder processes
    for pid in "${pids[@]}"; do
        wait "$pid" || log "WARN: Encoder process $pid exited with error"
    done
else
    start_encoder "$CAMERA"
fi

log "INFO: Encoding pipeline shutdown complete"
