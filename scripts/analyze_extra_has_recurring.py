#!/usr/bin/env python3
"""
Analyze the 56 "extra" customers who have has_recurring=True 
but aren't in the Active Subscriptions Report.
Check what they actually purchased.
"""
import csv
import json
from datetime import datetime
from collections import Counter

# Paths
CUSTOMERS_CSV = '/root/twy/announce/data/customers/customers_20260319.csv'
ACTIVE_SUBS_REPORT = '/root/twy/announce/docs/active_subscriptions_2026-03-19T00_19_47.110198Z.csv'
FULL_EXPORT = '/root/twy/announce/docs/customers-export-3.csv'

# Get expected active emails
with open(ACTIVE_SUBS_REPORT, 'r') as f:
    expected_emails = {row['Email'].lower().strip() for row in csv.DictReader(f)}

# Get has_recurring=True emails
with open(CUSTOMERS_CSV, 'r') as f:
    has_recurring_emails = {row['email'].lower().strip() 
                           for row in csv.DictReader(f) 
                           if row['has_recurring'] == 'True'}

# Find the "extra" 56
extra_emails = has_recurring_emails - expected_emails

# Exclude business owner
extra_emails.discard('tiffany@tiffanywoodyoga.com')

print(f"Analyzing {len(extra_emails)} 'extra' customers with has_recurring=True")
print("(excluding tiffany@tiffanywoodyoga.com - business owner)")
print("=" * 80)
print()

# Get their purchase info from the full export
with open(FULL_EXPORT, 'r') as f:
    reader = csv.DictReader(f)
    
    # Get product columns (everything after 'country')
    headers = reader.fieldnames
    basic_fields = ['firstName', 'lastName', 'email', 'phone', 'address', 
                   'zipcode', 'city', 'state', 'country', 'firstPurchase', 
                   'latestPurchase', 'totalSpent', 'signedWaiver']
    product_fields = [h for h in headers if h not in basic_fields]
    
    extra_purchases = []
    
    for row in reader:
        email = row['email'].lower().strip()
        if email not in extra_emails:
            continue
        
        # Get products they purchased (where value is not False/empty)
        purchased = []
        for product in product_fields:
            value = row.get(product, '')
            if value and value not in ('False', 'false', '0', ''):
                purchased.append(product)
        
        extra_purchases.append({
            'email': email,
            'first_purchase': row.get('firstPurchase', ''),
            'latest_purchase': row.get('latestPurchase', ''),
            'total_spent': row.get('totalSpent', ''),
            'products': purchased
        })

# Analyze patterns
print("PATTERN ANALYSIS")
print("-" * 80)

# Count product purchases
all_products = []
for p in extra_purchases:
    all_products.extend(p['products'])

product_counts = Counter(all_products)
print(f"\nMost common products purchased by the 'extra' {len(extra_emails)} customers:")
for product, count in product_counts.most_common(15):
    print(f"  {count:3d} - {product}")

print("\n\nSAMPLE CUSTOMERS (first 10):")
print("-" * 80)
for i, customer in enumerate(sorted(extra_purchases, key=lambda x: x['email'])[:10]):
    print(f"\n{i+1}. {customer['email']}")
    print(f"   Latest purchase: {customer['latest_purchase']}")
    print(f"   Total spent: ${customer['total_spent']}")
    print(f"   Products ({len(customer['products'])}): {', '.join(customer['products'][:5])}")
    if len(customer['products']) > 5:
        print(f"   ... and {len(customer['products']) - 5} more")

