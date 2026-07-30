# ── Stage 1: build dependencies ──────────────────────────────────────────────
FROM python:3.14-slim AS builder

WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc \
        libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt


# ── Stage 2: runtime image ────────────────────────────────────────────────────
FROM python:3.14-slim AS runtime

LABEL maintainer="platform-team" \
      service="responsible-gaming" \
      description="Responsible Gaming Service — Brazilian Betting Platform"

# Security: non-root user
RUN groupadd --gid 1001 appgroup && \
    useradd  --uid 1001 --gid appgroup --no-create-home --shell /bin/false appuser

WORKDIR /app

COPY --from=builder /install /usr/local
COPY --chown=appuser:appgroup . .

USER appuser

EXPOSE 8020

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8020/health')"

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8020", \
     "--workers", "2", "--no-access-log"]
