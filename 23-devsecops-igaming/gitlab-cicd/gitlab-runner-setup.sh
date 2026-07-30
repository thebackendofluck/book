#!/usr/bin/env bash
# Companion code for "The Backend of Luck" - Chapter 23, DevSecOps for iGaming.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

set -euo pipefail

# =============================================================================
# GitLab Runner Setup for On-Premises iGaming Infrastructure
# =============================================================================
# Installs and configures GitLab Runner on bare metal or VM for use in
# regulated gaming environments. Supports air-gapped networks, Docker executor,
# caching, security hardening, and Prometheus metrics.
#
# Usage:
#   ./gitlab-runner-setup.sh --url <gitlab-url> --token <registration-token> \
#       [--tags docker,shell] [--executor docker] [--air-gapped] \
#       [--cache-type s3|local] [--cache-path /var/cache/gitlab-runner]
#
# Requirements:
#   - Root or sudo access
#   - Network access to GitLab instance (or air-gapped package)
#   - Docker installed (for docker executor)
#
# iGaming context:
#   Regulated gambling operators often run on-premises infrastructure to
#   satisfy data residency requirements. Runners must be hardened, auditable,
#   and capable of operating in restricted network environments.
# =============================================================================

# -- Defaults --
GITLAB_URL=""
REGISTRATION_TOKEN=""
RUNNER_TAGS="docker,shell,kubernetes"
EXECUTOR="docker"
AIR_GAPPED=false
CACHE_TYPE="local"
CACHE_PATH="/var/cache/gitlab-runner"
CACHE_S3_ENDPOINT=""
CACHE_S3_BUCKET=""
CACHE_S3_ACCESS_KEY=""
CACHE_S3_SECRET_KEY=""
RUNNER_NAME="igaming-runner-$(hostname -s)"
DOCKER_IMAGE="python:3.12-slim"
CONCURRENT=4
METRICS_PORT=9252

# =============================================================================
# Functions
# =============================================================================

usage() {
    cat <<EOF
Usage: $(basename "$0") [OPTIONS]

Install and configure GitLab Runner for on-premises iGaming deployment.

Required:
  --url <url>              GitLab instance URL (e.g., https://gitlab.igaming.internal)
  --token <token>          Runner registration token

Optional:
  --name <name>            Runner name (default: igaming-runner-<hostname>)
  --tags <tags>            Comma-separated runner tags (default: docker,shell,kubernetes)
  --executor <type>        Executor type: docker, shell (default: docker)
  --docker-image <image>   Default Docker image (default: python:3.12-slim)
  --concurrent <n>         Max concurrent jobs (default: 4)
  --air-gapped             Configure for air-gapped environment
  --cache-type <type>      Cache backend: local, s3 (default: local)
  --cache-path <path>      Local cache directory (default: /var/cache/gitlab-runner)
  --cache-s3-endpoint <ep> S3-compatible endpoint for cache
  --cache-s3-bucket <name> S3 bucket name for cache
  --cache-s3-access <key>  S3 access key
  --cache-s3-secret <key>  S3 secret key
  --metrics-port <port>    Prometheus metrics port (default: 9252)
  --help                   Show this help message

Examples:
  # Standard installation with Docker executor
  $(basename "$0") --url https://gitlab.igaming.internal --token GR134...

  # Air-gapped with local cache
  $(basename "$0") --url https://gitlab.local --token GR134... --air-gapped

  # S3-compatible cache (MinIO)
  $(basename "$0") --url https://gitlab.local --token GR134... \\
      --cache-type s3 --cache-s3-endpoint http://minio:9000 \\
      --cache-s3-bucket runner-cache
EOF
    exit 0
}

log_info() {
    echo "[INFO]  $(date -u +%Y-%m-%dT%H:%M:%SZ) $*"
}

log_warn() {
    echo "[WARN]  $(date -u +%Y-%m-%dT%H:%M:%SZ) $*" >&2
}

log_error() {
    echo "[ERROR] $(date -u +%Y-%m-%dT%H:%M:%SZ) $*" >&2
}

check_root() {
    if [[ $EUID -ne 0 ]]; then
        log_error "This script must be run as root or with sudo."
        exit 1
    fi
}

# =============================================================================
# Parse arguments
# =============================================================================
while [[ $# -gt 0 ]]; do
    case $1 in
        --url)             GITLAB_URL="$2";          shift 2 ;;
        --token)           REGISTRATION_TOKEN="$2";  shift 2 ;;
        --name)            RUNNER_NAME="$2";         shift 2 ;;
        --tags)            RUNNER_TAGS="$2";         shift 2 ;;
        --executor)        EXECUTOR="$2";            shift 2 ;;
        --docker-image)    DOCKER_IMAGE="$2";        shift 2 ;;
        --concurrent)      CONCURRENT="$2";          shift 2 ;;
        --air-gapped)      AIR_GAPPED=true;          shift   ;;
        --cache-type)      CACHE_TYPE="$2";          shift 2 ;;
        --cache-path)      CACHE_PATH="$2";          shift 2 ;;
        --cache-s3-endpoint) CACHE_S3_ENDPOINT="$2"; shift 2 ;;
        --cache-s3-bucket) CACHE_S3_BUCKET="$2";     shift 2 ;;
        --cache-s3-access) CACHE_S3_ACCESS_KEY="$2"; shift 2 ;;
        --cache-s3-secret) CACHE_S3_SECRET_KEY="$2"; shift 2 ;;
        --metrics-port)    METRICS_PORT="$2";        shift 2 ;;
        --help)            usage ;;
        *)
            log_error "Unknown option: $1"
            usage
            ;;
    esac
