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

# setup-cloudhsm-cluster.sh
# Automates AWS CloudHSM v2 cluster creation, initialisation, user provisioning,
# and cryptographic key generation for the iGaming platform.
#
# Prerequisites:
#   - AWS CLI v2 configured with appropriate IAM permissions
#   - VPC and private subnets (3 AZs) already exist
#   - CloudHSM Client SDK 5.11+ installed on this host
#   - OpenSSL 3.x installed
#
# Usage:
#   export AWS_REGION="eu-west-1"
#   export VPC_ID="vpc-xxxxx"
#   export SUBNET_IDS="subnet-aaa,subnet-bbb,subnet-ccc"
#   export HSM_CO_PASSWORD="<crypto-officer-password>"
#   export HSM_APP_PASSWORD="<crypto-user-password>"
#   bash setup-cloudhsm-cluster.sh [--skip-cluster] [--skip-keys]
#
# Flags:
#   --skip-cluster   Skip cluster and HSM creation (use CLUSTER_ID from env)
#   --skip-keys      Skip key generation (useful for re-runs)
#
# Compliance: PCI DSS v4.0.1 Req. 3.6/3.7, ISO 27001 A.8.24, FIPS 140-2 L3

set -euo pipefail

# ── Configuration ──────────────────────────────────────────────────────────────
AWS_REGION="${AWS_REGION:-eu-west-1}"
VPC_ID="${VPC_ID:-}"
SUBNET_IDS="${SUBNET_IDS:-}"           # comma-separated: subnet-a,subnet-b,subnet-c
HSM_TYPE="${HSM_TYPE:-hsm1.medium}"
HSM_CO_PASSWORD="${HSM_CO_PASSWORD:-}" # Crypto Officer password
HSM_APP_PASSWORD="${HSM_APP_PASSWORD:-}" # Crypto User password for app

CLUSTER_ID="${CLUSTER_ID:-}"          # Pre-set if --skip-cluster
CA_KEY_FILE="${CA_KEY_FILE:-./hsm-ca.key}"
CA_CERT_FILE="${CA_CERT_FILE:-./hsm-ca.crt}"
CLUSTER_CERT_FILE="${CLUSTER_CERT_FILE:-./cluster.crt}"
WORK_DIR="/tmp/cloudhsm-setup-$$"
LOG_FILE="/var/log/cloudhsm-setup-$(date +%Y%m%d-%H%M%S).log"

SKIP_CLUSTER=0
SKIP_KEYS=0

# ── Argument parsing ───────────────────────────────────────────────────────────
for arg in "$@"; do
    case "${arg}" in
        --skip-cluster) SKIP_CLUSTER=1 ;;
        --skip-keys)    SKIP_KEYS=1 ;;
        *) echo "Unknown argument: ${arg}"; exit 1 ;;
    esac
done

