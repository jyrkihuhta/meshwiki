#!/bin/bash
set -e

echo "=== Validating branch for PR ==="

git fetch origin main

LOCAL=$(git rev-parse HEAD)
REMOTE=$(git rev-parse origin/main)

if git merge-base --is-ancestor HEAD origin/main 2>/dev/null; then
    echo "✓ Branch is up-to-date with main"
else
    echo "⚠ Branch is behind main. Run: git rebase origin/main"
fi

echo ""
echo "=== Running tests ==="
cd src && pytest tests/ -q --tb=short && cd ..

echo ""
echo "=== Checking formatting (matching CI versions) ==="
cd src
python3 -m venv /tmp/lint-check-venv 2>/dev/null || true
VENV=/tmp/lint-check-venv
if [ ! -f "$VENV/bin/black" ]; then
    python3 -m venv $VENV
fi
$VENV/bin/pip install -q black==26.3.1 ruff==0.15.8 isort==8.0.1
$VENV/bin/black --check . || { echo "Run: $VENV/bin/black ."; exit 1; }
$VENV/bin/isort --check-only --profile black . || { echo "Run: $VENV/bin/isort ."; exit 1; }
$VENV/bin/ruff check . || { echo "Fix issues above"; exit 1; }
cd ..

echo ""
echo "=== Running E2E smoke tests (requires Rust) ==="
if command -v maturin &> /dev/null; then
    cd src
    MESHWIKI_DATA_DIR=/tmp/meshwiki-smoke MESHWIKI_GRAPH_WATCH=false \
        python -m uvicorn meshwiki.main:app --host 127.0.0.1 --port 8080 &
    SERVER_PID=$!
    sleep 5
    pytest e2e/test_navigation.py -v -k "TestTocSidebar or TestHeaderSearch" --browser chromium
    kill $SERVER_PID 2>/dev/null || true
    cd ..
else
    echo "⚠ maturin not found, skipping E2E tests. Run manually:"
    echo "   cd src && pytest e2e/ -v --browser chromium"
fi

echo ""
echo "✓ Validation complete"
