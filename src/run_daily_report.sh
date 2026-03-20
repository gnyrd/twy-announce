#!/bin/bash
# Wrapper script to sync HeyMarvelous data and run daily status report

set -e

cd "$(dirname "$0")/.."

echo "========================================="
echo "Daily Status Report Runner"
echo "Time: $(date)"
echo "========================================="

# Step 1: Sync HeyMarvelous data to SQLite
echo ""
echo "Step 1: Syncing HeyMarvelous data..."
cd /root/twy/marvy
.venv/bin/python3 scripts/sync.py
if [ $? -ne 0 ]; then
    echo "ERROR: Failed to sync HeyMarvelous data"
    exit 1
fi
cd -

# Step 2: Run daily status report
echo ""
echo "Step 2: Running daily status report..."
python3 src/daily_status_report.py
if [ $? -ne 0 ]; then
    echo "ERROR: Failed to run daily status report"
    exit 1
fi

echo ""
echo "========================================="
echo "Daily Status Report Complete"
echo "=========================================" 