# ── Logging helpers ────────────────────────────────────────────────────────────
mkdir -p "$(dirname "${LOG_FILE}")"
log()  { echo "[$(date -Is)] INFO  $*" | tee -a "${LOG_FILE}"; }
warn() { echo "[$(date -Is)] WARN  $*" | tee -a "${LOG_FILE}"; }
die()  { echo "[$(date -Is)] ERROR $*" | tee -a "${LOG_FILE}"; exit 1; }
sep()  { echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━" | tee -a "${LOG_FILE}"; }

# ── Preflight checks ──────────────────────────────────────────────────────────
preflight() {
    sep
    log "=== Preflight checks ==="
    command -v aws     >/dev/null 2>&1 || die "AWS CLI not found. Install: https://docs.aws.amazon.com/cli/latest/userguide/install-cliv2.html"
    command -v openssl >/dev/null 2>&1 || die "OpenSSL not found"
    command -v jq      >/dev/null 2>&1 || die "jq not found (sudo apt-get install jq)"

    [[ -n "${VPC_ID}"          ]] || die "VPC_ID not set"
    [[ -n "${SUBNET_IDS}"      ]] || die "SUBNET_IDS not set (comma-separated)"
    [[ -n "${HSM_CO_PASSWORD}" ]] || die "HSM_CO_PASSWORD not set"
    [[ -n "${HSM_APP_PASSWORD}" ]] || die "HSM_APP_PASSWORD not set"

    # Verify AWS credentials
    local ACCOUNT_ID
    ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text 2>/dev/null) \
        || die "AWS credentials not configured or insufficient permissions"
    log "AWS account: ${ACCOUNT_ID}, region: ${AWS_REGION}"

    # Warn on weak passwords
    if [[ "${#HSM_CO_PASSWORD}" -lt 12 ]]; then
        warn "HSM_CO_PASSWORD is shorter than 12 characters — CloudHSM requires ≥7 but PCI DSS recommends ≥12"
    fi

    # Check CloudHSM client SDK version
    if command -v /opt/cloudhsm/bin/key_mgmt_util >/dev/null 2>&1; then
        local SDK_VER
        SDK_VER=$(dpkg -l cloudhsm-client 2>/dev/null | awk '/cloudhsm-client/{print $3}' | head -1)
        if [[ -n "${SDK_VER}" ]]; then
            log "CloudHSM Client SDK installed: ${SDK_VER}"
            # Check for Ed25519 support (requires 5.11+)
            local MAJOR MINOR
            MAJOR=$(echo "${SDK_VER}" | cut -d. -f1)
            MINOR=$(echo "${SDK_VER}" | cut -d. -f2)
            if [[ "${MAJOR}" -lt 5 ]] || { [[ "${MAJOR}" -eq 5 ]] && [[ "${MINOR}" -lt 11 ]]; }; then
                warn "SDK version ${SDK_VER} is below 5.11 — Ed25519 key generation will FAIL"
                warn "Upgrade: https://docs.aws.amazon.com/cloudhsm/latest/userguide/client-history.html"
            fi
        fi
    else
        warn "CloudHSM Client SDK not found at /opt/cloudhsm/bin/ — key generation step will fail"
        warn "Install guide: https://docs.aws.amazon.com/cloudhsm/latest/userguide/install-and-configure-client-linux.html"
    fi

    mkdir -p "${WORK_DIR}"
    log "Working directory: ${WORK_DIR}"
    log "Preflight: OK"
}

# ── Convert comma-separated subnet IDs to space-separated ─────────────────────
subnet_array() {
    echo "${SUBNET_IDS}" | tr ',' ' '
}

# ── Create CloudHSM cluster ────────────────────────────────────────────────────
create_cluster() {
    sep
    log "=== Step 1: Creating CloudHSM v2 cluster ==="

    # shellcheck disable=SC2046
    CLUSTER_ID=$(aws cloudhsmv2 create-cluster \
        --hsm-type "${HSM_TYPE}" \
        --subnet-ids $(subnet_array) \
        --region "${AWS_REGION}" \
        --query 'Cluster.ClusterId' \
        --output text)

    log "Cluster created: ${CLUSTER_ID}"

    log "Waiting for cluster to reach UNINITIALIZED state..."
    local ATTEMPTS=0
    while true; do
        local STATE
        STATE=$(aws cloudhsmv2 describe-clusters \
            --filters "clusterIds=${CLUSTER_ID}" \
            --region "${AWS_REGION}" \
            --query 'Clusters[0].State' \
            --output text)
        log "  Cluster state: ${STATE}"
        [[ "${STATE}" == "UNINITIALIZED" ]] && break
        (( ATTEMPTS++ ))
        [[ "${ATTEMPTS}" -gt 40 ]] && die "Cluster did not reach UNINITIALIZED after 10 minutes"
        sleep 15
    done
    log "Cluster ${CLUSTER_ID} is UNINITIALIZED — ready for first HSM"
}

# ── Create first HSM device ────────────────────────────────────────────────────
create_first_hsm() {
    sep
    log "=== Step 2: Creating first HSM device ==="

    # Use the first subnet's AZ
    local FIRST_SUBNET
    FIRST_SUBNET=$(echo "${SUBNET_IDS}" | cut -d, -f1)
    local AZ
    AZ=$(aws ec2 describe-subnets \
        --subnet-ids "${FIRST_SUBNET}" \
        --region "${AWS_REGION}" \
        --query 'Subnets[0].AvailabilityZone' \
        --output text)

    local HSM_ID
    HSM_ID=$(aws cloudhsmv2 create-hsm \
        --cluster-id "${CLUSTER_ID}" \
        --availability-zone "${AZ}" \
        --region "${AWS_REGION}" \
        --query 'Hsm.HsmId' \
        --output text)

    log "HSM created: ${HSM_ID} in ${AZ}"
    log "Waiting for HSM to become ACTIVE (may take 10–15 minutes)..."

    local ATTEMPTS=0
    while true; do
        local STATE
        STATE=$(aws cloudhsmv2 describe-clusters \
            --filters "clusterIds=${CLUSTER_ID}" \
            --region "${AWS_REGION}" \
            --query 'Clusters[0].Hsms[?HsmId==`'"${HSM_ID}"'`].State' \
            --output text)
        log "  HSM state: ${STATE}"
        [[ "${STATE}" == "ACTIVE" ]] && break
        (( ATTEMPTS++ ))
        [[ "${ATTEMPTS}" -gt 60 ]] && die "HSM did not become ACTIVE after 15 minutes"
        sleep 15
    done

    # Extract ENI IP for client configuration
    HSM_ENI_IP=$(aws cloudhsmv2 describe-clusters \
        --filters "clusterIds=${CLUSTER_ID}" \
        --region "${AWS_REGION}" \
        --query 'Clusters[0].Hsms[0].EniIp' \
        --output text)
    log "First HSM ENI IP: ${HSM_ENI_IP}"
}

