#!/usr/bin/env python3
"""Post daily status report to Slack with Marvelous subscription data."""

import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any

import requests
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Configuration
JWT_CACHE_FILE = Path("/root/twy-announce/.jwt_cache.json")
METABASE_URL = "https://reports.heymarv.com/api/embed/card/{jwt_token}/query/json"


def load_cached_jwt() -> str:
    """Load JWT token from cache file."""
    try:
        with open(JWT_CACHE_FILE) as f:
            cache_data = json.load(f)
            return cache_data["jwt_token"]
    except Exception as e:
        raise Exception(f"Failed to load cached JWT: {e}")


def get_marvelous_data() -> List[Dict[str, Any]]:
    """Fetch active subscription data from Marvelous using cached JWT."""
    jwt_token = load_cached_jwt()
    
    url = METABASE_URL.format(jwt_token=jwt_token)
    
    print(f"Fetching Marvelous subscription data...")
    try:
        resp = requests.get(url, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        
        if not data:
            raise Exception("No data returned from Marvelous report")
        
        print(f"✓ Fetched {len(data)} subscription records")
        return data
        
    except requests.RequestException as e:
        raise Exception(f"Failed to fetch Marvelous report data: {e}")


def format_report(subscriptions: List[Dict[str, Any]]) -> str:
    """Format subscription data into Slack message."""
    # Calculate totals
    total_subs = sum(row["# of Active Subscriptions"] for row in subscriptions)
    total_revenue = sum(row["Revenue per Cycle"] for row in subscriptions)
    
    # Get today's date
    today = datetime.now().strftime("%A, %b %d, %Y")
    
    # Group by product
    products = {}
    for row in subscriptions:
        product = row["Product Name"]
        if product not in products:
            products[product] = []
        products[product].append(row)
    
    # Build message
    lines = [
        "📊 *TWY Daily Status Report*",
        f"_{today}_",
        "",
        f"💰 *Active Subscriptions: {total_subs}*",
        f"Total Revenue: ${total_revenue:,.0f}/cycle",
        "",
        "*By Product:*",
    ]
    
    # Sort products alphabetically
    for product in sorted(products.keys()):
        # Sort billing cycles: Monthly first, then Other
        cycles = products[product]
        cycles.sort(key=lambda x: (x["Billing Cycle"] != "Monthly", x["Billing Cycle"]))
        
        for cycle_data in cycles:
            billing_cycle = cycle_data["Billing Cycle"]
            subs = cycle_data["# of Active Subscriptions"]
            revenue = cycle_data["Revenue per Cycle"]
            lines.append(f"✅ {product} ({billing_cycle}): {subs} subs - ${revenue:,.0f}")
    
    return "\n".join(lines)


def post_to_slack(message: str):
    """Post message to Slack."""
    webhook_url = os.getenv("SLACK_WEBHOOK_URL")
    bot_token = os.getenv("SLACK_BOT_TOKEN")
    channel = os.getenv("SLACK_CHANNEL", "#twy-status")
    
    if webhook_url:
        # Use webhook
        print("Posting to Slack via webhook...")
        resp = requests.post(
            webhook_url,
            json={"text": message},
            headers={"Content-Type": "application/json"},
        )
        resp.raise_for_status()
        print("✓ Posted to Slack")
        
    elif bot_token:
        # Use Slack API
        print("Posting to Slack via bot token...")
        resp = requests.post(
            "https://slack.com/api/chat.postMessage",
            json={"channel": channel, "text": message},
            headers={"Authorization": f"Bearer {bot_token}"},
        )
        resp.raise_for_status()
        result = resp.json()
        if not result.get("ok"):
            raise Exception(f"Slack API error: {result.get('error')}")
        print("✓ Posted to Slack")
        
    else:
        raise ValueError("No Slack credentials found. Set SLACK_WEBHOOK_URL or SLACK_BOT_TOKEN in .env")


def main():
    """Main entry point."""
    print("=" * 60)
    print("Daily Status Report")
    print("=" * 60)
    
    try:
        # Get data
        subscriptions = get_marvelous_data()
        
        # Format message
        message = format_report(subscriptions)
        print("\nReport preview:")
        print("-" * 60)
        print(message)
        print("-" * 60)
        
        # Post to Slack
        post_to_slack(message)
        
        print("\n✓ Daily status report completed successfully")
        return 0
        
    except Exception as e:
        print(f"\n✗ Error: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
