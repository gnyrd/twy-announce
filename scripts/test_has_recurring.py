#!/usr/bin/env python3
"""
Test: Use has_recurring field instead of 45-day lookback to identify active subscriptions.
Compare results against Active Subscriptions Report for validation.
"""
import csv
import sys
from pathlib import Path

# Paths
CUSTOMERS_CSV = '/root/twy/announce/data/customers/customers_20260319.csv'
ACTIVE_SUBS_REPORT = '/root/twy/announce/docs/active_subscriptions_2026-03-19T00_19_47.110198Z.csv'

# Product mappings (from sync script)
YOGA_LIFESTYLE_PRODUCTS = ["The Yoga Lifestyle Membership", "Yoga Lifestyle"]
ARCHIVE_PRODUCTS = ["The Archive", "TWY Archive"]

print("=" * 70)
print("TEST: Using has_recurring field to identify active subscriptions")
print("=" * 70)
print()

# Step 1: Read validation data (Active Subscriptions Report)
print("STEP 1: Load validation data from Active Subscriptions Report")
print("-" * 70)
with open(ACTIVE_SUBS_REPORT, 'r') as f:
    reader = csv.DictReader(f)
    expected_active = {}
    for row in reader:
        email = row['Email'].lower().strip()
        product = row['Product Name']
        expected_active[email] = product

print(f"Expected active subscriptions: {len(expected_active)}")
yoga_lifestyle_expected = [e for e, p in expected_active.items() if 'Yoga Lifestyle' in p]
archive_expected = [e for e, p in expected_active.items() if 'Archive' in p]
print(f"  - Yoga Lifestyle: {len(yoga_lifestyle_expected)}")
print(f"  - Archive: {len(archive_expected)}")
print()

# Step 2: Use has_recurring field from customer data
print("STEP 2: Identify active subscriptions using has_recurring field")
print("-" * 70)
with open(CUSTOMERS_CSV, 'r') as f:
    reader = csv.DictReader(f)
    found_active = {}
    
    for row in reader:
        email = row['email'].lower().strip()
        has_recurring = row['has_recurring']
        
        # Skip if no active subscription
        if has_recurring != 'True':
            continue
        
        # Customer has active subscription - determine which product
        # We need to check purchase history or product name
        # For now, let's see what we can determine from the CSV
        found_active[email] = {
            'has_recurring': has_recurring,
            'last_purchase': row['last_time_purchase'],
            'total_spend': row['total_spend']
        }

print(f"Found customers with has_recurring=True: {len(found_active)}")
print()

# Step 3: Compare results
print("STEP 3: Compare against validation data")
print("-" * 70)

expected_emails = set(expected_active.keys())
found_emails = set(found_active.keys())

# Check if counts match
print(f"Expected: {len(expected_emails)} active subscriptions")
print(f"Found:    {len(found_emails)} customers with has_recurring=True")
print()

# Find differences
missing = expected_emails - found_emails
extra = found_emails - expected_emails

if missing:
    print(f"❌ MISSING {len(missing)} customers that should be active:")
    for email in sorted(missing):
        print(f"   - {email} (should have: {expected_active[email]})")
    print()

if extra:
    print(f"⚠️  EXTRA {len(extra)} customers marked has_recurring but not in report:")
    for email in sorted(extra):
        print(f"   - {email}")
    print()

if not missing and not extra:
    print("✅ PERFECT MATCH! All expected active subscriptions found.")
    print()

# Step 4: Summary
print("=" * 70)
print("SUMMARY")
print("=" * 70)
if len(found_emails) == len(expected_emails) and not missing and not extra:
    print("✅ TEST PASSED: has_recurring field accurately identifies active subscriptions")
else:
    print(f"⚠️  TEST RESULTS:")
    print(f"   - Match rate: {len(expected_emails & found_emails)}/{len(expected_emails)} ({100*len(expected_emails & found_emails)//len(expected_emails)}%)")
    if missing:
        print(f"   - Missing: {len(missing)} customers")
    if extra:
        print(f"   - Extra: {len(extra)} customers")

