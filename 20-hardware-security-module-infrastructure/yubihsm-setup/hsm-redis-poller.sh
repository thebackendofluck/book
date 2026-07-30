#!/usr/bin/env bash
# Companion code for "The Backend of Luck" - Chapter 20, Hardware Security Module Infrastructure.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# hsm-redis-poller.sh
# Runs on ops-host, polls YubiHSM2 + OpenBao status every 30s,
# pushes JSON to Redis on 203.0.113.1 via SSH tunnel.
#
# Prerequisites on ops-host:
#   - yubihsm-connector running on localhost:12345
#   - bao CLI installed (OpenBao)
#   - redis-cli installed
#   - SSH tunnel open: ssh -f -N -L 16379:127.0.0.1:6381 root@203.0.113.1
#     (maps local :16379 -> production Redis :6381 via SSH)
#
# Production Redis: 203.0.113.1:6381 (new-casino-redis container)
# Redis DB: 0 (same DB as casino API — hsm: prefix provides namespacing)
# TTL: 60s per push (dashboard reads with 30s TTL, poller pushes every 30s)
#
# Usage:
#   ./hsm-redis-poller.sh            # foreground
#   nohup ./hsm-redis-poller.sh &    # background
#   systemctl start hsm-redis-poller # via systemd unit

set -euo pipefail

REDIS_HOST="127.0.0.1"
REDIS_PORT=16379        # local end of SSH tunnel to production
REDIS_DB=0              # same DB as the casino API (redis://new-redis:6379/0)
HSM_KEY="hsm:status"
HSM_TTL=60              # seconds before key expires
POLL_INTERVAL=30        # seconds between polls

CONNECTOR_URL="http://127.0.0.1:12345"
# BAO_ADDR is used via VAULT_ADDR env var in bao CLI calls below
export BAO_ADDR="${VAULT_ADDR:-http://127.0.0.1:8200}"

log() { echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] $*" >&2; }

redis_set() {
    local key="$1"
    local value="$2"
    local ttl="$3"
    redis-cli -h "$REDIS_HOST" -p "$REDIS_PORT" -n "$REDIS_DB" \
        SET "$key" "$value" EX "$ttl" > /dev/null
}

collect_device_status() {
    local status="pending"
    local serial="—"
    local firmware="—"
    local connector_status="waiting"

    if curl -sf --max-time 3 "${CONNECTOR_URL}/connector/status" > /dev/null 2>&1; then
        connector_status="ok"
        local info
        info=$(curl -sf --max-time 3 "${CONNECTOR_URL}/connector/status" 2>/dev/null || echo "{}")
        if echo "$info" | grep -q '"status"'; then
            status="connected"
            serial=$(echo "$info" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('serial_number','—'))" 2>/dev/null || echo "—")
            firmware=$(echo "$info" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('version','—'))" 2>/dev/null || echo "—")
        fi
    fi

    cat << DEVEOF
"device": {
    "status": "${status}",
    "serial": "${serial}",
    "firmware": "${firmware}",
    "connector": {"host": "127.0.0.1", "port": 12345, "status": "${connector_status}"}
}
DEVEOF
}

