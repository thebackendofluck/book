# syntax=docker/dockerfile:1.7
# Multi-stage: build wheels on python:3.12-slim, run on distroless-python.
FROM python:3.14-slim AS builder
WORKDIR /build
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

FROM gcr.io/distroless/python3-debian12:nonroot
WORKDIR /app
# Copy installed site-packages from builder.
COPY --from=builder /install /usr/local
COPY app /app/app
USER nonroot
# readOnlyRootFilesystem is enforced at the Pod securityContext level;
# /tmp is mounted as emptyDir by the Deployment.
# No HEALTHCHECK: the distroless runtime ships no shell, curl or wget, so a
# Dockerfile HEALTHCHECK has nothing to exec. That is the trade we accepted when
# we chose distroless. Health lives in the Deployment's httpGet readiness and
# liveness probes against /health, which the kubelet runs from outside the
# container and which work regardless of what the image contains.
EXPOSE 8000
ENTRYPOINT ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
