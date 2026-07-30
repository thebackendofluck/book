FROM python:3.14-slim

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    tesseract-ocr-eng \
    tesseract-ocr-spa \
    tesseract-ocr-por \
    tesseract-ocr-fra \
    tesseract-ocr-deu \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender-dev \
    libgomp1 \
    libgl1-mesa-glx \
    ffmpeg \
    libpq-dev \
    gcc \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user
RUN groupadd -r appgroup && useradd -r -g appgroup -s /sbin/nologin appuser

# Set working directory
WORKDIR /app

# Copy requirements
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Create necessary directories and set ownership
RUN mkdir -p /logs /data /templates /models && \
    chown -R appuser:appgroup /app /logs /data /templates /models

# Drop to non-root user
USER appuser

# Set environment variables
ENV PYTHONUNBUFFERED=1
ENV TESSDATA_PREFIX=/usr/share/tesseract-ocr/4.00/tessdata/

# Health check
HEALTHCHECK --interval=30s --timeout=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/health')"

EXPOSE 8080

# Run the application
CMD ["python", "app.py"]