done

if [[ -z "${GITLAB_URL}" || -z "${REGISTRATION_TOKEN}" ]]; then
    log_error "Both --url and --token are required."
    usage
fi

# =============================================================================
# Main installation
# =============================================================================

check_root

log_info "Starting GitLab Runner installation for iGaming platform"
log_info "GitLab URL: ${GITLAB_URL}"
log_info "Runner name: ${RUNNER_NAME}"
log_info "Executor: ${EXECUTOR}"
log_info "Air-gapped mode: ${AIR_GAPPED}"

# -- Step 1: Install GitLab Runner --
log_info "Step 1: Installing GitLab Runner..."

if [[ "${AIR_GAPPED}" == "true" ]]; then
    log_info "Air-gapped mode: expecting local package at /opt/gitlab-runner/"
    if [[ -f /opt/gitlab-runner/gitlab-runner.deb ]]; then
        dpkg -i /opt/gitlab-runner/gitlab-runner.deb
    elif [[ -f /opt/gitlab-runner/gitlab-runner.rpm ]]; then
        rpm -i /opt/gitlab-runner/gitlab-runner.rpm
    else
        log_error "No GitLab Runner package found at /opt/gitlab-runner/"
        log_error "Download from: https://docs.gitlab.com/runner/install/linux-manually.html"
        exit 1
    fi
else
    # Detect OS and install accordingly
    if command -v apt-get &>/dev/null; then
        log_info "Detected Debian/Ubuntu, using apt repository..."
        curl -fsSL "https://packages.gitlab.com/install/repositories/runner/gitlab-runner/script.deb.sh" | bash
        apt-get install -y gitlab-runner
    elif command -v yum &>/dev/null; then
        log_info "Detected RHEL/CentOS, using yum repository..."
        curl -fsSL "https://packages.gitlab.com/install/repositories/runner/gitlab-runner/script.rpm.sh" | bash
        yum install -y gitlab-runner
    else
        log_error "Unsupported OS. Install GitLab Runner manually."
        exit 1
    fi
fi

log_info "GitLab Runner version: $(gitlab-runner --version 2>&1 | head -1)"

