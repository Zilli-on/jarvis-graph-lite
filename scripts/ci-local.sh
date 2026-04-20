#!/bin/bash
# ci-local.sh — local CI for jarvis-graph-lite.
# Runs ruff (lint only — format is enforced by the PostToolUse hook,
# not CI, so legacy code doesn't block every PR) + the 277-test
# stdlib unittest suite. Must pass before any PR merges to main.
#
# Usage:
#     bash scripts/ci-local.sh
#
# Exit 0 = ready to merge. Exit 1 = at least one check failed.

set -e

echo "================================"
echo "  jarvis-graph-lite local CI"
echo "================================"

PASS=0
FAIL=0

check() {
    local name="$1"
    local cmd="$2"
    printf "  %-28s" "$name..."
    if eval "$cmd" >/dev/null 2>&1; then
        echo "[PASS]"
        PASS=$((PASS + 1))
    else
        echo "[FAIL]"
        FAIL=$((FAIL + 1))
    fi
}

echo ""
echo "--- Git Checks ---"
check "Clean working tree" "test -z \"\$(git status --porcelain)\""
check "Not on main" "! git branch --show-current | grep -qE '^main$'"

echo ""
echo "--- Code Quality ---"
if command -v ruff &>/dev/null; then
    check "Ruff lint (src + tests)" "ruff check src tests"
else
    echo "  Ruff: SKIP (not installed — install with: pip install ruff)"
fi

echo ""
echo "--- Tests ---"
check "Unittest (stdlib)" "python -m unittest discover -s tests"

echo ""
echo "================================"
echo "  Results: $PASS passed, $FAIL failed"
echo "================================"

if [ "$FAIL" -gt 0 ]; then
    echo "  [FAIL] Fix failures before merging."
    exit 1
fi
echo "  [PASS] Ready to merge."
