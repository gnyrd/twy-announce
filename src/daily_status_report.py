#!/usr/bin/env python3
"""Post daily status report to Slack with Marvelous subscription data."""

import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Dict, Any, Optional

import requests
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Configuration
JWT_CACHE_FILE = Path("/root/twy-announce/.jwt_cache.json")
HISTORY_DIR = Path("/root/twy-announce/data/marvelous/history")
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


def save_daily_snapshot(subscriptions: List[Dict[str, Any]], date: str):
    """Save daily subscription data to history."""
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    
    snapshot = {
        "date": date,
        "timestamp": datetime.now().isoformat(),
        "subscriptions": subscriptions
    }
    
    filepath = HISTORY_DIR / f"{date}.json"
    with open(filepath, "w") as f:
        json.dump(snapshot, f, indent=2)
    
    print(f"✓ Saved snapshot to {filepath}")


def load_historical_snapshot(date: str) -> Optional[Dict[str, Any]]:
    """Load historical snapshot for a specific date."""
    filepath = HISTORY_DIR / f"{date}.json"
    if not filepath.exists():
        return None
    
    try:
        with open(filepath) as f:
            return json.load(f)
    except Exception as e:
        print(f"Warning: Could not load snapshot for {date}: {e}")
        return None


def calculate_totals(subscriptions: List[Dict[str, Any]]) -> Dict[str, float]:
    """Calculate total subscriptions and revenue."""
    return {
        "total_subs": sum(row["# of Active Subscriptions"] for row in subscriptions),
        "total_revenue": sum(row["Revenue per Cycle"] for row in subscriptions)
    }


def simplify_product_name(product: str) -> str:
    """Simplify product names for display."""
    if product == "The Archive":
        return "TWY Archive"
    return product


def format_change(current: float, previous: float) -> str:
    """Format change with sign."""
    diff = current - previous
    if diff > 0:
        return f"+{diff:.0f}"
    elif diff < 0:
        return f"{diff:.0f}"
    else:
        return "0"


def format_report(subscriptions: List[Dict[str, Any]], today: str) -> str:
    """Format subscription data into Slack message with historical comparisons."""
    # Current totals
    current_totals = calculate_totals(subscriptions)
    
    # Load historical data
    now = datetime.now()
    week_ago_date = (now - timedelta(days=7)).strftime("%Y-%m-%d")
    month_ago_date = (now - timedelta(days=30)).strftime("%Y-%m-%d")
    year_ago_date = (now - timedelta(days=365)).strftime("%Y-%m-%d")
    
    week_snapshot = load_historical_snapshot(week_ago_date)
    month_snapshot = load_historical_snapshot(month_ago_date)
    year_snapshot = load_historical_snapshot(year_ago_date)
    
    # Format date
    today_formatted = now.strftime("%A, %b %d, %Y")
    
    # Build message
    lines = [
        "*TWY Daily Status Report*",
        today_formatted,
        "",
        "*Membership:*",
        f" Active Students: {current_totals['total_subs']:.0f}",
    ]
    
    # Add historical comparisons if data exists
    if week_snapshot or month_snapshot or year_snapshot:
        lines.append("")
        
        if week_snapshot:
            week_totals = calculate_totals(week_snapshot["subscriptions"])
            change = format_change(current_totals['total_subs'], week_totals['total_subs'])
            lines.append(f"  Week over week: {change}")
        
        if month_snapshot:
            month_totals = calculate_totals(month_snapshot["subscriptions"])
            change = format_change(current_totals['total_subs'], month_totals['total_subs'])
            lines.append(f"  Month over month: {change}")
        
        if year_snapshot:
            year_totals = calculate_totals(year_snapshot["subscriptions"])
            change = format_change(current_totals['total_subs'], year_totals['total_subs'])
            lines.append(f"  Year over year: {change}")
    
    # Product breakdown
    lines.append("")
    
    # Group by product
    products = {}
    for row in subscriptions:
        product = row["Product Name"]
        if product not in products:
            products[product] = []
        products[product].append(row)
    
    # Sort products alphabetically
    for product in sorted(products.keys()):
        # Sort billing cycles: Monthly first, then Other
        cycles = products[product]
        cycles.sort(key=lambda x: (x["Billing Cycle"] != "Monthly", x["Billing Cycle"]))
        
        for cycle_data in cycles:
            billing_cycle = cycle_data["Billing Cycle"]
            subs = cycle_data["# of Active Subscriptions"]
            student_word = "student" if subs == 1 else "students"
            display_name = simplify_product_name(product)
            lines.append(f" {display_name} ({billing_cycle}): {subs} {student_word}")
    
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
    
    today = datetime.now().strftime("%Y-%m-%d")
    
    try:
        # Get data
        subscriptions = get_marvelous_data()
        
        # Save snapshot
        save_daily_snapshot(subscriptions, today)
        
        # Format message
        message = format_report(subscriptions, today)
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
