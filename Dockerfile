# syntax=docker/dockerfile:1.7
# codeforge - QR / barcode service. Build: docker compose build   Test: docker build --target test .
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# ghostscript renders BWIPP barcodes, libzbar0 decodes, libdmtx0 adds DataMatrix decoding,
# fonts-urw-base35 provides the PostScript fonts BWIPP uses for human readable text
RUN apt-get update \
    && apt-get install -y --no-install-recommends ghostscript fonts-urw-base35 libzbar0 libdmtx0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /srv/codeforge
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app ./app

RUN useradd --system --uid 10001 --home /srv/codeforge --shell /usr/sbin/nologin codeforge \
    && chown -R codeforge:codeforge /srv/codeforge

# ---------------------------------------------------------------- test stage
FROM base AS test
COPY requirements-dev.txt tests ./tests/
RUN pip install -r tests/requirements-dev.txt
USER codeforge
RUN python -m pytest -q tests

# ------------------------------------------------------------- runtime stage
FROM base AS runtime
USER codeforge
EXPOSE 8000
ENV CODEFORGE_HOST=0.0.0.0 CODEFORGE_PORT=8000 CODEFORGE_WORKERS=2
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import sys,urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4).status == 200 else 1)"
CMD ["sh", "-c", "exec uvicorn app.main:app --host ${CODEFORGE_HOST} --port ${CODEFORGE_PORT} --workers ${CODEFORGE_WORKERS} --proxy-headers --forwarded-allow-ips='*' --no-server-header"]
