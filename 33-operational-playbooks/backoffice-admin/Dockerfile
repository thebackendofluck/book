FROM python:3.14-slim

# Security: run as non-root
RUN addgroup --system appgroup && adduser --system --ingroup appgroup appuser

WORKDIR /app

# Install dependencies first for layer caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source
COPY . .

# Change ownership
RUN chown -R appuser:appgroup /app

USER appuser

# Expose admin port
EXPOSE 8080

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/dashboard/health')" || exit 1

# Run with uvicorn
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "2"]
