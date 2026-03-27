# Multi-stage Dockerfile for MeshWiki with Rust graph engine
#
# Build from repo root:
#   docker build -t meshwiki:latest .
#
# Base image digest: python:3.12-slim — update when bumping Python version.

# ── Stage 1: Build Rust graph engine ────────────────────────
FROM python:3.12-slim@sha256:3d5ed973e45820f5ba5e46bd065bd88b3a504ff0724d85980dcd05eab361fcf4 AS rust-builder

RUN apt-get update && apt-get install -y --no-install-recommends \
        curl build-essential && \
    rm -rf /var/lib/apt/lists/*

# Install Rust
RUN curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y
ENV PATH="/root/.cargo/bin:$PATH"

# Install Maturin
RUN pip install --no-cache-dir maturin

WORKDIR /build
COPY graph-core/ ./graph-core/

# Build the graph_core wheel
WORKDIR /build/graph-core
RUN PYO3_USE_ABI3_FORWARD_COMPATIBILITY=1 maturin build --release --out /wheels

# ── Stage 2: Runtime image ──────────────────────────────────
FROM python:3.12-slim@sha256:3d5ed973e45820f5ba5e46bd065bd88b3a504ff0724d85980dcd05eab361fcf4

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Create non-root user (UID/GID 1001)
RUN groupadd -r app --gid 1001 && \
    useradd -r -g app --uid 1001 --no-log-init app

# Install Python dependencies
COPY src/pyproject.toml .
RUN pip install --no-cache-dir .

# Install the graph_core wheel from builder stage
COPY --from=rust-builder /wheels/*.whl /tmp/
RUN pip install --no-cache-dir /tmp/*.whl && rm -rf /tmp/*.whl

# Copy application code
COPY src/meshwiki/ ./meshwiki/

# Create data directory and transfer ownership to app user
RUN mkdir -p /data/pages && chown -R app:app /data /app

ENV MESHWIKI_DATA_DIR=/data/pages

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD python3 -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health/live')" || exit 1

USER app

CMD ["uvicorn", "meshwiki.main:app", "--host", "0.0.0.0", "--port", "8000"]
