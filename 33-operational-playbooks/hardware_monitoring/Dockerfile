# Dockerfile for Hardware Monitoring Agent - Chapter 23: Operational Playbooks
#
# Builds a monitoring container with all tools required for
# comprehensive hardware health data collection.
#
# Note: This container requires --privileged and host device access at runtime
# to read hardware sensors. The USER directive runs the app as non-root;
# however, privileged mode must be granted explicitly at runtime via docker-compose.
#
# Part of the iGaming Platform Engineering book.

FROM python:3.14-slim

# Install system dependencies for hardware monitoring
RUN apt-get update && apt-get install -y --no-install-recommends \
    smartmontools \
    nvme-cli \
    ipmitool \
    lm-sensors \
    ethtool \
    dmidecode \
    procps \
    net-tools \
    curl \
    jq \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user
RUN groupadd -r monitor && useradd -r -g monitor -s /sbin/nologin monitor

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Create app directory
WORKDIR /app

# Copy application code
COPY hardware_monitor.sh .
COPY data_collector.py .
COPY ml_predictor.py .

# Create data directory and set ownership
RUN mkdir -p /app/data /app/logs && \
    chown -R monitor:monitor /app

USER monitor

# Set environment variables
ENV PYTHONUNBUFFERED=1
ENV COLLECTION_INTERVAL=300
ENV ELASTICSEARCH_HOST=elasticsearch:9200
ENV INFLUXDB_HOST=influxdb:8086
ENV PROMETHEUS_PUSHGATEWAY=pushgateway:9091

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import psutil; print('Agent healthy')" || exit 1

# Run the monitoring agent
CMD ["python", "data_collector.py", "--interval", "300", "--duration", "8760"]