# -- Step 2: Create dedicated service user --
log_info "Step 2: Configuring service user..."

if ! id -u gitlab-runner &>/dev/null; then
    useradd --system --shell /usr/sbin/nologin --home-dir /home/gitlab-runner \
        --create-home gitlab-runner
fi

# Add to docker group if using docker executor
if [[ "${EXECUTOR}" == "docker" ]]; then
    if getent group docker &>/dev/null; then
        usermod -aG docker gitlab-runner
        log_info "Added gitlab-runner to docker group"
    else
        log_warn "Docker group not found. Ensure Docker is installed."
    fi
fi

# -- Step 3: Register the runner --
log_info "Step 3: Registering runner with GitLab..."

REGISTER_CMD=(
    gitlab-runner register
    --non-interactive
    --url "${GITLAB_URL}"
    --registration-token "${REGISTRATION_TOKEN}"
    --name "${RUNNER_NAME}"
    --tag-list "${RUNNER_TAGS}"
    --executor "${EXECUTOR}"
    --run-untagged=false
    --locked=false
)

if [[ "${EXECUTOR}" == "docker" ]]; then
    REGISTER_CMD+=(
        --docker-image "${DOCKER_IMAGE}"
        --docker-privileged=false
        --docker-volumes "/var/run/docker.sock:/var/run/docker.sock"
        --docker-network-mode "igaming-ci"
        --docker-pull-policy "if-not-present"
    )

    # For air-gapped: use local registry mirror
    if [[ "${AIR_GAPPED}" == "true" ]]; then
        REGISTER_CMD+=(
            --docker-pull-policy "never"
        )
        log_info "Air-gapped: Docker pull policy set to 'never' (pre-loaded images required)"
    fi
fi

"${REGISTER_CMD[@]}"

log_info "Runner registered successfully"

# -- Step 4: Configure runner (config.toml) --
log_info "Step 4: Configuring runner settings..."

RUNNER_CONFIG="/etc/gitlab-runner/config.toml"

# Set concurrency
sed -i "s/^concurrent = .*/concurrent = ${CONCURRENT}/" "${RUNNER_CONFIG}"

# Add check_interval for regulated environments (faster feedback)
if ! grep -q "check_interval" "${RUNNER_CONFIG}"; then
    sed -i '/^concurrent/a check_interval = 3' "${RUNNER_CONFIG}"
fi

# -- Step 5: Configure caching --
log_info "Step 5: Configuring build cache (${CACHE_TYPE})..."

if [[ "${CACHE_TYPE}" == "s3" ]]; then
    if [[ -z "${CACHE_S3_ENDPOINT}" || -z "${CACHE_S3_BUCKET}" ]]; then
        log_warn "S3 cache requires --cache-s3-endpoint and --cache-s3-bucket"
        log_warn "Falling back to local cache"
        CACHE_TYPE="local"
    fi
fi

if [[ "${CACHE_TYPE}" == "s3" ]]; then
    cat >> "${RUNNER_CONFIG}" <<TOML

  [runners.cache]
    Type = "s3"
    Shared = true
    [runners.cache.s3]
      ServerAddress = "${CACHE_S3_ENDPOINT}"
      BucketName = "${CACHE_S3_BUCKET}"
      AccessKey = "${CACHE_S3_ACCESS_KEY}"
      SecretKey = "${CACHE_S3_SECRET_KEY}"
      Insecure = false
TOML
    log_info "S3-compatible cache configured: ${CACHE_S3_ENDPOINT}/${CACHE_S3_BUCKET}"
else
    mkdir -p "${CACHE_PATH}"
    chown gitlab-runner:gitlab-runner "${CACHE_PATH}"
    chmod 750 "${CACHE_PATH}"
    log_info "Local cache directory: ${CACHE_PATH}"
fi

# -- Step 6: Security hardening --
log_info "Step 6: Applying security hardening..."

# Restrict config file permissions
chmod 600 "${RUNNER_CONFIG}"
chown root:root "${RUNNER_CONFIG}"

