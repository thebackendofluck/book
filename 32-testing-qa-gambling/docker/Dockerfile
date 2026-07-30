# Multi-stage Dockerfile for iGaming Load Testing Framework
# Uses uv for fast dependency installation - Python 3.12

# Stage 1: Base image with Python and security hardening
FROM python:3.14-slim AS base

# Security: Run as non-root user
RUN groupadd -r locust && useradd -r -g locust locust

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    unzip \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

# Stage 2: Builder with uv for fast dependency installation
FROM base AS builder

# Install uv (10-100x faster than pip)
RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.cargo/bin:$PATH"

# Install AWS CLI v2
RUN curl "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o "awscliv2.zip" \
    && unzip awscliv2.zip \
    && ./aws/install \
    && rm -rf awscliv2.zip aws

WORKDIR /app

# Copy dependency files
COPY pyproject.toml ./

# Create virtual environment and install dependencies with uv
RUN uv venv /app/.venv \
    && . /app/.venv/bin/activate \
    && uv pip install --no-cache-dir \
        locust>=2.20.0 \
        boto3>=1.34.0 \
        pydantic>=2.5.0 \
        httpx>=0.26.0 \
        structlog>=23.2.0 \
        uvloop>=0.19.0 \
        numpy>=1.26.0 \
        scipy>=1.12.0 \
        redis>=5.0.0 \
        asyncpg>=0.29.0 \
        aiohttp>=3.9.0 \
        psycopg2-binary>=2.9.0 \
        pyyaml>=6.0.0

# Stage 3: Runtime image
FROM base AS runtime

WORKDIR /app

# Copy AWS CLI from builder
COPY --from=builder /usr/local/aws-cli /usr/local/aws-cli
RUN ln -s /usr/local/aws-cli/v2/current/bin/aws /usr/local/bin/aws

# Copy virtual environment from builder
COPY --from=builder /app/.venv /app/.venv

# Copy application code
COPY load-testing/ ./load-testing/
COPY testing-qa/ ./testing-qa/
COPY config/ ./config/

# Copy scripts
COPY docker/start_master.sh ./
COPY docker/start_worker.sh ./

# Make scripts executable
RUN chmod +x start_master.sh start_worker.sh

# Set up environment
ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONPATH="/app"
ENV PYTHONUNBUFFERED=1

# Create log directory
RUN mkdir -p /home/locust \
    && touch /home/locust/locust_stats_history.csv \
    && chown -R locust:locust /home/locust /app

# Security: Use non-root user
USER locust

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8089/stats/requests || exit 1

# Expose Locust web UI port
EXPOSE 8089

# Default command
CMD ["locust", "-f", "load-testing/locustfile.py"]
