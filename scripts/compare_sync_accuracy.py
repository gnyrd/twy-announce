#!/usr/bin/env python3
"""
Compare sync script results against Active Subscriptions Report
to validate accuracy.
"""
import csv
import sys

# Your validation data
ACTIVE_SUBS_REPORT = '/root/twy/announce/docs/active_subscriptions_2026-03-19T00_19_47.110198Z.csv'

# Read active subscriptions from report
with open(ACTIVE_SUBS_REPORT, 'r') as f:
    reader = csv.DictReader(f)
    active_subs = {}
    for row in reader:
        email = row['Email'].lower().strip()
        product = row['Product Name']
        active_subs[email] = product

print(f"=== VALIDATION REPORT ===")
print(f"Active subscriptions: {len(active_subs)}")
print()

# Group by product
from collections import Counter
products = Counter(active_subs.values())
for product, count in products.most_common():
    print(f"  {product}: {count}")
print()

# What the sync SHOULD produce
print("=== EXPECTED SYNC RESULTS ===")
expected_yoga_lifestyle = [e for e, p in active_subs.items() if 'Yoga Lifestyle' in p]
expected_archive = [e for e, p in active_subs.items() if 'Archive' in p]

print(f"Should tag as 'Status - Member' + 'Membership - Yoga Lifestyle': {len(expected_yoga_lifestyle)}")
print(f"Should tag as 'Status - Member' + 'Membership - TWY Archive': {len(expected_archive)}")
print()

print("Yoga Lifestyle members:")
for email in sorted(expected_yoga_lifestyle):
    print(f"  {email}")
print()

print("Archive members:")
for email in sorted(expected_archive):
    print(f"  {email}")

