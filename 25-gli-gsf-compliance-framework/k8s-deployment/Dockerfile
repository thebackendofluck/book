FROM python:3.14-slim AS builder
WORKDIR /build
COPY pyproject.toml .
RUN pip install --no-cache-dir \
        fastapi==0.118.0 \
        'uvicorn[standard]==0.34.0' \
        prometheus-client==0.21.1 \
        httpx==0.28.1

FROM python:3.14-slim
LABEL org.opencontainers.image.title="gli-compliance-runner"
LABEL org.opencontainers.image.description="Scheduled GLI-12/13/16/21/28 compliance check runner"
LABEL org.opencontainers.image.source="https://github.com/acmetocasino/egambling-book"

RUN apt-get update && \
    apt-get install -y --no-install-recommends curl jq ca-certificates && \
    rm -rf /var/lib/apt/lists/* && \
    groupadd --system --gid 10001 gli && \
    useradd  --system --uid 10001 --gid gli --no-create-home --shell /usr/sbin/nologin gli && \
    mkdir -p /app /var/lib/gli-evidence && \
    chown -R gli:gli /app /var/lib/gli-evidence

COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin/uvicorn /usr/local/bin/uvicorn

WORKDIR /app
COPY --chown=gli:gli runner ./runner
COPY --chown=gli:gli scripts ./scripts
COPY --chown=gli:gli main.py ./main.py

USER gli:gli
EXPOSE 8080
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=2).status == 200 else 1)"

ENTRYPOINT ["python", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]