collect_bao_cluster() {
    local nodes_json="[]"
    local transit=false
    local pki=false
    local kv=false

    if command -v bao > /dev/null 2>&1; then
        local node_ids=("bao-01" "bao-02" "bao-03")
        local node_addrs=("10.0.10.11:8200" "10.0.10.12:8200" "10.0.10.13:8200")
        local nodes_arr=()

        for i in 0 1 2; do
            local addr="${node_addrs[$i]}"
            local nid="${node_ids[$i]}"
            local sealed=true
            local role="pending"

            local bao_status
            bao_status=$(VAULT_ADDR="http://${addr}" bao status -format=json 2>/dev/null || echo "{}")
            if echo "$bao_status" | python3 -c "import sys,json; d=json.load(sys.stdin); exit(0 if not d.get('sealed',True) else 1)" 2>/dev/null; then
                sealed=false
                role=$(echo "$bao_status" | python3 -c "import sys,json; d=json.load(sys.stdin); print('active' if not d.get('standby',True) else 'standby')" 2>/dev/null || echo "standby")
            fi

            nodes_arr+=("{\"id\":\"${nid}\",\"role\":\"${role}\",\"addr\":\"${addr}\",\"sealed\":${sealed}}")
        done

        nodes_json="[$(IFS=,; echo "${nodes_arr[*]}")]"

        # Check engines on primary (bao-01)
        if VAULT_ADDR="http://10.0.10.11:8200" bao secrets list 2>/dev/null | grep -q "transit"; then transit=true; fi
        if VAULT_ADDR="http://10.0.10.11:8200" bao secrets list 2>/dev/null | grep -q "pki"; then pki=true; fi
        if VAULT_ADDR="http://10.0.10.11:8200" bao secrets list 2>/dev/null | grep -q "kv"; then kv=true; fi
    fi

    cat << CLSEOF
"cluster": {
    "nodes": ${nodes_json},
    "engines": {"transit": ${transit}, "pki": ${pki}, "kv": ${kv}}
}
CLSEOF
}

collect_rng_health() {
    # RNG pool info — filled by actual yubihsm-shell calls once device is live
    cat << RNGEOF
"rng": {
    "pool_level": 0,
    "pool_max": 5000,
    "seeds_per_min": 0,
    "nist_tests": []
}
RNGEOF
}

collect_audit() {
    cat << AUDEOF
"audit": {
    "chain_length": 0,
    "last_checkpoint": null,
    "integrity": "pending"
}
AUDEOF
}

collect_remote_api() {
    local status="offline"
    local total_requests=0
    local encrypt_p50="null"
    local encrypt_p95="null"
    local encrypt_p99="null"
    local sign_p50="null"
    local sign_p95="null"
    local sign_p99="null"
    local error_rate=0.0
    local mtls_enabled="null"
    local active_connections=0
    local last_request="null"

    local raw
    if raw=$(curl -sf --max-time 3 "http://127.0.0.1:8190/hsm/health" 2>/dev/null); then
        status="online"
        total_requests=$(echo "$raw" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('total_requests', d.get('requests',{}).get('total',0)))" 2>/dev/null || echo "0")
        encrypt_p50=$(echo "$raw" | python3 -c "import sys,json; d=json.load(sys.stdin); lat=d.get('latency',d.get('encrypt_latency',{})); print(lat.get('p50', lat.get('encrypt_p50','null')))" 2>/dev/null || echo "null")
        encrypt_p95=$(echo "$raw" | python3 -c "import sys,json; d=json.load(sys.stdin); lat=d.get('latency',d.get('encrypt_latency',{})); print(lat.get('p95', lat.get('encrypt_p95','null')))" 2>/dev/null || echo "null")
        encrypt_p99=$(echo "$raw" | python3 -c "import sys,json; d=json.load(sys.stdin); lat=d.get('latency',d.get('encrypt_latency',{})); print(lat.get('p99', lat.get('encrypt_p99','null')))" 2>/dev/null || echo "null")
        sign_p50=$(echo "$raw" | python3 -c "import sys,json; d=json.load(sys.stdin); lat=d.get('sign_latency',{}); print(lat.get('p50','null'))" 2>/dev/null || echo "null")
        sign_p95=$(echo "$raw" | python3 -c "import sys,json; d=json.load(sys.stdin); lat=d.get('sign_latency',{}); print(lat.get('p95','null'))" 2>/dev/null || echo "null")
        sign_p99=$(echo "$raw" | python3 -c "import sys,json; d=json.load(sys.stdin); lat=d.get('sign_latency',{}); print(lat.get('p99','null'))" 2>/dev/null || echo "null")
        error_rate=$(echo "$raw" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('error_rate',0.0))" 2>/dev/null || echo "0.0")
        mtls_enabled=$(echo "$raw" | python3 -c "import sys,json; d=json.load(sys.stdin); v=d.get('mtls_enabled'); print('true' if v is True else ('false' if v is False else 'null'))" 2>/dev/null || echo "null")
        active_connections=$(echo "$raw" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('active_connections',0))" 2>/dev/null || echo "0")
        last_request=$(echo "$raw" | python3 -c "import sys,json; d=json.load(sys.stdin); v=d.get('last_request'); print('\"'+v+'\"' if v else 'null')" 2>/dev/null || echo "null")
    fi

    cat << APIEOF
"remote_api": {
    "status": "${status}",
    "total_requests": ${total_requests},
    "encrypt_latency_p50": ${encrypt_p50},
    "encrypt_latency_p95": ${encrypt_p95},
    "encrypt_latency_p99": ${encrypt_p99},
    "sign_latency_p50": ${sign_p50},
    "sign_latency_p95": ${sign_p95},
    "sign_latency_p99": ${sign_p99},
    "error_rate": ${error_rate},
    "mtls_enabled": ${mtls_enabled},
    "active_connections": ${active_connections},
    "last_request": ${last_request}
}
APIEOF
}

