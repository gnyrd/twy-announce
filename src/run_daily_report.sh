#!/bin/bash
# Wrapper script to refresh JWT and run daily status report

set -e

cd /root/twy-announce

echo "========================================="
echo "Daily Status Report Runner"
echo "Time: $(date)"
echo "========================================="

# Step 1: Refresh JWT token
echo ""
echo "Step 1: Refreshing JWT token..."
python3 src/refresh_jwt.py
if [ $? -ne 0 ]; then
    echo "ERROR: Failed to refresh JWT token"
    exit 1
fi

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
