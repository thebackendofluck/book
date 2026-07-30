FROM python:3.14-slim AS builder

WORKDIR /build
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

FROM python:3.14-slim

RUN groupadd -r mailer && useradd -r -g mailer mailer

WORKDIR /app
COPY --from=builder /install /usr/local
COPY . .

USER mailer

EXPOSE 8888

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8888/health')" || exit 1

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8888"]
