# ── Stage 1: Build / dependency cache ────────────────────────────────────────
FROM python:3.14-slim AS builder

WORKDIR /build

RUN pip install --upgrade pip
COPY requirements.txt .
RUN pip install --prefix=/install --no-cache-dir -r requirements.txt

# ── Stage 2: Runtime ──────────────────────────────────────────────────────────
FROM python:3.14-slim AS runtime

# Non-root user
RUN groupadd --system appgroup && useradd --system --gid appgroup --no-create-home appuser

WORKDIR /app

# Copy installed packages
COPY --from=builder /install /usr/local

# Copy application code
COPY --chown=appuser:appgroup main.py models.py database.py wagering.py sigap_bonus.py ./

USER appuser

ENV PORT=8082
ENV DATABASE_URL=postgresql+asyncpg://bonus:bonus@postgres:5432/bonus_engine
ENV REDIS_URL=redis://redis:6379/1
ENV OPERATOR_CNPJ=00.000.000/0001-00
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

EXPOSE 8082

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8082/health')"

CMD ["python", "main.py"]
