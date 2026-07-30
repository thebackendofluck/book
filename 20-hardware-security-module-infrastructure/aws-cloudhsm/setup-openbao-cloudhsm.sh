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

# setup-openbao-cloudhsm.sh
# Install OpenBao 2.2+ (CGO/PKCS#11 build), configure auto-unseal via
# AWS CloudHSM PKCS#11, initialise a 3-node Raft cluster, and enable
# Transit, PKI, KV, and audit engines.
#
# This script is the CloudHSM equivalent of:
#   ../yubihsm-setup/setup-openbao-cluster.sh
#
# Prerequisites:
#   - CloudHSM cluster ACTIVE and initialised (run setup-cloudhsm-cluster.sh first)
#   - CloudHSM Client SDK 5.11+ installed
#   - CA certificate at /opt/cloudhsm/etc/customerCA.crt
#   - cloudhsm-client daemon running
#
# Usage (run on each OpenBao node):
#   NODE_ID=bao-01 NODE_IP=10.0.1.10 CLUSTER_ID=cluster-xxx \
#   CLOUDHSM_PIN=hsm-app:<password> \
#   BAO_01_IP=10.0.1.10 BAO_02_IP=10.0.2.10 BAO_03_IP=10.0.3.10 \
#   bash setup-openbao-cloudhsm.sh [--init]
#
# Pass --init on bao-01 only, after all nodes are running.
#
# Compliance: PCI DSS v4.0.1 Req. 3.6/3.7, ISO 27001 A.8.24, FIPS 140-2 L3

set -euo pipefail

# ── Configuration ──────────────────────────────────────────────────────────────
NODE_ID="${NODE_ID:-bao-01}"
NODE_IP="${NODE_IP:-}"
CLUSTER_ID="${CLUSTER_ID:-}"
CLOUDHSM_PIN="${CLOUDHSM_PIN:-}" # format: hsm-app:<password>

BAO_PORT="8200"
BAO_CLUSTER_PORT="8201"
BAO_DIR="/opt/openbao"
BAO_DATA_DIR="${BAO_DIR}/data"
BAO_TLS_DIR="${BAO_DIR}/tls"
BAO_CONFIG_DIR="${BAO_DIR}/config"
BAO_LOG_DIR="/var/log/openbao"
LOG_FILE="${BAO_LOG_DIR}/openbao-setup-$(date +%Y%m%d-%H%M%S).log"

PKCS11_LIB="/opt/cloudhsm/lib/libcloudhsm_pkcs11.so"

# Peer IPs for Raft cluster
BAO_01_IP="${BAO_01_IP:-}"
BAO_02_IP="${BAO_02_IP:-}"
BAO_03_IP="${BAO_03_IP:-}"

DO_INIT=0

# ── Argument parsing ───────────────────────────────────────────────────────────
for arg in "$@"; do
    case "${arg}" in
        --init) DO_INIT=1 ;;
        *) echo "Unknown argument: ${arg}"; exit 1 ;;
    esac
done

