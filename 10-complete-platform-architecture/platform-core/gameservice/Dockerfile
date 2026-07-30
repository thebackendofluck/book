# Multi-stage Dockerfile for the Game Aggregation Layer
#
# Stage 1: builder — installs Python dependencies into a venv
# Stage 2: runtime — minimal image, non-root user, health check
#
# Build:  docker build -t acmeto-gal:latest .
# Run:    docker run -p 8080:8080 --env-file .env acmeto-gal:latest

# ---------------------------------------------------------------------------
# Stage 1 — builder
# ---------------------------------------------------------------------------
FROM python:3.14-slim AS builder

# Prevent .pyc files and enable unbuffered stdout
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build

# Install build dependencies (needed for asyncpg, cryptography)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Create isolated virtual environment
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Install Python dependencies
COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt


# ---------------------------------------------------------------------------
# Stage 2 — runtime
# ---------------------------------------------------------------------------
FROM python:3.14-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    PORT=8080 \
    WORKERS=4

# Install only runtime system libraries
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy virtual environment from builder
COPY --from=builder /opt/venv /opt/venv

# Create non-root user
RUN groupadd --gid 1001 gal && \
    useradd --uid 1001 --gid gal --no-create-home --shell /sbin/nologin gal

WORKDIR /app

# Copy application source
COPY --chown=gal:gal . .

# Switch to non-root user
USER gal

# Expose HTTP port
EXPOSE 8080

# Health check — calls the /health endpoint every 30 seconds
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:${PORT}/health || exit 1

# Start the application
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT} --workers ${WORKERS} --log-level info"]
