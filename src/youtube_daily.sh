#!/bin/bash
# Daily YouTube subscriber data fetch and sync
# Runs hourly via cron, skips if today's file already exists

# Get the directory where this script lives
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# Load .env if it exists
if [[ -f "$REPO_ROOT/.env" ]]; then
    export $(grep -v '^#' "$REPO_ROOT/.env" | xargs)
fi

DATE=$(date +%Y-%m-%d)
LOCAL_FILE="$REPO_ROOT/data/youtube/history/${DATE}.json"
REMOTE_DEST="${YOUTUBE_REMOTE_DEST:-}"

# Skip if today's file already exists
if [[ -f "$LOCAL_FILE" ]]; then
    exit 0
fi

# Run the Python script
if ! python3 "$SCRIPT_DIR/youtube_subscriber_data.py"; then
    echo "Failed to fetch YouTube data"
    exit 1
fi

# SCP the file to the server (if remote destination configured)
if [[ -n "$REMOTE_DEST" && -f "$LOCAL_FILE" ]]; then
    scp "$LOCAL_FILE" "$REMOTE_DEST"
fi
