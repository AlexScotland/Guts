#!/usr/bin/env bash
# =============================================================================
# verify-fix.sh — Run on the HOST (where Docker is available) after rebuilding
# and restarting the frontend container.
# =============================================================================
set -euo pipefail

CONTAINER="${1:-frontend-1}"
echo "=== Verifying container: $CONTAINER ==="
echo

# 1. What is PID 1 actually running?
echo "--- [1] PID 1 command ---"
CMDLINE=$(docker exec "$CONTAINER" cat /proc/1/cmdline 2>/dev/null | tr '\0' ' ')
echo "  $CMDLINE"
if echo "$CMDLINE" | grep -qE "next (start|dev)|npm run (start|dev)"; then
    echo "  ❌ BAD: running 'next start' or 'next dev' — will ENOENT on required-server-files.json"
    exit 1
elif echo "$CMDLINE" | grep -q "node server.js"; then
    echo "  ✅ GOOD: running the standalone server (node server.js)"
else
    echo "  ⚠️  UNEXPECTED: $CMDLINE"
fi
echo

# 2. Is the standalone server.js present?
echo "--- [2] /app/server.js ---"
docker exec "$CONTAINER" ls -la /app/server.js 2>&1
docker exec "$CONTAINER" head -3 /app/server.js 2>&1
echo

# 3. Is required-server-files.json absent? (expected for standalone)
echo "--- [3] required-server-files.json (should be ABSENT) ---"
if docker exec "$CONTAINER" test -f /app/.next/required-server-files.json 2>/dev/null; then
    echo "  Present (unexpected for standalone build)"
else
    echo "  ✅ Absent (correct for standalone build)"
fi
echo

# 4. Is the static assets directory present?
echo "--- [4] /app/.next/static/ ---"
docker exec "$CONTAINER" ls /app/.next/static/ 2>&1 | head -5
echo

# 5. Does the app respond?
echo "--- [5] HTTP check ---"
PORT=$(docker port "$CONTAINER" 2>/dev/null | grep 3000 | head -1 | cut -d: -f1 || echo "3000")
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" "http://localhost:${PORT}/" 2>/dev/null || echo "000")
echo "  HTTP $HTTP_CODE"
if [ "$HTTP_CODE" = "200" ]; then
    echo "  ✅ App is responding"
elif [ "$HTTP_CODE" = "500" ]; then
    echo "  ❌ Still returning 500 — check logs: docker logs $CONTAINER --tail 30"
    exit 1
else
    echo "  ⚠️  Unexpected status (container may still be starting)"
fi
echo
echo "=== Verification complete ==="