# ── Logging ────────────────────────────────────────────────────────────────────
mkdir -p "${BAO_LOG_DIR}"
log()  { echo "[$(date -Is)] INFO  $*" | tee -a "${LOG_FILE}"; }
warn() { echo "[$(date -Is)] WARN  $*" | tee -a "${LOG_FILE}"; }
die()  { echo "[$(date -Is)] ERROR $*" | tee -a "${LOG_FILE}"; exit 1; }
sep()  { echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━" | tee -a "${LOG_FILE}"; }

# ── Preflight ──────────────────────────────────────────────────────────────────
preflight() {
    sep
    log "=== Preflight (node: ${NODE_ID}) ==="
    [[ $EUID -eq 0 ]] || die "Must run as root"
    [[ -n "${CLOUDHSM_PIN}" ]] || die "CLOUDHSM_PIN not set (format: hsm-app:<password>)"
    [[ -n "${NODE_IP}"      ]] || die "NODE_IP not set"
    [[ -n "${CLUSTER_ID}"   ]] || warn "CLUSTER_ID not set — CloudHSM auto-discover will be used"

    # Verify PKCS#11 library
    [[ -f "${PKCS11_LIB}" ]] || die "PKCS#11 library not found: ${PKCS11_LIB}\nInstall CloudHSM Client SDK 5.11+"

    # Verify cloudhsm-client daemon
    if ! systemctl is-active --quiet cloudhsm-client; then
        warn "cloudhsm-client daemon not running — attempting start"
        systemctl start cloudhsm-client || die "Failed to start cloudhsm-client"
    fi
    log "cloudhsm-client daemon: active"

    # Check SDK version for Ed25519 support
    local SDK_VER
    SDK_VER=$(dpkg -l cloudhsm-client 2>/dev/null | awk '/cloudhsm-client/{print $3}' | head -1 || echo "unknown")
    log "CloudHSM Client SDK: ${SDK_VER}"

    command -v openssl >/dev/null 2>&1 || apt-get install -y openssl
    command -v curl    >/dev/null 2>&1 || apt-get install -y curl
    command -v jq      >/dev/null 2>&1 || apt-get install -y jq

    log "Preflight: OK"
}

# ── Install OpenBao (CGO build with PKCS#11 support) ──────────────────────────
install_openbao() {
    sep
    log "=== Installing OpenBao (CGO build) ==="

    if command -v bao &>/dev/null; then
        local VER
        VER=$(bao version 2>/dev/null || echo "unknown")
        log "OpenBao already installed: ${VER}"
        if echo "${VER}" | grep -q "(cgo)"; then
            log "CGO build confirmed — skipping reinstall"
            return 0
        else
            warn "Installed build is NOT CGO — reinstalling openbao-hsm package"
        fi
    fi

    # Add OpenBao APT repository (Ubuntu/Debian)
    local CODENAME
    CODENAME=$(lsb_release -cs 2>/dev/null || echo "jammy")

    wget -qO- https://apt.releases.openbao.org/gpg/openbao.gpg \
        | gpg --dearmor \
        -o /usr/share/keyrings/openbao-archive-keyring.gpg

    echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/openbao-archive-keyring.gpg] \
https://apt.releases.openbao.org ${CODENAME} main" \
        > /etc/apt/sources.list.d/openbao.list

    apt-get update -qq
    # openbao-hsm is the CGO variant with PKCS#11 support
    apt-get install -y openbao-hsm || apt-get install -y openbao

    local VER
    VER=$(bao version 2>/dev/null || echo "unknown")
    log "Installed: ${VER}"

    if ! echo "${VER}" | grep -q "(cgo)"; then
        warn "WARNING: Installed OpenBao build does not include '(cgo)' — PKCS#11 seal may not work"
        warn "The openbao-hsm package is required for PKCS#11 support"
    fi
}

# ── Create directories and user ────────────────────────────────────────────────
create_directories() {
    sep
    log "=== Creating OpenBao directories and user ==="

    id -u openbao &>/dev/null || useradd -r -m -d "${BAO_DIR}" -s /usr/sbin/nologin openbao

    mkdir -p "${BAO_DATA_DIR}" "${BAO_TLS_DIR}" "${BAO_CONFIG_DIR}" "${BAO_LOG_DIR}"
    chown -R openbao:openbao "${BAO_DIR}" "${BAO_LOG_DIR}"
    chmod 750 "${BAO_DATA_DIR}" "${BAO_TLS_DIR}" "${BAO_CONFIG_DIR}"
    log "Directories created"
}

