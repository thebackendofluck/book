# =============================================================================
# Hardened Scanner Container for iGaming Supply Chain Security
# Includes: Syft, Grype, Trivy, cosign, and the dependency-scanner aggregator
#
# Build:
#   docker build -t acme-casino/supply-chain-scanner:latest .
#
# Run:
#   docker run --rm -v /var/run/docker.sock:/var/run/docker.sock \
#     acme-casino/supply-chain-scanner:latest \
#     python3 /opt/scanner/dependency-scanner.py --image myapp:latest
# =============================================================================

# --- Stage 1: Fetch tools ---------------------------------------------------
FROM alpine:3.24 AS tools

ARG TRIVY_VERSION=0.50.1
ARG GRYPE_VERSION=0.74.7
ARG SYFT_VERSION=1.1.0
ARG COSIGN_VERSION=2.2.3

RUN apk add --no-cache curl tar gzip

WORKDIR /tools

# Trivy
RUN curl -sSfL "https://github.com/aquasecurity/trivy/releases/download/v${TRIVY_VERSION}/trivy_${TRIVY_VERSION}_Linux-64bit.tar.gz" \
    | tar xz -C /tools trivy

# Grype
RUN curl -sSfL "https://github.com/anchore/grype/releases/download/v${GRYPE_VERSION}/grype_${GRYPE_VERSION}_linux_amd64.tar.gz" \
    | tar xz -C /tools grype

# Syft
RUN curl -sSfL "https://github.com/anchore/syft/releases/download/v${SYFT_VERSION}/syft_${SYFT_VERSION}_linux_amd64.tar.gz" \
    | tar xz -C /tools syft

# Cosign
RUN curl -sSfL -o /tools/cosign \
    "https://github.com/sigstore/cosign/releases/download/v${COSIGN_VERSION}/cosign-linux-amd64" \
    && chmod +x /tools/cosign

# --- Stage 2: Final hardened image -------------------------------------------
FROM python:3.14-slim-bookworm

LABEL maintainer="security-team@acme-casino.io"
LABEL description="Supply chain security scanner for iGaming platforms"
LABEL org.opencontainers.image.source="https://github.com/acme-casino/supply-chain-scanner"

# Security: Run as non-root
RUN groupadd -r scanner && useradd -r -g scanner -d /opt/scanner -s /sbin/nologin scanner

# Install minimal system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    git \
    jq \
    curl \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

# Copy tools from builder stage
COPY --from=tools /tools/trivy /usr/local/bin/trivy
COPY --from=tools /tools/grype /usr/local/bin/grype
COPY --from=tools /tools/syft  /usr/local/bin/syft
COPY --from=tools /tools/cosign /usr/local/bin/cosign

# Install Python dependencies
COPY requirements.txt /opt/scanner/requirements.txt
RUN pip install --no-cache-dir -r /opt/scanner/requirements.txt

# Copy scanner scripts
COPY dependency-scanner.py /opt/scanner/dependency-scanner.py
COPY sbom-pipeline.sh      /opt/scanner/sbom-pipeline.sh
RUN chmod +x /opt/scanner/sbom-pipeline.sh

# Create output directories
RUN mkdir -p /opt/acme-casino/sbom/{build,registry,runtime,attestations} \
    /opt/acme-casino/scans \
    && chown -R scanner:scanner /opt/acme-casino

# Security hardening
# Remove unnecessary setuid/setgid binaries
RUN find / -perm /6000 -type f -exec chmod a-s {} + 2>/dev/null || true

# Drop all capabilities
# (Applied at runtime via --cap-drop=ALL)

# Read-only root filesystem marker
LABEL org.opencontainers.image.readonly-rootfs="true"

# Health check
HEALTHCHECK --interval=60s --timeout=10s --retries=3 \
    CMD trivy --version && grype version && syft version

WORKDIR /opt/scanner
USER scanner

ENTRYPOINT ["python3", "/opt/scanner/dependency-scanner.py"]
CMD ["--help"]
