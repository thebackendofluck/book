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

# shellcheck disable=SC2034  # Config and color constants
# setup-openbao-cluster.sh
# Install OpenBao 2.2+ (CGO/PKCS#11 build), configure 3-node Raft cluster,
# configure PKCS#11 auto-unseal via YubiHSM 2, enable Transit/PKI/KV/Audit.
#
# Target: ops-host hypervisor — bao-01 (10.0.0.11), bao-02, bao-03
# Run this script on EACH node, adjusting NODE_ID and NODE_IP accordingly.
#
# Usage:
#   NODE_ID=bao-01 NODE_IP=10.0.10.11 BAO_HSM_PIN=<pin> bash setup-openbao-cluster.sh
#   NODE_ID=bao-02 NODE_IP=10.0.10.12 BAO_HSM_PIN=<pin> bash setup-openbao-cluster.sh
#   NODE_ID=bao-03 NODE_IP=10.0.10.13 BAO_HSM_PIN=<pin> bash setup-openbao-cluster.sh
#
# After all nodes are running, run with --init on bao-01 to initialize the cluster.
#
# Compliance: PCI DSS Req. 3.6/3.7, ISO 27001 A.10.1.2, FIPS 140-2 L3

set -euo pipefail

# ── Configuration ──────────────────────────────────────────────────────────────
NODE_ID="${NODE_ID:-bao-01}"
NODE_IP="${NODE_IP:-10.0.10.11}"
BAO_PORT="8200"
BAO_CLUSTER_PORT="8201"
BAO_DIR="/opt/openbao"
BAO_DATA_DIR="${BAO_DIR}/data"
BAO_TLS_DIR="${BAO_DIR}/tls"
BAO_CONFIG_DIR="${BAO_DIR}/config"
LOG_FILE="/var/log/openbao-setup.log"
PKCS11_LIB="/usr/lib/x86_64-linux-gnu/pkcs11/yubihsm_pkcs11.so"
YUBIHSM_CONF="/etc/yubihsm_pkcs11.conf"

# Where `bao operator init` output lands. Mounted as a private tmpfs by
# mount_init_tmpfs() so the recovery shares and the bootstrap root token never
# reach a persistent filesystem. See that function for why this is not /tmp and
# why the old `shred -u` advice was not a remedy.
BAO_INIT_DIR="${BAO_INIT_DIR:-/run/openbao-init}"

# Peer node IPs for Raft cluster (adjust to your network)
BAO_01_IP="${BAO_01_IP:-10.0.10.11}"
BAO_02_IP="${BAO_02_IP:-10.0.10.12}"
BAO_03_IP="${BAO_03_IP:-10.0.10.13}"

# ── Logging helpers ────────────────────────────────────────────────────────────
log()  { echo "[$(date -Is)] INFO  $*" | tee -a "$LOG_FILE"; }
warn() { echo "[$(date -Is)] WARN  $*" | tee -a "$LOG_FILE"; }
die()  { echo "[$(date -Is)] ERROR $*" | tee -a "$LOG_FILE"; exit 1; }

# ── Preflight ──────────────────────────────────────────────────────────────────
preflight() {
    log "=== Preflight checks (node: ${NODE_ID}) ==="
    [[ $EUID -eq 0 ]] || die "Must run as root"
    [[ -n "${BAO_HSM_PIN:-}" ]] || die "BAO_HSM_PIN not set. Export before running."
    [[ "$BAO_HSM_PIN" != "0001password" ]] || \
        warn "Using default YubiHSM PIN — change before production!"
    command -v openssl >/dev/null 2>&1 || apt-get install -y openssl
    command -v curl    >/dev/null 2>&1 || apt-get install -y curl
}