# ── Generate TLS certificates for this node ───────────────────────────────────
generate_tls() {
    sep
    log "=== Generating TLS certificates for ${NODE_ID} ==="

    local CA_KEY="${BAO_TLS_DIR}/ca.key"
    local CA_CERT="${BAO_TLS_DIR}/ca.crt"
    local NODE_KEY="${BAO_TLS_DIR}/${NODE_ID}.key"
    local NODE_CERT="${BAO_TLS_DIR}/${NODE_ID}.crt"

    # On bao-01: generate cluster CA
    if [[ "${NODE_ID}" == "bao-01" ]] && [[ ! -f "${CA_CERT}" ]]; then
        log "Generating OpenBao cluster CA (bao-01 only)"
        openssl genrsa -out "${CA_KEY}" 4096
        openssl req -new -x509 -days 3650 \
            -key "${CA_KEY}" \
            -out "${CA_CERT}" \
            -subj "/CN=OpenBao Cluster CA/O=iGaming Platform/C=GB"
        chmod 600 "${CA_KEY}"
        log "CA generated — distribute ${CA_CERT} to bao-02 and bao-03 before running on them"
    fi

    [[ -f "${CA_CERT}" ]] || die "CA certificate not found: ${CA_CERT}\nCopy from bao-01 before running on peer nodes"

    # Generate node key and CSR
    if [[ ! -f "${NODE_KEY}" ]]; then
        openssl genrsa -out "${NODE_KEY}" 4096
        chmod 600 "${NODE_KEY}"
    fi

    # Generate CSR with SAN for IP and hostname
    local SAN="IP:${NODE_IP},DNS:${NODE_ID},DNS:localhost"
    [[ -n "${BAO_01_IP}" ]] && SAN="${SAN},IP:${BAO_01_IP}"
    [[ -n "${BAO_02_IP}" ]] && SAN="${SAN},IP:${BAO_02_IP}"
    [[ -n "${BAO_03_IP}" ]] && SAN="${SAN},IP:${BAO_03_IP}"

    openssl req -new \
        -key "${NODE_KEY}" \
        -out "${WORK_DIR:-/tmp}/${NODE_ID}.csr" \
        -subj "/CN=${NODE_ID}/O=iGaming OpenBao Cluster/C=GB"

    openssl x509 -req -days 3650 \
        -in "${WORK_DIR:-/tmp}/${NODE_ID}.csr" \
        -CA "${CA_CERT}" \
        -CAkey "${CA_KEY}" \
        -CAcreateserial \
        -out "${NODE_CERT}" \
        -extfile <(echo "subjectAltName=${SAN}")

    chown openbao:openbao "${NODE_KEY}" "${NODE_CERT}"
    log "TLS certificate generated: ${NODE_CERT}"
}

# ── Write OpenBao configuration ────────────────────────────────────────────────
write_config() {
    sep
    log "=== Writing OpenBao configuration ==="

    local CONFIG_FILE="${BAO_CONFIG_DIR}/openbao.hcl"

    # Determine peer IPs
    local PEER_01="${BAO_01_IP:-10.0.1.10}"
    local PEER_02="${BAO_02_IP:-10.0.2.10}"
    local PEER_03="${BAO_03_IP:-10.0.3.10}"

    cat > "${CONFIG_FILE}" << EOF
# openbao.hcl — OpenBao server configuration
# Node: ${NODE_ID} (CloudHSM PKCS#11 seal)
# Generated by setup-openbao-cloudhsm.sh
#
# Compliance: PCI DSS v4.0.1 Req. 3.5/3.6/3.7, FIPS 140-2 Level 3

ui            = true
disable_mlock = false
log_level     = "info"
log_format    = "json"

storage "raft" {
  path    = "${BAO_DATA_DIR}"
  node_id = "${NODE_ID}"

  retry_join {
    leader_api_addr         = "https://${PEER_01}:${BAO_PORT}"
    leader_ca_cert_file     = "${BAO_TLS_DIR}/ca.crt"
    leader_client_cert_file = "${BAO_TLS_DIR}/${NODE_ID}.crt"
    leader_client_key_file  = "${BAO_TLS_DIR}/${NODE_ID}.key"
  }
  retry_join {
    leader_api_addr         = "https://${PEER_02}:${BAO_PORT}"
    leader_ca_cert_file     = "${BAO_TLS_DIR}/ca.crt"
    leader_client_cert_file = "${BAO_TLS_DIR}/${NODE_ID}.crt"
    leader_client_key_file  = "${BAO_TLS_DIR}/${NODE_ID}.key"
  }
  retry_join {
    leader_api_addr         = "https://${PEER_03}:${BAO_PORT}"
    leader_ca_cert_file     = "${BAO_TLS_DIR}/ca.crt"
    leader_client_cert_file = "${BAO_TLS_DIR}/${NODE_ID}.crt"
    leader_client_key_file  = "${BAO_TLS_DIR}/${NODE_ID}.key"
  }

  performance_multiplier = 1
}

listener "tcp" {
  address            = "0.0.0.0:${BAO_PORT}"
  tls_cert_file      = "${BAO_TLS_DIR}/${NODE_ID}.crt"
  tls_key_file       = "${BAO_TLS_DIR}/${NODE_ID}.key"
  tls_client_ca_cert = "${BAO_TLS_DIR}/ca.crt"
  tls_min_version    = "tls13"
}

# PKCS#11 auto-unseal via AWS CloudHSM
# PIN injected via: /etc/systemd/system/openbao.service.d/hsm.conf
# Key label must match what was generated by setup-cloudhsm-cluster.sh
seal "pkcs11" {
  lib        = "${PKCS11_LIB}"
  slot       = "1"
  pin        = "env:CLOUDHSM_PIN"
  key_label  = "wrap-key-aes256"
  mechanism  = "0x00001085"    # CKM_AES_KEY_WRAP_PAD
  generate_key = "false"       # Key was pre-generated by setup-cloudhsm-cluster.sh
}

api_addr     = "https://${NODE_IP}:${BAO_PORT}"
cluster_addr = "https://${NODE_IP}:${BAO_CLUSTER_PORT}"

telemetry {
  prometheus_retention_time = "60s"
  disable_hostname          = false
}
EOF

    # Fix the typo we introduced (BAT_TLS_DIR should be BAO_TLS_DIR — written literally above)
    sed -i "s|BAT_TLS_DIR|BAO_TLS_DIR|g" "${CONFIG_FILE}"

    chown openbao:openbao "${CONFIG_FILE}"
    chmod 640 "${CONFIG_FILE}"
    log "Configuration written: ${CONFIG_FILE}"
}