# Create builds directory with restricted permissions
BUILDS_DIR="/var/lib/gitlab-runner/builds"
mkdir -p "${BUILDS_DIR}"
chown gitlab-runner:gitlab-runner "${BUILDS_DIR}"
chmod 750 "${BUILDS_DIR}"

# Restrict home directory
chmod 700 /home/gitlab-runner

# Set up log rotation
cat > /etc/logrotate.d/gitlab-runner <<EOF
/var/log/gitlab-runner/*.log {
    daily
    rotate 90
    compress
    delaycompress
    missingok
    notifempty
    create 640 gitlab-runner gitlab-runner
}
EOF

# Create audit log directory (iGaming compliance requirement)
AUDIT_LOG_DIR="/var/log/gitlab-runner/audit"
mkdir -p "${AUDIT_LOG_DIR}"
chown gitlab-runner:gitlab-runner "${AUDIT_LOG_DIR}"
chmod 750 "${AUDIT_LOG_DIR}"

log_info "Security hardening applied: restricted permissions, log rotation, audit logging"

# -- Step 7: Docker network for CI --
log_info "Step 7: Creating CI Docker network..."

if [[ "${EXECUTOR}" == "docker" ]]; then
    if command -v docker &>/dev/null; then
        if ! docker network inspect igaming-ci &>/dev/null 2>&1; then
            docker network create \
                --driver bridge \
                --subnet 172.28.0.0/16 \
                --opt com.docker.network.bridge.name=br-igaming-ci \
                igaming-ci
            log_info "Created Docker network: igaming-ci (172.28.0.0/16)"
        else
            log_info "Docker network igaming-ci already exists"
        fi
    fi
fi

# -- Step 8: Prometheus metrics --
log_info "Step 8: Configuring Prometheus metrics endpoint..."

# Enable metrics in config.toml
if ! grep -q "listen_address" "${RUNNER_CONFIG}"; then
    sed -i "1a listen_address = \":${METRICS_PORT}\"" "${RUNNER_CONFIG}"
fi

log_info "Prometheus metrics available at http://$(hostname -f):${METRICS_PORT}/metrics"

# -- Step 9: Systemd service --
log_info "Step 9: Configuring systemd service..."

gitlab-runner install --user gitlab-runner --working-directory "${BUILDS_DIR}" 2>/dev/null || true
systemctl daemon-reload
systemctl enable gitlab-runner
systemctl restart gitlab-runner

log_info "GitLab Runner service started and enabled"

# -- Step 10: Verification --
log_info "Step 10: Verifying installation..."

if gitlab-runner verify 2>&1 | grep -q "is alive"; then
    log_info "Runner verification: PASSED"
else
    log_warn "Runner verification: runner may need connectivity to GitLab"
fi

log_info "Runner status:"
gitlab-runner status || true

cat <<SUMMARY

=============================================================================
  GitLab Runner Installation Complete
=============================================================================
  Runner name:    ${RUNNER_NAME}
  GitLab URL:     ${GITLAB_URL}
  Executor:       ${EXECUTOR}
  Tags:           ${RUNNER_TAGS}
  Concurrent:     ${CONCURRENT}
  Cache:          ${CACHE_TYPE} (${CACHE_PATH})
  Metrics:        http://$(hostname -f):${METRICS_PORT}/metrics
  Config:         ${RUNNER_CONFIG}
  Builds dir:     ${BUILDS_DIR}
  Audit logs:     ${AUDIT_LOG_DIR}
  Air-gapped:     ${AIR_GAPPED}
=============================================================================
  Next steps:
  1. Verify runner appears in GitLab: Settings > CI/CD > Runners
  2. Tag jobs in .gitlab-ci.yml to match runner tags
  3. For air-gapped: pre-load Docker images to local registry
  4. Monitor metrics at the Prometheus endpoint
=============================================================================
SUMMARY
