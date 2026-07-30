FROM python:3.14-slim AS builder

WORKDIR /build
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

FROM python:3.14-slim

RUN groupadd -r balancetool && useradd -r -g balancetool balancetool

WORKDIR /app
COPY --from=builder /install /usr/local
COPY . .

USER balancetool

HEALTHCHECK --interval=60s --timeout=10s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import os; os.kill(os.getpid(), 0)"]

ENTRYPOINT ["python", "-m", "main"]
