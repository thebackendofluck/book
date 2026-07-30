# ── Stage 1: build dependencies ───────────────────────────────────────────────
FROM python:3.14-slim AS builder

WORKDIR /build

# System deps for asyncpg / cryptography / scikit-learn
RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc \
        libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt


# ── Stage 2: runtime image ────────────────────────────────────────────────────
FROM python:3.14-slim AS runtime

LABEL maintainer="platform-team" \
      service="aml-fraud" \
      description="AML/Fraud Detection — Brazilian Betting Platform"

# Security: non-root user
RUN groupadd --gid 1001 appgroup && \
    useradd  --uid 1001 --gid appgroup --no-create-home --shell /bin/false appuser

WORKDIR /app

# Copy installed packages from builder
COPY --from=builder /install /usr/local

# Create models directory for ML artifacts
RUN mkdir -p /app/models && chown -R appuser:appgroup /app/models

# Copy application source
COPY --chown=appuser:appgroup . .

# Drop to non-root
USER appuser

# Non-secret defaults only — credentials MUST be injected at runtime via K8s Secrets or docker-compose env_file
ENV DATABASE_URL=postgresql+asyncpg://aml:REPLACE_ME@localhost:5432/aml
ENV NEO4J_URI=bolt://neo4j:7687
ENV NEO4J_USER=neo4j
ENV NEO4J_PASSWORD=REPLACE_ME
ENV COAF_API_URL=https://api.coaf.fazenda.gov.br
ENV COAF_API_KEY=REPLACE_ME
ENV MODEL_PATH=/app/models/fraud_model.joblib
ENV PORT=8080

EXPOSE 8080

# Health check: call /health every 30 s; unhealthy after 3 failures
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/health')"

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080", \
     "--workers", "2", "--no-access-log"]
