# ── Minutes in Seconds — Dockerfile ──────────────────────────────────────────
# Multi-stage build: install heavy deps, then copy app.

FROM python:3.11-slim AS base

# System deps: ffmpeg for audio extraction, build tools for some Python wheels
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    git \
    curl \
    build-essential \
    libsndfile1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# ── Python deps (cached layer) ────────────────────────────────────────────────
COPY requirements.txt .

# Install PyTorch CPU-only first (much smaller than CUDA build — suitable for Zeabur)
RUN pip install --no-cache-dir torch==2.2.2 --index-url https://download.pytorch.org/whl/cpu

# Install everything else
RUN pip install --no-cache-dir -r requirements.txt

# ── App source ────────────────────────────────────────────────────────────────
COPY . .

# Create runtime dirs
RUN mkdir -p uploads outputs

# Pre-download Whisper medium model at build time (avoids cold-start delay)
# Comment this out to reduce image size; model will download on first request instead.
# RUN python -c "import whisper; whisper.load_model('medium')"

# ── Runtime config ────────────────────────────────────────────────────────────
ENV HOST=0.0.0.0
ENV PORT=8080
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

# Whisper model cache dir — point to a persistent volume in Zeabur
ENV XDG_CACHE_HOME=/app/.cache

EXPOSE 8080

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
  CMD curl -f http://localhost:8080/ || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
