FROM python:3.14-slim

# System dependencies for packet capture
RUN apt-get update && apt-get install -y --no-install-recommends \
    tcpdump \
    libpcap-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY monitor.py .

# Run as non-root (CIS Docker Benchmark 4.1)
RUN useradd --create-home --shell /bin/bash monitor
USER monitor

EXPOSE 5000

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import requests; requests.get('http://127.0.0.1:5000')" || exit 1

CMD ["python", "monitor.py", "--interface", "eth0"]