# ── Install OpenBao with CGO (PKCS#11 requires CGO build) ─────────────────────
install_openbao() {
    log "=== Installing OpenBao (CGO build with PKCS#11 support) ==="

    if command -v bao &>/dev/null; then
        local ver
        ver=$(bao version 2>/dev/null || echo "unknown")
        log "OpenBao already installed: $ver"
        if echo "$ver" | grep -q "(cgo)"; then
            log "CGO build confirmed — skipping reinstall"
            return 0
        else
            warn "Installed build is NOT CGO — reinstalling openbao-hsm package"
        fi
    fi

    # Add OpenBao APT repository
    wget -O- https://apt.releases.openbao.org/gpg/openbao.gpg \
        | gpg --dearmor \
        -o /usr/share/keyrings/openbao-archive-keyring.gpg

    local CODENAME
    CODENAME=$(lsb_release -cs 2>/dev/null || echo "noble")

    echo "deb [arch=$(dpkg --print-architecture) \
        signed-by=/usr/share/keyrings/openbao-archive-keyring.gpg] \
        https://apt.releases.openbao.org ${CODENAME} main" \
        | tee /etc/apt/sources.list.d/openbao.list

    apt-get update -qq
    # openbao-hsm = build with CGO, mandatory for PKCS#11 seal
    apt-get install -y openbao-hsm

    local ver
    ver=$(bao version 2>/dev/null)
    log "Installed: $ver"
    echo "$ver" | grep -q "(cgo)" || die "Installed build lacks CGO — PKCS#11 seal will not work"
}

# ── Create directories and service user ───────────────────────────────────────
setup_directories() {
    log "=== Setting up directories ==="

    id openbao &>/dev/null || \
        useradd --system --home "${BAO_DIR}" --shell /usr/sbin/nologin openbao

    mkdir -p "${BAO_DATA_DIR}" "${BAO_TLS_DIR}" "${BAO_CONFIG_DIR}"
    mkdir -p /var/log/openbao
    chown -R openbao:openbao "${BAO_DIR}" /var/log/openbao
    chmod 750 "${BAO_DATA_DIR}" "${BAO_TLS_DIR}"
}

# ── Generate TLS certificates (internal PKI) ──────────────────────────────────
generate_tls() {
    log "=== Generating TLS certificates ==="

    # Only generate CA on bao-01 if it doesn't exist
    if [[ "${NODE_ID}" == "bao-01" ]] && [[ ! -f "${BAO_TLS_DIR}/ca.crt" ]]; then
        log "Generating internal CA (bao-01 only)"
        openssl genrsa -out "${BAO_TLS_DIR}/ca.key" 4096
        openssl req -new -x509 -days 3650 \
            -key "${BAO_TLS_DIR}/ca.key" \
            -out "${BAO_TLS_DIR}/ca.crt" \
            -subj "/CN=OpenBao Internal CA/O=iGaming Platform"
        chmod 600 "${BAO_TLS_DIR}/ca.key"
        log "CA certificate generated at ${BAO_TLS_DIR}/ca.crt"
        log "Distribute ca.crt to bao-02 and bao-03 via secure channel"
    fi

    [[ -f "${BAO_TLS_DIR}/ca.crt" ]] || die "ca.crt not found — copy from bao-01 first"

    # Generate node certificate if not present
    if [[ ! -f "${BAO_TLS_DIR}/${NODE_ID}.crt" ]]; then
        log "Generating certificate for ${NODE_ID}"
        openssl genrsa -out "${BAO_TLS_DIR}/${NODE_ID}.key" 2048
        openssl req -new \
            -key "${BAO_TLS_DIR}/${NODE_ID}.key" \
            -out "${BAO_TLS_DIR}/${NODE_ID}.csr" \
            -subj "/CN=${NODE_ID}/O=OpenBao Cluster"

        # SAN: include hostname and IP.
        # mktemp, not a predictable /tmp path: this runs as root in a
        # world-writable directory, so any local user could pre-create
        # /tmp/extfile-bao-01.cnf as a symlink and have root overwrite the
        # target, or swap the file between write and read to control the SAN
        # this CA signs.
        local extfile
        extfile=$(mktemp "/tmp/extfile-${NODE_ID}.XXXXXXXX.cnf")
        cat > "$extfile" << EOF
subjectAltName=DNS:${NODE_ID},IP:${NODE_IP},IP:127.0.0.1
EOF
        openssl x509 -req -days 825 \
            -in "${BAO_TLS_DIR}/${NODE_ID}.csr" \
            -CA "${BAO_TLS_DIR}/ca.crt" \
            -CAkey "${BAO_TLS_DIR}/ca.key" \
            -CAcreateserial \
            -extfile "$extfile" \
            -out "${BAO_TLS_DIR}/${NODE_ID}.crt"

        rm -f "$extfile" "${BAO_TLS_DIR}/${NODE_ID}.csr"
        chmod 600 "${BAO_TLS_DIR}/${NODE_ID}.key"
        log "Certificate generated: ${BAO_TLS_DIR}/${NODE_ID}.crt"
    else
        log "Certificate for ${NODE_ID} already exists"
    fi

    chown -R openbao:openbao "${BAO_TLS_DIR}"
}

