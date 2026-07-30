# Backup container — all dependencies for wasabi-backup scripts
# Build: docker build -t wasabi-backup .
# Run:   docker run --rm \
#          -v /root/.wasabi:/root/.wasabi:ro \
#          -v /opt/backups:/opt/backups \
#          -v /var/log:/var/log \
#          -v /var/run/docker.sock:/var/run/docker.sock \
#          -e PROMETHEUS_PUSHGATEWAY_URL=http://localhost:9091 \
#          wasabi-backup

FROM debian:bookworm-slim

# Avoid interactive prompts during apt installs
ENV DEBIAN_FRONTEND=noninteractive

# Install runtime dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    # AWS CLI v2 dependencies
    curl \
    unzip \
    ca-certificates \
    # GPG for AES-256 encryption
    gnupg \
    # PostgreSQL client for pg_dump / pg_restore
    postgresql-client \
    # Python for manifest parsing in wasabi-restore.sh
    python3 \
    # Utilities used by scripts
    gzip \
    coreutils \
    && rm -rf /var/lib/apt/lists/*

# Install AWS CLI v2
RUN curl -fsSL "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o /tmp/awscliv2.zip \
    && unzip -q /tmp/awscliv2.zip -d /tmp \
    && /tmp/aws/install \
    && rm -rf /tmp/awscliv2.zip /tmp/aws

# Create working directories
RUN mkdir -p /opt/backups/wasabi-tmp /opt/backups/wasabi-restore-tmp \
    && chmod 700 /opt/backups/wasabi-tmp /opt/backups/wasabi-restore-tmp

# Copy scripts
COPY wasabi-backup-encrypted.sh /opt/scripts/wasabi-backup-encrypted.sh
COPY wasabi-restore.sh /opt/scripts/wasabi-restore.sh
COPY backup-status-refresh.sh /opt/scripts/backup-status-refresh.sh

RUN chmod +x /opt/scripts/*.sh

# Credentials and passphrase are mounted at runtime — never baked into the image
# Mount: -v /root/.wasabi:/root/.wasabi:ro
VOLUME ["/root/.wasabi"]

# No USER and no HEALTHCHECK here, deliberately.
#
# USER: this is an operator utility, not a service. It reads the 0700 root-owned
# credential directory mounted at /root/.wasabi, talks to the Docker socket to
# quiesce containers, and reads /var/log to archive it. Every one of those needs
# uid 0 on the host side. The compensating control is the run command above:
# mount only what a given run needs, mount it :ro where possible, and use --rm so
# the container does not outlive the backup. Do not use this image as a base for
# anything that serves traffic.
#
# HEALTHCHECK: the container runs to completion. There is no steady state to
# probe, and the exit code is the health signal. Alert on the backup's exit
# status and on the Pushgateway metric it publishes, not on container liveness.

# Default entrypoint runs the backup; override for restore
ENTRYPOINT ["/opt/scripts/wasabi-backup-encrypted.sh"]