# ── Generate CA and sign cluster CSR ──────────────────────────────────────────
generate_ca_and_sign() {
    sep
    log "=== Step 3: Key ceremony — generate CA and sign cluster CSR ==="
    log "IMPORTANT: Document who is performing this ceremony for PCI DSS Req. 3.7.6"

    # Retrieve cluster CSR
    local CSR_FILE="${WORK_DIR}/cluster.csr"
    aws cloudhsmv2 describe-clusters \
        --filters "clusterIds=${CLUSTER_ID}" \
        --region "${AWS_REGION}" \
        --query 'Clusters[0].Certificates.ClusterCsr' \
        --output text > "${CSR_FILE}"
    log "Cluster CSR saved: ${CSR_FILE}"

    # Generate CA key (4096-bit RSA with AES-256 passphrase)
    if [[ ! -f "${CA_KEY_FILE}" ]]; then
        log "Generating CA private key (store this securely — offline HSM recommended)"
        openssl genrsa -aes256 -passout "pass:${HSM_CO_PASSWORD}" \
            -out "${CA_KEY_FILE}" 4096
        chmod 600 "${CA_KEY_FILE}"
        log "CA key: ${CA_KEY_FILE}"
    else
        log "Using existing CA key: ${CA_KEY_FILE}"
    fi

    # Generate self-signed CA certificate
    if [[ ! -f "${CA_CERT_FILE}" ]]; then
        openssl req -new -x509 -days 3650 \
            -key "${CA_KEY_FILE}" \
            -passin "pass:${HSM_CO_PASSWORD}" \
            -out "${CA_CERT_FILE}" \
            -subj "/CN=iGaming CloudHSM Cluster CA/O=iGaming Platform/C=GB" \
            -addext "basicConstraints=critical,CA:TRUE,pathlen:0" \
            -addext "keyUsage=critical,keyCertSign,cRLSign"
        log "CA certificate: ${CA_CERT_FILE}"
    else
        log "Using existing CA certificate: ${CA_CERT_FILE}"
    fi

    # Sign the cluster CSR
    openssl x509 -req -days 3650 \
        -in "${CSR_FILE}" \
        -CA "${CA_CERT_FILE}" \
        -CAkey "${CA_KEY_FILE}" \
        -passin "pass:${HSM_CO_PASSWORD}" \
        -CAcreateserial \
        -out "${CLUSTER_CERT_FILE}"
    log "Cluster certificate signed: ${CLUSTER_CERT_FILE}"
}

# ── Initialise the cluster ─────────────────────────────────────────────────────
initialize_cluster() {
    sep
    log "=== Step 4: Initialising CloudHSM cluster ==="

    aws cloudhsmv2 initialize-cluster \
        --cluster-id "${CLUSTER_ID}" \
        --signed-cert "file://${CLUSTER_CERT_FILE}" \
        --trust-anchor "file://${CA_CERT_FILE}" \
        --region "${AWS_REGION}"

    log "Waiting for cluster to reach INITIALIZED state..."
    local ATTEMPTS=0
    while true; do
        local STATE
        STATE=$(aws cloudhsmv2 describe-clusters \
            --filters "clusterIds=${CLUSTER_ID}" \
            --region "${AWS_REGION}" \
            --query 'Clusters[0].State' \
            --output text)
        log "  Cluster state: ${STATE}"
        [[ "${STATE}" == "INITIALIZED" ]] && break
        (( ATTEMPTS++ ))
        [[ "${ATTEMPTS}" -gt 20 ]] && die "Cluster did not reach INITIALIZED"
        sleep 15
    done
    log "Cluster initialised successfully"
}

