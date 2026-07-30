FROM python:3.14-slim

# Run as non-root.
RUN groupadd -r app && useradd -r -g app -s /sbin/nologin app

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py .

USER app

EXPOSE 8000
ENV PYTHONUNBUFFERED=1

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)" || exit 1

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
