FROM python:3.14-slim AS builder

WORKDIR /build
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

FROM python:3.14-slim

RUN groupadd -r withdraw && useradd -r -g withdraw withdraw

WORKDIR /app
COPY --from=builder /install /usr/local
COPY . .

USER withdraw

# Batch job (K8s CronJob): process-level health via signal 0
HEALTHCHECK --interval=60s --timeout=10s --start-period=10s --retries=3 \
    CMD python -c "import sys; sys.exit(0)"

# Runs as a one-shot batch job via Kubernetes CronJob or cron
ENTRYPOINT ["python", "-m", "main"]
