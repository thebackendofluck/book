FROM python:3.14-slim

# Create non-root user
RUN groupadd -r appgroup && useradd -r -g appgroup -s /sbin/nologin appuser

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py .

RUN chown -R appuser:appgroup /app

# Build metadata injected at image build time
ARG BUILD_BRANCH=unknown
ARG BUILD_REVISION=unknown
ARG BUILD_TIMESTAMP=unknown

ENV BUILD_BRANCH=${BUILD_BRANCH}
ENV BUILD_REVISION=${BUILD_REVISION}
ENV BUILD_TIMESTAMP=${BUILD_TIMESTAMP}
ENV PORT=8080

USER appuser

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/health')" || exit 1

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]
