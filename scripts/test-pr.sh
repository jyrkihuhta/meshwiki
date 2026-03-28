#!/bin/bash
set -e

# Test a PR locally before approving/merging
# Usage: ./scripts/test-pr.sh <PR_NUMBER>

if [ -z "$1" ]; then
    echo "Usage: $0 <PR_NUMBER>"
    echo "Example: $0 15"
    exit 1
fi

PR_NUM=$1
CURRENT_BRANCH=$(git branch --show-current)

echo "=== Testing PR #$PR_NUM locally ==="
echo "Current branch: $CURRENT_BRANCH"

# Fetch PR
echo ""
echo "Fetching PR #$PR_NUM..."
git fetch origin pull/$PR_NUM/head:pr-$PR_NUM

# Checkout PR branch
echo ""
echo "Checking out PR #$PR_NUM branch (pr-$PR_NUM)..."
git checkout pr-$PR_NUM

# Install dependencies if needed
echo ""
echo "Installing dependencies..."
cd src && pip install -e . -q 2>/dev/null || true

# Run E2E tests
echo ""
echo "Running E2E tests against localhost..."
cd ..
pip3 install playwright pytest-playwright -q --break-system-packages 2>/dev/null || true
python3 -m playwright install chromium 2>/dev/null || true
python3 -m pytest src/e2e/ -v

# Return to original branch
echo ""
echo "Returning to $CURRENT_BRANCH..."
git checkout $CURRENT_BRANCH

echo ""
echo "=== PR #$PR_NUM tested successfully ==="
echo "Review the test results above. If tests pass, the PR is safe to merge."
