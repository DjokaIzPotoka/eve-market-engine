#!/usr/bin/env bash
# Cron wrapper for `eve ingest-all` / `eve ingest-history-all` (Faza 8).
#
# Uses a `mkdir`-based lock (atomic on any POSIX filesystem, no extra dependency -- unlike
# `flock`, which isn't installed by default on macOS) to skip a run if a previous
# invocation is still in flight (e.g. a slow ESI response pushing past the next cron
# tick). Output is appended to data/logs/ -- `_configure_logging()` in cli.py only logs
# to stderr, so this script is the only thing writing to disk; `data/` is already in
# .gitignore.
#
# Usage: cron_ingest.sh [--history]
#   (no args)   -- eve ingest-all then eve derive, run every 5-15 min
#   --history   -- eve ingest-history-all, run once a day
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

LOG_DIR="data/logs"
mkdir -p "$LOG_DIR"

if [[ "${1:-}" == "--history" ]]; then
    COMMAND="ingest-history-all"
    LOG_FILE="$LOG_DIR/ingest-history.log"
    LOCK_DIR="/tmp/eve-market-engine-ingest-history.lock.d"
else
    COMMAND="ingest-all"
    LOG_FILE="$LOG_DIR/ingest.log"
    LOCK_DIR="/tmp/eve-market-engine-ingest.lock.d"
fi

if ! mkdir "$LOCK_DIR" 2>/dev/null; then
    echo "$(date -u '+%Y-%m-%dT%H:%M:%SZ') previous run still in progress, skipping" >> "$LOG_FILE"
    exit 0
fi
trap 'rmdir "$LOCK_DIR"' EXIT

echo "$(date -u '+%Y-%m-%dT%H:%M:%SZ') starting $COMMAND" >> "$LOG_FILE"
STATUS=0
uv run eve "$COMMAND" >> "$LOG_FILE" 2>&1 || STATUS=$?
# Order ingests are followed by the derived data (book features + snapshot diffs) so market
# making never ranks stale books; run it even if one region's ingest failed.
if [[ "$COMMAND" == "ingest-all" ]]; then
    echo "$(date -u '+%Y-%m-%dT%H:%M:%SZ') starting derive" >> "$LOG_FILE"
    uv run eve derive >> "$LOG_FILE" 2>&1 || STATUS=$?
fi
exit "$STATUS"
