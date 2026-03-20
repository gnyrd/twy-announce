#!/bin/bash
# Run daily status report (data sync is handled separately by cron)

set -e

cd "$(dirname "$0")/.."

echo "========================================="
echo "Daily Status Report Runner"
echo "Time: $(date)"
echo "========================================="

python3 src/daily_status_report.py
if [ $? -ne 0 ]; then
    echo "ERROR: Failed to run daily status report"
    exit 1
fi

echo ""
echo "========================================="
echo "Daily Status Report Complete"
echo "=========================================" 