# ── Write OpenBao HCL config ──────────────────────────────────────────────────
write_config() {
    log "=== Writing OpenBao configuration for ${NODE_ID} ==="

    local config_file="${BAO_CONFIG_DIR}/openbao.hcl"

    # Build retry_join blocks for all OTHER nodes
    local retry_joins=""
    for peer_id in bao-01 bao-02 bao-03; do
        [[ "$peer_id" == "$NODE_ID" ]] && continue
        local peer_ip_var
        peer_ip_var="$(echo "${peer_id}" | tr '-' '_' | tr '[:lower:]' '[:upper:]')_IP"
        local peer_ip="${!peer_ip_var:-}"
        retry_joins+="  retry_join {\n"
        retry_joins+="    leader_api_addr = \"https://${peer_id}:8200\"\n"
        retry_joins+="    leader_ca_cert_file     = \"${BAO_TLS_DIR}/ca.crt\"\n"
        retry_joins+="    leader_client_cert_file = \"${BAO_TLS_DIR}/${NODE_ID}.crt\"\n"
        retry_joins+="    leader_client_key_file  = \"${BAO_TLS_DIR}/${NODE_ID}.key\"\n"
        retry_joins+="  }\n"
    done

    cat > "$config_file" << HCLEOF
# OpenBao configuration — ${NODE_ID}
# Generated by setup-openbao-cluster.sh
# PCI DSS Req. 3.6/3.7 · FIPS 140-2 L3

ui = true
disable_mlock = false          # keep false in production (prevents swap)
log_level    = "info"
log_format   = "json"

# Raft integrated storage — no Consul dependency
storage "raft" {
  path    = "${BAO_DATA_DIR}"
  node_id = "${NODE_ID}"

$(printf "%b" "$retry_joins")}

# TLS listener — mTLS mandatory for cluster communication
listener "tcp" {
  address     = "0.0.0.0:${BAO_PORT}"
  tls_cert_file      = "${BAO_TLS_DIR}/${NODE_ID}.crt"
  tls_key_file       = "${BAO_TLS_DIR}/${NODE_ID}.key"
  tls_client_ca_cert = "${BAO_TLS_DIR}/ca.crt"
  # Enable mTLS (require client cert) when VMs have PKI-issued certs:
  # tls_require_and_verify_client_cert = true
}

# PKCS#11 auto-unseal via YubiHSM 2 — OpenBao 2.2+ OSS, no license cost
# PIN provided via BAO_HSM_PIN environment variable (see systemd override)
seal "pkcs11" {
  lib        = "${PKCS11_LIB}"
  slot       = "0"
  key_label  = "bao-root-key-aes"
  mechanism  = "0x1087"   # CKM_AES_GCM (256-bit)
  # PIN: never in this file — see /etc/systemd/system/openbao.service.d/hsm.conf
}

api_addr     = "https://${NODE_ID}:${BAO_PORT}"
cluster_addr = "https://${NODE_ID}:${BAO_CLUSTER_PORT}"
HCLEOF

    chmod 640 "$config_file"
    chown openbao:openbao "$config_file"
    log "Config written to $config_file"
}

# ── Write systemd service override (PIN via env, never in HCL) ───────────────
write_systemd_override() {
    log "=== Writing systemd HSM environment override ==="

    local override_dir="/etc/systemd/system/openbao.service.d"
    mkdir -p "$override_dir"

    cat > "${override_dir}/hsm.conf" << EOF
[Service]
# PIN provided via environment — NEVER put PIN in openbao.hcl
# PCI DSS Req. 3.5: protect cryptographic keys from unauthorized disclosure
Environment="BAO_HSM_PIN=${BAO_HSM_PIN}"
Environment="YUBIHSM_PKCS11_CONF=${YUBIHSM_CONF}"

# Hardening
PrivateTmp=yes
ProtectSystem=strict
ProtectHome=yes
ReadWritePaths=${BAO_DIR} /var/log/openbao
NoNewPrivileges=yes
LimitNOFILE=65536
EOF

    # Restrict access — contains the HSM PIN
    chmod 600 "${override_dir}/hsm.conf"
    chown root:root "${override_dir}/hsm.conf"
    log "Systemd override written: ${override_dir}/hsm.conf (chmod 600)"
}

