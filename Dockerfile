# =====================================================================
# NEPSE Quant Engine — Production Dockerfile
# =====================================================================
# Multi-stage build:
#   1. base    — shared Python environment
#   2. builder — installs dependencies, runs tests
#   3. web     — Streamlit frontend (default)
#   4. api     — FastAPI backend (alternative CMD)
# =====================================================================

# ── Stage 1: Base ─────────────────────────────────────────────────
FROM python:3.12-slim AS base

LABEL maintainer="NEPSE Quant Engine Team"
LABEL description="NEPSE Quant Engine — Algorithmic Trading Platform"

# Prevent Python from buffering stdout/stderr
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DEBIAN_FRONTEND=noninteractive

# Create non-root user
RUN groupadd -r nepse && useradd -r -g nepse -d /app -s /sbin/nologin nepse

# System dependencies for reportlab + weasyprint (optional PDF export)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpango-1.0-0 \
    libpangocairo-1.0-0 \
    libgdk-pixbuf2.0-0 \
    libffi-dev \
    libssl-dev \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# ── Stage 2: Builder ──────────────────────────────────────────────
FROM base AS builder

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source code
COPY src/ src/
COPY app.py .
COPY assets/ assets/

# Compile-check the source
RUN python -m compileall src/

# ── Stage 3: Web (Streamlit) — default target ────────────────────
FROM base AS web

COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin
COPY --from=builder /app /app

# Data volumes
VOLUME ["/app/data", "/app/logs", "/home/nepse/.nepse"]

EXPOSE 8501

USER nepse

HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8501/_stcore/health')" || exit 1

ENTRYPOINT ["python", "-m", "streamlit", "run", "app.py", \
    "--server.address=0.0.0.0", \
    "--server.port=8501", \
    "--server.enableCORS=false", \
    "--server.enableXsrfProtection=true", \
    "--browser.gatherUsageStats=false"]

# ── Stage 4: API (FastAPI) — alternative target via --target=api ──
FROM base AS api

COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin
COPY --from=builder /app /app

VOLUME ["/app/data", "/app/logs", "/home/nepse/.nepse"]

EXPOSE 8000

USER nepse

HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health/live')" || exit 1

ENTRYPOINT ["python", "-m", "uvicorn", "src.api.main:app", \
    "--host=0.0.0.0", "--port=8000", \
    "--workers=2", "--loop=uvloop", "--http=httptools"]
