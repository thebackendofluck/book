# syntax=docker/dockerfile:1.7
FROM python:3.14-slim AS builder
WORKDIR /build
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

FROM gcr.io/distroless/python3-debian12:nonroot
WORKDIR /app
COPY --from=builder /install /usr/local
COPY app /app/app
USER nonroot
# No HEALTHCHECK: distroless has no shell or curl for the daemon to exec, so
# health is expressed as httpGet probes in the Deployment instead. See
# hub/global-id/Dockerfile for the full reasoning.
EXPOSE 8000
ENTRYPOINT ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