build_payload() {
    local now
    now=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

    local device_json
    device_json=$(collect_device_status)

    local cluster_json
    cluster_json=$(collect_bao_cluster)

    local rng_json
    rng_json=$(collect_rng_health)

    local audit_json
    audit_json=$(collect_audit)

    local remote_api_json
    remote_api_json=$(collect_remote_api)

    cat << PAYEOF
{
    ${device_json},
    ${cluster_json},
    "keys": {
        "epoch_id": "—",
        "epoch_expires": null,
        "derived_keys": [],
        "rotation_history": []
    },
    ${rng_json},
    ${audit_json},
    "luks": {"volumes": []},
    "compliance": {
        "pci_dss": {"score": 0, "total": 100},
        "gli_19": {"score": 0, "total": 100},
        "iso_27001": {"score": 0, "total": 100},
        "gdpr": {"score": 0, "total": 100}
    },
    ${remote_api_json},
    "last_updated": "${now}",
    "source": "poller"
}
PAYEOF
}

# Ensure SSH tunnel to production Redis is open
ensure_tunnel() {
    if ! redis-cli -h "$REDIS_HOST" -p "$REDIS_PORT" -n "$REDIS_DB" PING > /dev/null 2>&1; then
        log "SSH tunnel to production Redis not available — starting tunnel..."
        ssh -f -N \
            -o StrictHostKeyChecking=accept-new \
            -o ServerAliveInterval=30 \
            -o ExitOnForwardFailure=yes \
            -L "${REDIS_PORT}:127.0.0.1:6381" \
            root@203.0.113.1 2>/dev/null || true
        sleep 2
        if ! redis-cli -h "$REDIS_HOST" -p "$REDIS_PORT" -n "$REDIS_DB" PING > /dev/null 2>&1; then
            log "WARNING: Redis tunnel unavailable, will retry next cycle"
            return 1
        fi
        log "SSH tunnel established"
    fi
    return 0
}

log "HSM Redis poller starting (interval: ${POLL_INTERVAL}s, target: ${REDIS_HOST}:${REDIS_PORT}/db${REDIS_DB})"

while true; do
    if ensure_tunnel; then
        payload=$(build_payload 2>/dev/null) || { log "ERROR building payload"; sleep "$POLL_INTERVAL"; continue; }

        # Validate JSON before pushing
        if echo "$payload" | python3 -c "import sys,json; json.load(sys.stdin)" > /dev/null 2>&1; then
            redis_set "$HSM_KEY" "$payload" "$HSM_TTL"
            log "Pushed hsm:status to Redis (${#payload} bytes)"
        else
            log "ERROR: generated payload is not valid JSON, skipping push"
        fi
    fi

    sleep "$POLL_INTERVAL"
done