# ── Install systemd service ────────────────────────────────────────────────────
install_service() {
    sep
    log "=== Installing OpenBao systemd service ==="

    cat > /etc/systemd/system/openbao.service << 'SYSTEMD'
[Unit]
Description=OpenBao Secret Management Service
Documentation=https://openbao.org/docs/
Requires=network-online.target cloudhsm-client.service
After=network-online.target cloudhsm-client.service

[Service]
User=openbao
Group=openbao
ProtectSystem=full
ProtectHome=read-only
PrivateTmp=yes
PrivateDevices=yes
SecureBits=keep-caps
AmbientCapabilities=CAP_IPC_LOCK
CapabilityBoundingSet=CAP_SYSLOG CAP_IPC_LOCK
NoNewPrivileges=yes
ExecStart=/usr/bin/bao server -config=/opt/openbao/config/openbao.hcl
ExecReload=/bin/kill --signal HUP $MAINPID
KillMode=process
KillSignal=SIGINT
Restart=on-failure
RestartSec=5
TimeoutStopSec=30
StartLimitInterval=60
StartLimitBurst=3
LimitNOFILE=65536
LimitMEMLOCK=infinity
StandardOutput=journal
StandardError=journal
SyslogIdentifier=openbao

[Install]
WantedBy=multi-user.target
SYSTEMD

    # Inject CloudHSM PIN via secure drop-in (never in the main config file)
    mkdir -p /etc/systemd/system/openbao.service.d
    cat > /etc/systemd/system/openbao.service.d/hsm.conf << EOF
[Service]
Environment="CLOUDHSM_PIN=${CLOUDHSM_PIN}"
EOF
    chmod 600 /etc/systemd/system/openbao.service.d/hsm.conf
    chown root:root /etc/systemd/system/openbao.service.d/hsm.conf

    systemctl daemon-reload
    systemctl enable openbao
    log "Systemd service installed (hsm.conf mode 600)"
}

# ── Start OpenBao ──────────────────────────────────────────────────────────────
start_openbao() {
    sep
    log "=== Starting OpenBao ==="
    systemctl start openbao

    log "Waiting for OpenBao to be ready..."
    local ATTEMPTS=0
    while true; do
        if curl -sf --cacert "${BAO_TLS_DIR}/ca.crt" \
                "https://${NODE_IP}:${BAO_PORT}/v1/sys/health" &>/dev/null; then
            break
        fi
        (( ATTEMPTS++ ))
        [[ "${ATTEMPTS}" -gt 30 ]] && die "OpenBao did not become ready within 60 seconds"
        sleep 2
    done
    log "OpenBao is accepting requests on port ${BAO_PORT}"
}

