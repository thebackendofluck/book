FROM python:3.14-slim

# poppler-utils provides `pdftotext` for parsers that need layout-preserving
# text extraction (Norway, some other PDF-only regulators).
RUN apt-get update && apt-get install -y --no-install-recommends \
    poppler-utils \
    tesseract-ocr \
    && rm -rf /var/lib/apt/lists/*

# Pin UID/GID so the host can chown the bind-mounted output dir to a known
# numeric owner (security: avoids running as root in the container OR opening
# the dir to world-write).
RUN groupadd -r -g 1500 app && useradd -r -u 1500 -g 1500 -s /sbin/nologin app

WORKDIR /app

COPY collector-requirements.txt .
RUN pip install --no-cache-dir -r collector-requirements.txt

# We expect the parent collector source to be bind-mounted at /src.
ENV PYTHONPATH=/src
WORKDIR /src

USER app

# No HEALTHCHECK: run.py scrapes the regulator sources once and exits. A
# run-to-completion container has no steady state for the daemon to probe, and a
# HEALTHCHECK that always reports "starting" is worse than none. The signal that
# matters is the exit code, plus whether the expected output files appeared.
ENTRYPOINT ["python", "run.py"]
