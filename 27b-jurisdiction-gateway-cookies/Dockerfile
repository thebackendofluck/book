# syntax=docker/dockerfile:1.7
#
# Multi-stage build for jurisdiction-gateway.
# Stage 1: install dependencies into a virtualenv on python:3.12-slim.
# Stage 2: copy the virtualenv and app onto the SAME base image, as a non-root user.
#
# Security notes:
#  * One base image, pinned by digest, for both stages. An earlier version built wheels on
#    Python 3.12 and copied them into a Python 3.11 distroless image; compiled wheels
#    (pydantic-core, pyyaml, uvloop) are tagged per interpreter version and failed or loaded
#    the wrong build. Build and runtime now always run the same interpreter.
#    The digest was read from the registry on 2026-11-21; re-pin on the next rebuild.
#  * USER 10010 matches runAsUser in k8s/deployment.yaml. No login shell, no home directory.
#  * No build tools in the final layer.
#  * HEALTHCHECK uses the stdlib, so the image needs no curl. K8s uses its own httpGet probe.

ARG PY_BASE=python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b

# ---------------- builder ----------------
FROM ${PY_BASE} AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build
COPY requirements.txt .

RUN python -m venv /opt/venv \
 && /opt/venv/bin/pip install -r requirements.txt

# ---------------- runtime ----------------
FROM ${PY_BASE}

LABEL org.opencontainers.image.title="jurisdiction-gateway" \
      org.opencontainers.image.description="Central policy decision point for cross-border data transfers under GDPR/LGPD/ePrivacy + cookie consent." \
      org.opencontainers.image.vendor="AcmeToCasino" \
      org.opencontainers.image.source="https://github.com/acmetocasino/egambling-book/tree/main/jurisdiction-gateway" \
      org.opencontainers.image.licenses="MIT"

ENV PATH=/opt/venv/bin:${PATH} \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN groupadd --gid 10010 jgw \
 && useradd --uid 10010 --gid 10010 --no-create-home --shell /usr/sbin/nologin jgw \
 && install -d -o jgw -g jgw /var/log/jurisdiction-gateway /var/lib/jurisdiction-gateway

COPY --from=builder /opt/venv /opt/venv
COPY app/ /app/app/

WORKDIR /app

USER 10010:10010

EXPOSE 8210

HEALTHCHECK --interval=10s --timeout=3s --retries=5 \
  CMD ["python", "-c", "import urllib.request as u; u.urlopen('http://127.0.0.1:8210/v1/healthz', timeout=2)"]

ENTRYPOINT ["python", "-m", "app.main"]