# ── Configure CloudHSM client on this EC2 instance ────────────────────────────
configure_client() {
    sep
    log "=== Step 5: Configuring CloudHSM client ==="

    if [[ ! -f /opt/cloudhsm/bin/configure-client ]]; then
        warn "CloudHSM client not installed — skipping client configuration"
        warn "Install manually, then run: /opt/cloudhsm/bin/configure-client -a ${HSM_ENI_IP:-<HSM_ENI_IP>} --hsm-ca-cert ${CA_CERT_FILE}"
        return 0
    fi

    # Install the CA certificate in the expected location
    sudo cp "${CA_CERT_FILE}" /opt/cloudhsm/etc/customerCA.crt
    sudo chmod 644 /opt/cloudhsm/etc/customerCA.crt

    sudo /opt/cloudhsm/bin/configure-client \
        -a "${HSM_ENI_IP}" \
        --hsm-ca-cert "${CA_CERT_FILE}"

    sudo systemctl enable cloudhsm-client
    sudo systemctl restart cloudhsm-client

    # Wait for daemon to stabilise
    sleep 5
    if sudo systemctl is-active --quiet cloudhsm-client; then
        log "CloudHSM client daemon is running"
    else
        warn "CloudHSM client daemon did not start — check: journalctl -u cloudhsm-client"
    fi
}

# ── Activate CO and create CU ─────────────────────────────────────────────────
provision_hsm_users() {
    sep
    log "=== Step 6: Provisioning HSM users via cloudhsm_mgmt_util ==="

    if [[ ! -f /opt/cloudhsm/bin/cloudhsm_mgmt_util ]]; then
        warn "cloudhsm_mgmt_util not found — skipping user provisioning"
        warn "Run manually after installing SDK:"
        warn "  /opt/cloudhsm/bin/cloudhsm_mgmt_util /opt/cloudhsm/etc/cloudhsm_mgmt_util.cfg"
        warn "  loginHSM CO admin <initial-password>"
        warn "  changePswd CO admin ${HSM_CO_PASSWORD}"
        warn "  createUser CU hsm-app ${HSM_APP_PASSWORD}"
        warn "  quit"
        return 0
    fi

    # Build the command file for cloudhsm_mgmt_util
    local CMD_FILE="${WORK_DIR}/mgmt-cmds.txt"
    cat > "${CMD_FILE}" << EOF
loginHSM CO admin password
changePswd CO admin ${HSM_CO_PASSWORD}
createUser CU hsm-app ${HSM_APP_PASSWORD}
listUsers
quit
EOF

    log "Running cloudhsm_mgmt_util to create users..."
    # NOTE: 'password' is the AWS default initial CO password
    /opt/cloudhsm/bin/cloudhsm_mgmt_util \
        /opt/cloudhsm/etc/cloudhsm_mgmt_util.cfg < "${CMD_FILE}" \
        | tee -a "${LOG_FILE}" \
        || warn "cloudhsm_mgmt_util exited non-zero — review log for details"

    shred -u "${CMD_FILE}"
    log "HSM users provisioned; command file securely deleted"
}

# ── Generate cryptographic keys ────────────────────────────────────────────────
generate_keys() {
    sep
    log "=== Step 7: Generating cryptographic keys ==="

    if [[ ! -f /opt/cloudhsm/bin/key_mgmt_util ]]; then
        warn "key_mgmt_util not found — skipping key generation"
        warn "See IMPLEMENTATION-GUIDE.md Step 6 for manual key generation commands"
        return 0
    fi

    # Build the key generation command file
    local EPOCH_LABEL
    EPOCH_LABEL="epoch-$(date +%Y%m%d)-aes256"
    local KEY_CMD_FILE="${WORK_DIR}/key-cmds.txt"

    cat > "${KEY_CMD_FILE}" << EOF
loginHSM -u CU -s hsm-app -p ${HSM_APP_PASSWORD}
# AES-256 wrap key for OpenBao barrier key — non-exportable, persistent
genSymKey -t 31 -s 32 -l wrap-key-aes256 -nex -sess 0 -id 1001
# AES-256 epoch key for HKDF seed — non-exportable, persistent
genSymKey -t 31 -s 32 -l ${EPOCH_LABEL} -nex -sess 0 -id 1002
# Ed25519 key pair for JWT signing — non-exportable (requires SDK 5.11+)
genECCKeyPair -curve ED25519 -l jwt-signing-ed25519 -nex -id 2001
# ECDSA P-256 key pair for audit chain — non-exportable
genECCKeyPair -curve prime256v1 -l audit-chain-p256 -nex -id 2002
listKeys -u -s
logout
quit
EOF

    log "Generating keys via key_mgmt_util..."
    /opt/cloudhsm/bin/key_mgmt_util << EOF | tee -a "${LOG_FILE}"
$(cat "${KEY_CMD_FILE}")
EOF

    shred -u "${KEY_CMD_FILE}"

    log "Keys generated:"
    log "  1001 — wrap-key-aes256       (AES-256, OpenBao seal)"
    log "  1002 — ${EPOCH_LABEL} (AES-256, epoch/HKDF seed)"
    log "  2001 — jwt-signing-ed25519   (Ed25519, JWT auth)"
    log "  2002 — audit-chain-p256      (ECDSA P-256, audit chain)"
}

