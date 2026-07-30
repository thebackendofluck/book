FROM python:3.14-slim

# Create non-root user
RUN groupadd -r locust && useradd -r -g locust -s /sbin/nologin locust

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    unzip \
    ca-certificates \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml ./

RUN pip install --no-cache-dir poetry && \
    poetry config virtualenvs.create false && \
    poetry install --no-dev

COPY schemas schemas
COPY locusts .
COPY loadsim .
COPY gizmos .
COPY keys .
COPY start_master.sh .
COPY start_worker.sh .
COPY pre-stop.sh .

RUN mkdir -p /home/locust && \
    touch /home/locust/locust_stats_history.csv && \
    chown -R locust:locust /home/locust /app

USER locust

EXPOSE 8089

HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8089/stats/requests || exit 1