# ── Enable and start service ──────────────────────────────────────────────────
start_service() {
    log "=== Starting OpenBao service ==="
    systemctl daemon-reload
    systemctl enable openbao
    systemctl restart openbao

    # Wait for service to be listening
    local i=0
    until curl -sf --cacert "${BAO_TLS_DIR}/ca.crt" \
              "https://${NODE_ID}:${BAO_PORT}/v1/sys/health" &>/dev/null; do
        sleep 2
        (( i++ ))
        [[ $i -lt 30 ]] || die "OpenBao did not start within 60s"
    done
    log "OpenBao responding at https://${NODE_ID}:${BAO_PORT}"
}

# ── Private tmpfs for init material ───────────────────────────────────────────
# The init output contains five recovery shares and the bootstrap root token.
# It used to go to /tmp/bao-init.json via `tee`, which was wrong three times
# over: `tee` honours the ambient umask so the file was world readable, /tmp is
# world writable so the path could be pre-created by any local user, and `tee`
# also copies to stdout, which main() has redirected into $LOG_FILE — so the
# recovery shares and the root token were written into the setup log as well.
#
# The follow-up advice, `shred -u`, does not fix it either. shred overwrites the
# blocks the file currently points at. On ext4 with a journal, on btrfs or ZFS
# where every write is copy-on-write, and on any SSD whose FTL remaps blocks for
# wear levelling, earlier copies survive that overwrite. The only honest answer
# for key material is to never write it to a persistent filesystem.
mount_init_tmpfs() {
    install -d -m 700 -o root -g root "$BAO_INIT_DIR"

    if mountpoint -q "$BAO_INIT_DIR" 2>/dev/null; then
        log "init tmpfs already mounted at ${BAO_INIT_DIR}"
        return 0
    fi

    # noswap requires Linux 6.4+ and util-linux 2.39+. Without it the tmpfs
    # pages can be written to swap, which is a persistent filesystem again.
    if mount -t tmpfs -o size=1m,mode=700,noswap tmpfs "$BAO_INIT_DIR" 2>/dev/null; then
        log "init tmpfs mounted at ${BAO_INIT_DIR} (noswap)"
    else
        mount -t tmpfs -o size=1m,mode=700 tmpfs "$BAO_INIT_DIR" \
            || die "could not mount tmpfs at ${BAO_INIT_DIR}"
        warn "tmpfs mounted WITHOUT noswap (needs Linux 6.4+ / util-linux 2.39+)."
        warn "Encrypt swap or disable it before running --init, or the recovery"
        warn "shares can reach disk through swap."
    fi
}

# ── Initialize cluster (run ONCE on bao-01 only) ──────────────────────────────
initialize_cluster() {
    log "=== Initializing OpenBao cluster (bao-01 only) ==="

    export BAO_ADDR="https://${NODE_ID}:${BAO_PORT}"
    export BAO_CACERT="${BAO_TLS_DIR}/ca.crt"

    if bao status 2>/dev/null | grep -q "Initialized.*true"; then
        log "Cluster already initialized"
    else
        mount_init_tmpfs

        local init_file
        init_file=$(umask 077 && mktemp "${BAO_INIT_DIR}/bao-init.XXXXXXXX.json")

        log "Initializing cluster with 5-of-3 Shamir recovery keys..."
        # Redirect to the file. No `tee`: nothing about this output belongs on
        # stdout, which main() has pointed at $LOG_FILE.
        if ! bao operator init \
            -recovery-shares=5 \
            -recovery-threshold=3 \
            -format=json > "$init_file"; then
            rm -f "$init_file"
            die "bao operator init failed"
        fi

        log "=== CRITICAL: transcribe the recovery shares NOW ==="
        log "Init output: ${init_file} (mode 0600, root only, tmpfs)"
        log "  1. Read the 5 recovery shares with: jq -r '.recovery_keys_b64[]' ${init_file}"
        log "  2. Transcribe each share to its custodian, TWO separate physical safes."
        log "  3. Then destroy the whole tmpfs: umount ${BAO_INIT_DIR}"
        log "No shred needed — unmounting the tmpfs is the erase, and it is a real"
        log "erase because the bytes were never on a persistent filesystem."
        log "A reboot also destroys it: transcribe the shares BEFORE rebooting this host."
    fi

    # Hand the bootstrap root token to configure_engines in memory rather than
    # having that function re-read it off disk later. That ordering is what made
    # the old cleanup step impossible: the file had to survive until
    # configure_engines had run, so the "destroy it now" instruction and the
    # script's own requirements contradicted each other.
    load_bootstrap_token

    # Verify auto-unseal is working
    local sealed
    sealed=$(bao status -format=json 2>/dev/null | python3 -c "import sys,json; print(json.load(sys.stdin)['sealed'])" 2>/dev/null || echo "unknown")
    if [[ "$sealed" == "False" ]] || [[ "$sealed" == "false" ]]; then
        log "VERIFIED: Cluster is unsealed (PKCS#11 auto-unseal working)"
    else
        warn "Cluster may still be sealed. Check: bao status"
    fi

    # Verify Raft cluster
    log "Raft cluster peers:"
    bao operator raft list-peers 2>/dev/null | tee -a "$LOG_FILE" || true
}