# ── Initialise the OpenBao cluster (bao-01 only, with --init) ─────────────────
init_cluster() {
    sep
    log "=== Initialising OpenBao cluster ==="

    local INIT_FILE="${BAO_DIR}/init-output.json"

    export BAO_ADDR="https://${NODE_IP}:${BAO_PORT}"
    export BAO_CACERT="${BAO_TLS_DIR}/ca.crt"

    # Check if already initialised
    if bao status 2>/dev/null | grep -q "Initialized.*true"; then
        log "OpenBao already initialised — skipping"
        return 0
    fi

    # Initialise with recovery key shares (CloudHSM handles auto-unseal)
    bao operator init \
        -recovery-shares=5 \
        -recovery-threshold=3 \
        -format=json > "${INIT_FILE}"

    chmod 400 "${INIT_FILE}"
    chown root:root "${INIT_FILE}"

    log "IMPORTANT: Recovery keys written to ${INIT_FILE}"
    log "Distribute recovery key shards to separate custodians immediately"
    log "Then delete ${INIT_FILE} from this host"

    # Extract root token for engine configuration
    local ROOT_TOKEN
    ROOT_TOKEN=$(jq -r .root_token "${INIT_FILE}")
    export BAO_TOKEN="${ROOT_TOKEN}"

    log "Cluster initialised successfully"
}

# ── Enable platform engines ────────────────────────────────────────────────────
enable_engines() {
    sep
    log "=== Enabling platform engines ==="

    export BAO_ADDR="https://${NODE_IP}:${BAO_PORT}"
    export BAO_CACERT="${BAO_TLS_DIR}/ca.crt"

    [[ -n "${BAO_TOKEN:-}" ]] || die "BAO_TOKEN not set — run --init first, then set BAO_TOKEN"

    # Enable audit device first (PCI DSS Req. 10.2)
    bao audit enable file path="${BAO_LOG_DIR}/audit.log" \
        log_raw=false \
        hmac_accessor=true \
        || warn "Audit device may already be enabled"
    log "Audit logging enabled: ${BAO_LOG_DIR}/audit.log"

    # Transit engine — DEK management
    bao secrets enable -path=transit transit \
        || warn "Transit engine may already be enabled"
    bao write transit/keys/player-pii     type=aes256-gcm96
    bao write transit/keys/game-session   type=aes256-gcm96
    bao write transit/keys/audit-log      type=aes256-gcm96
    bao write transit/keys/luks-volumes   type=aes256-gcm96
    log "Transit engine enabled with keys: player-pii, game-session, audit-log, luks-volumes"

    # PKI engine — mTLS certificates
    bao secrets enable pki \
        || warn "PKI engine may already be enabled"
    bao secrets tune -max-lease-ttl=87600h pki
    bao write pki/root/generate/internal \
        common_name="iGaming Platform Root CA" \
        ttl=87600h \
        key_type=ec \
        key_bits=256
    log "PKI engine enabled"

    # KV v2 — non-cryptographic secrets
    bao secrets enable -path=secret kv-v2 \
        || warn "KV engine may already be enabled"
    log "KV v2 engine enabled"

    # AppRole auth — for platform services
    bao auth enable approle \
        || warn "AppRole auth may already be enabled"

    # Create AppRole for platform services
    bao write auth/approle/role/platform-services \
        token_ttl=1h \
        token_max_ttl=4h \
        token_policies=platform-transit-read \
        bind_secret_id=true

    log "AppRole auth enabled with role: platform-services"
    log "Engines enabled successfully"
}

# ── Main ───────────────────────────────────────────────────────────────────────
main() {
    log "OpenBao CloudHSM Setup — iGaming Platform"
    log "Node: ${NODE_ID} | Start: $(date -Is)"

    preflight
    install_openbao
    create_directories
    generate_tls
    write_config
    install_service
    start_openbao

    if [[ "${DO_INIT}" -eq 1 ]]; then
        init_cluster
        enable_engines
        sep
        log "=== bao-01 fully initialised ==="
        log "Status: $(bao status -format=json 2>/dev/null | jq -r .)"
    else
        log "Node ${NODE_ID} is running — waiting for cluster join"
        log "Once bao-01 is initialised (--init), this node will join automatically via Raft retry_join"
    fi

    sep
    log "Setup complete for ${NODE_ID}"
    log "PKCS#11 library: ${PKCS11_LIB}"
    log "OpenBao status:  bao status --address=https://${NODE_IP}:${BAO_PORT} --ca-cert=${BAO_TLS_DIR}/ca.crt"
}

main "$@"
