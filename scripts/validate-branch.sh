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
echo "=== Checking formatting ==="
black --check src/ 2>/dev/null || echo "Run: black src/"
ruff check src/ 2>/dev/null || echo "Fix issues above"

echo ""
echo "✓ Validation complete"
