#!/bin/bash
# Wrapper script for running MailChimp sync from cron
# Runs once daily: 1am

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ANNOUNCE_DIR="$(dirname "$SCRIPT_DIR")"
LOG_DIR="$ANNOUNCE_DIR/logs"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
LOG_FILE="$LOG_DIR/mailchimp_sync_$TIMESTAMP.log"

# Ensure log directory exists
mkdir -p "$LOG_DIR"

# Change to announce directory
cd "$ANNOUNCE_DIR" || exit 1

echo "========================================" | tee -a "$LOG_FILE"
echo "MailChimp Sync - $(date)" | tee -a "$LOG_FILE"
echo "========================================" | tee -a "$LOG_FILE"
echo "" | tee -a "$LOG_FILE"

# Run the sync
python3 src/sync_mailchimp.py 2>&1 | tee -a "$LOG_FILE"
EXIT_CODE=${PIPESTATUS[0]}

echo "" | tee -a "$LOG_FILE"
echo "========================================" | tee -a "$LOG_FILE"
echo "Exit code: $EXIT_CODE" | tee -a "$LOG_FILE"
echo "Log saved to: $LOG_FILE" | tee -a "$LOG_FILE"
echo "========================================" | tee -a "$LOG_FILE"

# Keep only last 30 days of logs
find "$LOG_DIR" -name "mailchimp_sync_*.log" -mtime +30 -delete

exit $EXIT_CODE
