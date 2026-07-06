# RenovAI 2.0 — Production Container (Cloud Run / Vertex AI)
# Uses gVisor-compatible base for kernel-level isolation.
# Binary Authorization gate must pass before deployment.

FROM python:3.11-slim AS builder

WORKDIR /app

# Install system deps (no build toolchain kept in final image)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

# --- Final stage ---
FROM python:3.11-slim

# gVisor note: this image is designed to run under gVisor (runsc) for
# kernel-level sandboxing. No setuid binaries, no privileged syscalls.
# Test with: docker run --runtime=runsc <image>

WORKDIR /app

RUN groupadd -r renovai && useradd -r -g renovai -d /app -s /bin/false renovai

COPY --from=builder /root/.local /usr/local
COPY . .

# Remove any setuid/setgid bits for gVisor compatibility
RUN find / -perm /6000 -type f -exec chmod a-s {} \; 2>/dev/null || true

RUN chown -R renovai:renovai /app

USER renovai

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

# Health check endpoint (Cloud Run uses this)
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/health')"

EXPOSE 8080

CMD ["uvicorn", "renovai.api.main:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1"]
