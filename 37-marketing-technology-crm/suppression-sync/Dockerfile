FROM python:3.14-slim AS builder

WORKDIR /build
RUN apt-get update && apt-get install -y --no-install-recommends libxml2-dev libxslt-dev gcc && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

FROM python:3.14-slim

RUN apt-get update && apt-get install -y --no-install-recommends libxml2 libxslt1.1 && rm -rf /var/lib/apt/lists/*
RUN groupadd -r suppression && useradd -r -g suppression suppression

WORKDIR /app
COPY --from=builder /install /usr/local
COPY . .

USER suppression

HEALTHCHECK --interval=60s --timeout=10s --start-period=10s --retries=3 \
    CMD python -c "import sys; sys.exit(0)"

ENTRYPOINT ["python", "-m", "main"]