# ── Add remaining HSMs for HA ──────────────────────────────────────────────────
scale_to_ha() {
    sep
    log "=== Step 8: Adding HSMs in remaining AZs for HA ==="

    local SUBNET_ARR
    IFS=',' read -r -a SUBNET_ARR <<< "${SUBNET_IDS}"
    # Skip first subnet (already has HSM from Step 2)
    local i=0
    for SUBNET in "${SUBNET_ARR[@]}"; do
        (( i++ ))
        [[ "${i}" -eq 1 ]] && continue  # skip first

        local AZ
        AZ=$(aws ec2 describe-subnets \
            --subnet-ids "${SUBNET}" \
            --region "${AWS_REGION}" \
            --query 'Subnets[0].AvailabilityZone' \
            --output text)

        log "Creating HSM ${i} in subnet ${SUBNET} (${AZ})..."
        local HSM_ID
        HSM_ID=$(aws cloudhsmv2 create-hsm \
            --cluster-id "${CLUSTER_ID}" \
            --availability-zone "${AZ}" \
            --region "${AWS_REGION}" \
            --query 'Hsm.HsmId' \
            --output text)
        log "  HSM ${i} created: ${HSM_ID}"
    done

    log "All HSMs created — cluster will sync keys automatically (may take 5–10 minutes)"
    log "Monitor state: aws cloudhsmv2 describe-clusters --filters clusterIds=${CLUSTER_ID}"
}

# ── Print summary ──────────────────────────────────────────────────────────────
print_summary() {
    sep
    log "=== Setup Complete ==="
    log ""
    log "Cluster ID:    ${CLUSTER_ID}"
    log "CA Certificate: ${CA_CERT_FILE}"
    log "Log file:       ${LOG_FILE}"
    log ""
    log "Next steps:"
    log "  1. Install CloudHSM Client SDK 5.11+ on all OpenBao EC2 nodes"
    log "  2. Copy ${CA_CERT_FILE} to /opt/cloudhsm/etc/customerCA.crt on each node"
    log "  3. Run configure-client with the HSM ENI IP on each node"
    log "  4. Deploy openbao-cloudhsm.hcl and setup-openbao-cloudhsm.sh"
    log "  5. Verify with test-cloudhsm.sh"
    log ""
    log "PKCS#11 library path: /opt/cloudhsm/lib/libcloudhsm_pkcs11.so"
    log "HSM App PIN env var:  CLOUDHSM_PIN=hsm-app:<app-password>"
    sep
}

# ── Main ───────────────────────────────────────────────────────────────────────
main() {
    log "CloudHSM Cluster Setup — iGaming Platform"
    log "Region: ${AWS_REGION} | Start: $(date -Is)"

    preflight

    if [[ "${SKIP_CLUSTER}" -eq 0 ]]; then
        create_cluster
        create_first_hsm
        generate_ca_and_sign
        initialize_cluster
        configure_client
        provision_hsm_users
    else
        [[ -n "${CLUSTER_ID}" ]] || die "--skip-cluster requires CLUSTER_ID to be set"
        log "Skipping cluster creation — using CLUSTER_ID=${CLUSTER_ID}"
        # Still need ENI IP for subsequent steps
        HSM_ENI_IP=$(aws cloudhsmv2 describe-clusters \
            --filters "clusterIds=${CLUSTER_ID}" \
            --region "${AWS_REGION}" \
            --query 'Clusters[0].Hsms[0].EniIp' \
            --output text)
        log "Existing HSM ENI IP: ${HSM_ENI_IP}"
    fi

    if [[ "${SKIP_KEYS}" -eq 0 ]]; then
        generate_keys
    else
        log "Skipping key generation (--skip-keys)"
    fi

    scale_to_ha
    print_summary
}

main "$@"
