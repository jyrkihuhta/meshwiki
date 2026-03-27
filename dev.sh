#!/usr/bin/env bash
# MeshWiki local development startup script
# Builds the Rust graph engine and starts the FastAPI server.
#
# Usage:
#   ./dev.sh              # Build engine + start server
#   ./dev.sh --skip-build # Start server without rebuilding Rust
#   ./dev.sh --build-only # Build engine without starting server

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
GRAPH_CORE_DIR="$ROOT_DIR/graph-core"
SRC_DIR="$ROOT_DIR/src"

SKIP_BUILD=false
BUILD_ONLY=false

for arg in "$@"; do
    case "$arg" in
        --skip-build) SKIP_BUILD=true ;;
        --build-only) BUILD_ONLY=true ;;
        -h|--help)
            echo "Usage: ./dev.sh [OPTIONS]"
            echo ""
            echo "Options:"
            echo "  --skip-build   Start server without rebuilding Rust engine"
            echo "  --build-only   Build Rust engine without starting server"
            echo "  -h, --help     Show this help message"
            exit 0
            ;;
        *)
            echo "Unknown option: $arg"
            exit 1
            ;;
    esac
done

# ── Build Rust graph engine ──────────────────────────────────

if [ "$SKIP_BUILD" = false ]; then
    echo "==> Building Rust graph engine..."

    # Ensure cargo is available
    if ! command -v cargo &>/dev/null; then
        if [ -f "$HOME/.cargo/env" ]; then
            source "$HOME/.cargo/env"
        else
            echo "ERROR: cargo not found. Install Rust: https://rustup.rs"
            exit 1
        fi
    fi

    # Ensure maturin is available
    if ! python3 -m maturin --version &>/dev/null 2>&1; then
        echo "==> Installing maturin..."
        pip install maturin
    fi

    # Build graph_core with maturin
    cd "$GRAPH_CORE_DIR"
    PYO3_USE_ABI3_FORWARD_COMPATIBILITY=1 python3 -m maturin develop 2>&1
    echo "==> Rust graph engine built successfully"
    cd "$ROOT_DIR"
fi

if [ "$BUILD_ONLY" = true ]; then
    echo "==> Build complete. Exiting (--build-only)."
    exit 0
fi

# ── Install Python dependencies ──────────────────────────────

echo "==> Installing Python dependencies..."
pip install -e "$SRC_DIR[dev]" --quiet

# ── Ensure data directory exists ─────────────────────────────

DATA_DIR="${MESHWIKI_DATA_DIR:-$SRC_DIR/data/pages}"
mkdir -p "$DATA_DIR"

# ── Start the server ─────────────────────────────────────────

echo "==> Starting MeshWiki at http://localhost:8000"
echo "    Data directory: $DATA_DIR"
echo "    Press Ctrl+C to stop"
echo ""

cd "$SRC_DIR"
exec uvicorn meshwiki.main:app --reload --host 127.0.0.1 --port 8000
