FROM python:3.14-slim

# Create non-root user
RUN groupadd -r appgroup && useradd -r -g appgroup -s /sbin/nologin appuser

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY *.py ./

RUN chown -R appuser:appgroup /app

USER appuser

HEALTHCHECK --interval=60s --timeout=10s --start-period=10s --retries=3 \
    CMD python -c "import sys; sys.exit(0)"

CMD ["python", "segment_messages.py"]