# ── Load the bootstrap root token into the environment ────────────────────────
load_bootstrap_token() {
    if [[ -n "${BAO_TOKEN:-}" ]]; then
        export BAO_TOKEN
        log "Using BAO_TOKEN from the environment"
        return 0
    fi

    # Find the init file this run produced.
    #
    # The `|| true` matters: with `set -euo pipefail`, find exiting non-zero on a
    # missing directory fails the pipeline, which aborts the assignment and kills
    # the script before the explanatory die below can run, leaving the operator
    # with a bare exit 1 and no message.
    #
    # No `find -printf` here: that is a GNU extension, and on a host without it
    # this would fail in a way that looks like "no token" rather than "your find
    # is different".
    local -a candidates=()
    local line
    while IFS= read -r line; do
        [[ -n "$line" ]] && candidates+=("$line")
    done < <(find "$BAO_INIT_DIR" -maxdepth 1 -name 'bao-init.*.json' -type f 2>/dev/null || true)

    if (( ${#candidates[@]} == 0 )); then
        die "No root token available. Either export BAO_TOKEN=<root-token> or re-run --init on an uninitialized cluster."
    fi
    if (( ${#candidates[@]} > 1 )); then
        # Deliberately not "pick the newest". Which file is the live cluster's
        # init output decides which root token gets revoked at the end; guessing
        # wrong means revoking a token that is not the one in use and leaving the
        # real one alive. Make the operator resolve it.
        warn "Multiple init files in ${BAO_INIT_DIR}:"
        for line in "${candidates[@]}"; do warn "  $line"; done
        die "Cannot tell which init output belongs to this cluster. Remove the stale ones, or set BAO_TOKEN explicitly."
    fi

    local init_file="${candidates[0]}"
    [[ -r "$init_file" ]] || die "init file not readable: $init_file"

    BAO_TOKEN=$(python3 -c "import json,sys; print(json.load(open(sys.argv[1]))['root_token'])" "$init_file")
    export BAO_TOKEN
    [[ -n "$BAO_TOKEN" ]] || die "could not parse root_token from ${init_file}"
    log "Bootstrap root token loaded from ${init_file} (not echoed)"
}

# ── Retire the bootstrap root token ───────────────────────────────────────────
# Chapter 20b puts it plainly: "The root token is for `bao operator init` and
# nothing else." A root token that outlives the bootstrap is a permanent,
# unattributable, non-expiring bypass of every policy this script just wrote, so
# the script that mints it is the right place to retire it.
retire_root_token() {
    log "=== Retiring the bootstrap root token ==="

    if [[ -z "${BAO_TOKEN:-}" ]]; then
        warn "No BAO_TOKEN in the environment — nothing to revoke"
        return 0
    fi

    if ! bao token revoke -self 2>/dev/null; then
        warn "bao token revoke -self failed. Revoke it manually before leaving:"
        warn "  BAO_TOKEN=<root-token> bao token revoke -self"
        return 1
    fi

    # Prove it is dead rather than assuming the command worked.
    if bao token lookup &>/dev/null; then
        warn "Root token still resolves after revoke — investigate immediately"
        return 1
    fi

    unset BAO_TOKEN
    log "Bootstrap root token revoked and verified dead."
    log "Generate a new one only for a break-glass operation, via 3-of-5 recovery"
    log "shares: bao operator generate-root -init"
    return 0
}

# ── Configure secrets engines ─────────────────────────────────────────────────
configure_engines() {
    log "=== Configuring secrets engines ==="

    export BAO_ADDR="https://${NODE_ID}:${BAO_PORT}"
    export BAO_CACERT="${BAO_TLS_DIR}/ca.crt"

    # initialize_cluster already put the bootstrap token in the environment.
    # Calling load_bootstrap_token again is a no-op in that case and covers the
    # operator who runs configure_engines with an explicit BAO_TOKEN.
    load_bootstrap_token

    # ── Transit Engine ──────────────────────────────────────────────────────
    if ! bao secrets list 2>/dev/null | grep -q "^transit/"; then
        bao secrets enable transit
        log "Transit engine enabled"
    fi

    # Create Transit keys for each VM class
    for vm in vm-db-01 vm-redis-01 vm-audit-01 vm-kafka-01; do
        if ! bao read "transit/keys/${vm}" &>/dev/null; then
            bao write "transit/keys/${vm}" type=aes256-gcm96
            # Annual auto-rotation (PCI DSS Req. 3.7.1)
            bao write "transit/keys/${vm}/config" \
                auto_rotate_period=8760h \
                deletion_allowed=false
            log "Transit key created: ${vm} (auto-rotate: 8760h)"
        fi
    done

    # ── AppRole Auth ───────────────────────────────────────────────────────
    if ! bao auth list 2>/dev/null | grep -q "approle/"; then
        bao auth enable approle
        log "AppRole auth enabled"
    fi

    # Create AppRole and policy per VM
    for vm in vm-db-01 vm-redis-01 vm-audit-01 vm-kafka-01; do
        if ! bao policy read "policy-${vm}" &>/dev/null; then
            bao policy write "policy-${vm}" - << EOF
# Minimal policy — decrypt only, own key only
# PCI DSS Req. 3.6: least privilege for crypto operations
path "transit/decrypt/${vm}" {
  capabilities = ["update"]
}
EOF
            bao write "auth/approle/role/${vm}" \
                token_policies="policy-${vm}" \
                token_ttl=5m \
                token_max_ttl=10m \
                token_num_uses=3 \
                secret_id_ttl=0 \
                bind_secret_id=true
            log "AppRole created for ${vm} (token TTL: 5m)"
        fi
    done

    # ── PKI Engine ─────────────────────────────────────────────────────────
    if ! bao secrets list 2>/dev/null | grep -q "^pki/"; then
        bao secrets enable pki
        bao secrets tune -max-lease-ttl=87600h pki

        # Internal CA for mTLS certs
        bao write pki/root/generate/internal \
            common_name="OpenBao VM CA" \
            ttl=87600h

        bao write pki/roles/vm-client \
            allowed_domains="vm.internal" \
            allow_subdomains=true \
            max_ttl=720h \
            key_type=rsa \
            key_bits=2048
        log "PKI engine enabled with VM CA"
    fi

    # ── KV Engine (v2) ─────────────────────────────────────────────────────
    if ! bao secrets list 2>/dev/null | grep -q "^kv/"; then
        bao secrets enable -version=2 kv
        log "KV v2 engine enabled"
    fi

    # ── Audit Logging (required for PCI DSS Req. 10 / ISO 27001 A.12.4) ──
    mkdir -p /var/log/openbao
    chown openbao:openbao /var/log/openbao

    if ! bao audit list 2>/dev/null | grep -q "file/"; then
        bao audit enable file \
            file_path=/var/log/openbao/audit.log \
            log_raw=false \
            format=json
        log "File audit log enabled: /var/log/openbao/audit.log"
    fi

    if ! bao audit list 2>/dev/null | grep -q "syslog/"; then
        bao audit enable syslog \
            tag=openbao \
            facility=AUTH
        log "Syslog audit enabled (tag=openbao, facility=AUTH)"
    fi

    # ── Configure logrotate ────────────────────────────────────────────────
    cat > /etc/logrotate.d/openbao << 'EOF'
/var/log/openbao/audit.log {
    daily
    rotate 365
    compress
    delaycompress
    missingok
    notifempty
    postrotate
        pkill -HUP openbao 2>/dev/null || true
    endscript
}
EOF
    log "Logrotate configured for OpenBao audit log (retain 365 days)"

    # Generated AppRole credentials.
    #
    # These used to be written through log(), which appends to
    # /var/log/openbao-setup.log — a long-lived file that ships to the log
    # collector. A secret_id in a log aggregator is a credential handed to
    # everyone with log read access, so the secret_ids go to the tmpfs instead
    # and only the role_ids (which are not secret on their own) are logged.
    mount_init_tmpfs
    local approle_file
    approle_file=$(umask 077 && mktemp "${BAO_INIT_DIR}/approle-creds.XXXXXXXX.txt")

    log "=== AppRole credentials ==="
    for vm in vm-db-01 vm-redis-01 vm-audit-01 vm-kafka-01; do
        local role_id secret_id
        role_id=$(bao read -field=role_id "auth/approle/role/${vm}/role-id" 2>/dev/null || echo "N/A")
        secret_id=$(bao write -field=secret_id -f "auth/approle/role/${vm}/secret-id" 2>/dev/null || echo "N/A")
        printf '%s role_id=%s secret_id=%s\n' "$vm" "$role_id" "$secret_id" >> "$approle_file"
        log "  ${vm}: role_id=${role_id} secret_id=<written to ${approle_file}>"
    done
    log "secret_ids are in ${approle_file} (mode 0600, tmpfs). Deliver them to each"
    log "VM over a secure channel, then: umount ${BAO_INIT_DIR}"

    log "=== OpenBao cluster configuration complete ==="
}

# ── Verify full cluster health ────────────────────────────────────────────────
verify_cluster() {
    log "=== Cluster verification ==="

    export BAO_ADDR="https://${NODE_ID}:${BAO_PORT}"
    export BAO_CACERT="${BAO_TLS_DIR}/ca.crt"

    log "Status:"
    bao status 2>/dev/null | tee -a "$LOG_FILE" || warn "bao status failed"

    log "Raft peers:"
    bao operator raft list-peers 2>/dev/null | tee -a "$LOG_FILE" || \
        warn "raft list-peers failed (may need token)"

    log "Secrets engines:"
    bao secrets list 2>/dev/null | tee -a "$LOG_FILE" || true

    log "Transit keys:"
    bao list transit/keys 2>/dev/null | tee -a "$LOG_FILE" || true

    log "Audit backends:"
    bao audit list 2>/dev/null | tee -a "$LOG_FILE" || true
}

# ── Main entrypoint ───────────────────────────────────────────────────────────
main() {
    # 077 before the log file is created: $LOG_FILE records the whole run and
    # lands in /var/log, where the default umask would leave it world readable.
    umask 077
    exec > >(tee -a "$LOG_FILE") 2>&1
    log "=== OpenBao Cluster Setup Start (node: ${NODE_ID}, $(date)) ==="

    preflight
    install_openbao
    setup_directories
    generate_tls
    write_config
    write_systemd_override
    start_service

    if [[ "${1:-}" == "--init" ]]; then
        [[ "$NODE_ID" == "bao-01" ]] || die "--init must be run on bao-01"
        initialize_cluster
        configure_engines
        # Verify while we still hold a token, then give the token up. Nothing
        # after this point is allowed to need root.
        verify_cluster
        retire_root_token || warn "bootstrap root token was NOT retired — see above"
    else
        verify_cluster
    fi

    log ""
    log "=== Setup complete for ${NODE_ID} ==="
    log ""
    log "Next steps:"
    log "  1. Run on bao-02: NODE_ID=bao-02 NODE_IP=10.0.10.12 BAO_HSM_PIN=<pin> bash setup-openbao-cluster.sh"
    log "  2. Run on bao-03: NODE_ID=bao-03 NODE_IP=10.0.10.13 BAO_HSM_PIN=<pin> bash setup-openbao-cluster.sh"
    log "  3. Initialize cluster (on bao-01 only): NODE_ID=bao-01 ... bash setup-openbao-cluster.sh --init"
    log "  4. Transcribe the recovery shares out of ${BAO_INIT_DIR} to two physical safes,"
    log "     deliver the AppRole secret_ids, then destroy the tmpfs: umount ${BAO_INIT_DIR}"
    log "  5. The bootstrap root token is revoked automatically at the end of --init."
    log "     For a later break-glass operation, mint a fresh one with 3-of-5 recovery"
    log "     shares (bao operator generate-root -init) and revoke it again afterwards."
}

main "$@"
