# syntax=docker/dockerfile:1.7
#
# Hardened multi-stage build for jurisdiction-gateway.
# Stage 1: build wheels on a full slim image.
# Stage 2: copy wheels + app into a distroless runtime with no shell, no pkg manager.
#
# Security notes:
#  * Pinned base digests pin supply chain at build time (re-pin on next rebuild).
#  * Distroless runtime removes apt, bash, coreutils — shrinks attack surface and
#    prevents attackers who land in the container from running arbitrary commands.
#  * USER nonroot (uid 65532) is baked into the distroless image; we set UID
#    consistently with the K8s deployment's securityContext.
#  * No build tools in the final layer.
#  * `--no-install-recommends` + `rm -rf /var/lib/apt/lists/*` keeps builder lean.
#  * HEALTHCHECK is driven by the K8s probe; Dockerfile HEALTHCHECK omitted
#    because distroless has no curl/wget; stdlib urllib probe works but a K8s
#    httpGet probe is the canonical path.

ARG PY_BUILDER=python:3.12-slim@sha256:4efa69bf17cfbd83a9942e60e2642335c3b397448e00410063a0421f9727c4c4
ARG PY_RUNTIME=gcr.io/distroless/python3-debian12:nonroot

# ---------------- builder ----------------
FROM ${PY_BUILDER} AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build
COPY requirements.txt .

RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential \
 && rm -rf /var/lib/apt/lists/*

# Install into an isolated prefix that we will copy into the runtime image.
RUN pip install --prefix=/install -r requirements.txt

# ---------------- runtime ----------------
FROM ${PY_RUNTIME}

LABEL org.opencontainers.image.title="jurisdiction-gateway" \
      org.opencontainers.image.description="Central policy decision point for cross-border data transfers under GDPR/LGPD/ePrivacy + cookie consent." \
      org.opencontainers.image.vendor="AcmeToCasino" \
      org.opencontainers.image.source="https://github.com/acmetocasino/egambling-book/tree/main/jurisdiction-gateway" \
      org.opencontainers.image.licenses="MIT"

ENV PYTHONPATH=/install/lib/python3.12/site-packages \
    PATH=/install/bin:${PATH} \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY --from=builder /install /install
COPY app/ /app/app/

WORKDIR /app

# distroless already runs as nonroot (uid 65532); confirm for grep-ability.
USER nonroot

EXPOSE 8210

# No shell available: ENTRYPOINT must be a direct exec form.
ENTRYPOINT ["python", "-m", "app.main"]
