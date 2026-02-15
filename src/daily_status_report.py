#!/usr/bin/env python3
"""Post daily status report to Slack with Marvelous subscription data."""

import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple

import requests
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Configuration
JWT_CACHE_FILE = Path("/root/twy-announce/.jwt_cache.json")
HISTORY_DIR = Path("/root/twy-announce/data/marvelous/history")
MAILCHIMP_HISTORY_DIR = Path("/root/twy-announce/data/mailchimp/history")
INSTAGRAM_HISTORY_DIR = Path("/root/twy-announce/data/instagram/history")
YOUTUBE_HISTORY_DIR = Path("/root/twy-announce/data/youtube/history")
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


def load_mailchimp_snapshot(date: str) -> Optional[Dict[str, Any]]:
    """Load Mailchimp snapshot for a specific date."""
    filepath = MAILCHIMP_HISTORY_DIR / f"{date}.json"
    if not filepath.exists():
        return None
    
    try:
        with open(filepath) as f:
            return json.load(f)
    except Exception as e:
        print(f"Warning: Could not load Mailchimp snapshot for {date}: {e}")
        return None


def load_instagram_snapshot(date: str) -> Optional[Dict[str, Any]]:
    """Load Instagram snapshot for a specific date."""
    filepath = INSTAGRAM_HISTORY_DIR / f"{date}.json"
    if not filepath.exists():
        return None
    
    try:
        with open(filepath) as f:
            return json.load(f)
    except Exception as e:
        print(f"Warning: Could not load Instagram snapshot for {date}: {e}")
        return None


def load_youtube_snapshot(date: str) -> Optional[Dict[str, Any]]:
    """Load YouTube snapshot for a specific date."""
    filepath = YOUTUBE_HISTORY_DIR / f"{date}.json"
    if not filepath.exists():
        return None
    
    try:
        with open(filepath) as f:
            return json.load(f)
    except Exception as e:
        print(f"Warning: Could not load YouTube snapshot for {date}: {e}")
        return None


def extract_all_counts(
    subscriptions: List[Dict[str, Any]],
    mailchimp: Optional[Dict[str, Any]],
    instagram: Optional[Dict[str, Any]],
    youtube: Optional[Dict[str, Any]]
) -> Dict[str, int]:
    """Extract all counts into a flat dict for comparison."""
    counts = {}
    
    # Marvelous subscription counts
    for row in subscriptions:
        product = row["Product Name"]
        cycle = row["Billing Cycle"]
        key = f"marvelous:{product}:{cycle}"
        counts[key] = row["# of Active Subscriptions"]
    
    # Subscriber counts
    if mailchimp:
        counts["mailchimp:subscriber_count"] = mailchimp.get("subscriber_count", 0)
    if instagram:
        counts["instagram:follower_count"] = instagram.get("follower_count", 0)
    if youtube:
        counts["youtube:subscriber_count"] = youtube.get("subscriber_count", 0)
    
    return counts


def compare_counts(today: Dict[str, int], yesterday: Dict[str, int]) -> Dict[str, int]:
    """Compare counts and return dict of changes (key -> delta)."""
    changes = {}
    all_keys = set(today.keys()) | set(yesterday.keys())
    
    for key in all_keys:
        today_val = today.get(key, 0)
        yesterday_val = yesterday.get(key, 0)
        if today_val != yesterday_val:
            changes[key] = today_val - yesterday_val
    
    return changes


def is_monday() -> bool:
    """Check if today is Monday."""
    return datetime.now().weekday() == 0


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


def format_change_highlighted(delta: int) -> str:
    """Format a change with bold and arrow for highlighting."""
    if delta > 0:
        return f"*+{delta} ↑*"
    elif delta < 0:
        return f"*{delta} ↓*"
    else:
        return "0"


def get_product_counts(snapshot: Optional[Dict[str, Any]]) -> Dict[str, Dict[str, int]]:
    """Extract product counts from a snapshot."""
    if not snapshot:
        return {}
    counts = {}
    for row in snapshot["subscriptions"]:
        product = row["Product Name"]
        if product not in counts:
            counts[product] = {"Monthly": 0, "Other": 0}
        billing_cycle = row["Billing Cycle"]
        if billing_cycle == "Monthly":
            counts[product]["Monthly"] = row["# of Active Subscriptions"]
        else:
            counts[product]["Other"] = row["# of Active Subscriptions"]
    return counts


def format_subscriber_deltas(current: int, week_val: Optional[int], month_val: Optional[int], year_val: Optional[int]) -> List[str]:
    """Format delta lines for a subscriber metric. Only include lines where there's a change."""
    lines = []
    
    if week_val is not None:
        diff = current - week_val
        if diff != 0:
            change = f"+{diff}" if diff > 0 else str(diff)
            lines.append(f"   𝚫 week:  {change}")
    
    if month_val is not None:
        diff = current - month_val
        if diff != 0:
            change = f"+{diff}" if diff > 0 else str(diff)
            lines.append(f"   𝚫 month: {change}")
    
    if year_val is not None:
        diff = current - year_val
        if diff != 0:
            change = f"+{diff}" if diff > 0 else str(diff)
            lines.append(f"   𝚫 year:  {change}")
    
    return lines


def format_report(subscriptions: List[Dict[str, Any]], today: str, changes: Dict[str, int]) -> str:
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
    
    # Load Mailchimp data
    mc_today_snapshot = load_mailchimp_snapshot(today)
    mc_week_snapshot = load_mailchimp_snapshot(week_ago_date)
    mc_month_snapshot = load_mailchimp_snapshot(month_ago_date)
    mc_year_snapshot = load_mailchimp_snapshot(year_ago_date)
    
    # Load Instagram data
    ig_today_snapshot = load_instagram_snapshot(today)
    ig_week_snapshot = load_instagram_snapshot(week_ago_date)
    ig_month_snapshot = load_instagram_snapshot(month_ago_date)
    ig_year_snapshot = load_instagram_snapshot(year_ago_date)
    
    # Load YouTube data
    yt_today_snapshot = load_youtube_snapshot(today)
    yt_week_snapshot = load_youtube_snapshot(week_ago_date)
    yt_month_snapshot = load_youtube_snapshot(month_ago_date)
    yt_year_snapshot = load_youtube_snapshot(year_ago_date)
    
    # Format date
    today_formatted = now.strftime("%a, %b %d")
    
    # Build message
    lines = [
        (f"*TWY Status* {today_formatted}"),
        "",
    ]
    
    # Add Subscribers section if we have any subscriber data
    if mc_today_snapshot or ig_today_snapshot or yt_today_snapshot:
        lines.append("*Subscribers:*")
        
        # Email (Mailchimp)
        if mc_today_snapshot:
            subscriber_count = mc_today_snapshot["subscriber_count"]
            change_key = "mailchimp:subscriber_count"
            if change_key in changes:
                lines.append(f" Email: {subscriber_count:,} {format_change_highlighted(changes[change_key])}")
            else:
                lines.append(f" Email: {subscriber_count:,}")
            
            # Add deltas for email
            week_val = mc_week_snapshot["subscriber_count"] if mc_week_snapshot else None
            month_val = mc_month_snapshot["subscriber_count"] if mc_month_snapshot else None
            year_val = mc_year_snapshot["subscriber_count"] if mc_year_snapshot else None
            lines.extend(format_subscriber_deltas(subscriber_count, week_val, month_val, year_val))
        
        # Instagram
        if ig_today_snapshot:
            follower_count = ig_today_snapshot["follower_count"]
            change_key = "instagram:follower_count"
            if change_key in changes:
                lines.append(f" Instagram: {follower_count:,} {format_change_highlighted(changes[change_key])}")
            else:
                lines.append(f" Instagram: {follower_count:,}")
            
            # Add deltas for Instagram
            week_val = ig_week_snapshot["follower_count"] if ig_week_snapshot else None
            month_val = ig_month_snapshot["follower_count"] if ig_month_snapshot else None
            year_val = ig_year_snapshot["follower_count"] if ig_year_snapshot else None
            lines.extend(format_subscriber_deltas(follower_count, week_val, month_val, year_val))
        
        # YouTube
        if yt_today_snapshot:
            subscriber_count = yt_today_snapshot["subscriber_count"]
            change_key = "youtube:subscriber_count"
            if change_key in changes:
                lines.append(f" YouTube: {subscriber_count:,} {format_change_highlighted(changes[change_key])}")
            else:
                lines.append(f" YouTube: {subscriber_count:,}")
            
            # Add deltas for YouTube
            week_val = yt_week_snapshot["subscriber_count"] if yt_week_snapshot else None
            month_val = yt_month_snapshot["subscriber_count"] if yt_month_snapshot else None
            year_val = yt_year_snapshot["subscriber_count"] if yt_year_snapshot else None
            lines.extend(format_subscriber_deltas(subscriber_count, week_val, month_val, year_val))
        
        lines.append("")
    
    lines.append("*Membership:*")
    lines.append(f" Active: {current_totals['total_subs']:.0f}")
    
    # Add historical comparisons if data exists
    if week_snapshot or month_snapshot or year_snapshot:
        
        if week_snapshot:
            week_totals = calculate_totals(week_snapshot["subscriptions"])
            change = format_change(current_totals['total_subs'], week_totals['total_subs'])
            lines.append(f"  𝚫 week: {change}")
        
        if month_snapshot:
            month_totals = calculate_totals(month_snapshot["subscriptions"])
            change = format_change(current_totals['total_subs'], month_totals['total_subs'])
            lines.append(f"  𝚫 month: {change}")
        
        if year_snapshot:
            year_totals = calculate_totals(year_snapshot["subscriptions"])
            change = format_change(current_totals['total_subs'], year_totals['total_subs'])
            lines.append(f"  𝚫 year: {change}")
    
    # Product breakdown
    lines.append("")
    
    # Group by product and billing cycle
    products = {}
    for row in subscriptions:
        product = row["Product Name"]
        if product not in products:
            products[product] = {"Monthly": 0, "Other": 0}
        billing_cycle = row["Billing Cycle"]
        if billing_cycle == "Monthly":
            products[product]["Monthly"] = row["# of Active Subscriptions"]
        else:
            products[product]["Other"] = row["# of Active Subscriptions"]
    
    # Get historical product counts
    week_counts = get_product_counts(week_snapshot)
    month_counts = get_product_counts(month_snapshot)
    year_counts = get_product_counts(year_snapshot)
    
    # Sort products alphabetically
    for product in sorted(products.keys()):
        monthly = products[product]["Monthly"]
        annual = products[product]["Other"]
        display_name = simplify_product_name(product)
        
        # Check for changes in this product
        monthly_change_key = f"marvelous:{product}:Monthly"
        annual_change_key = f"marvelous:{product}:Other"
        monthly_changed = monthly_change_key in changes
        annual_changed = annual_change_key in changes
        
        # Collect all values for this product to determine alignment
        all_monthly = [monthly]
        all_annual = [annual]
        
        for hist_counts in [week_counts, month_counts, year_counts]:
            if product in hist_counts:
                all_monthly.append(monthly - hist_counts[product]["Monthly"])
                all_annual.append(annual - hist_counts[product]["Other"])
        
        # Determine max width needed (account for +/- signs on deltas)
        def calc_width(val, is_delta):
            if is_delta:
                return len(f"+{abs(val)}") if val >= 0 else len(str(val))
            return len(str(val))
        
        max_monthly_width = max(calc_width(v, i > 0) for i, v in enumerate(all_monthly))
        max_annual_width = max(calc_width(v, i > 0) for i, v in enumerate(all_annual))
        
        total_subs = monthly + annual
        # student_word = "student" if total_subs == 1 else "students"
        
        # Build product line with change highlights
        monthly_str = str(monthly)
        annual_str = str(annual)
        if monthly_changed:
            monthly_str = f"{monthly} {format_change_highlighted(changes[monthly_change_key])}"
        if annual_changed:
            annual_str = f"{annual} {format_change_highlighted(changes[annual_change_key])}"
        
        lines.append(f" {display_name} (Monthly / Annual): {monthly_str} / {annual_str}")
        
        # Add historical comparisons per product
        has_history = False
        for label, hist_counts in [("week", week_counts), ("month", month_counts), ("year", year_counts)]:
            if product in hist_counts:
                has_history = True
                m_diff = monthly - hist_counts[product]["Monthly"]
                a_diff = annual - hist_counts[product]["Other"]
                m_str = f"+{m_diff}" if m_diff >= 0 else str(m_diff)
                a_str = f"+{a_diff}" if a_diff >= 0 else str(a_diff)
                if m_diff != 0 or a_diff != 0:
                    lines.append(f"   𝚫 {label}:  {m_str:>{max_monthly_width}} / {a_str:>{max_annual_width}}")
        
        if has_history:
            lines.append("")  # Blank line after product block with history
    
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
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    
    try:
        # Get today's data
        subscriptions = get_marvelous_data()
        
        # Save snapshot (always)
        save_daily_snapshot(subscriptions, today)
        
        # Load today's subscriber data
        mc_today = load_mailchimp_snapshot(today)
        ig_today = load_instagram_snapshot(today)
        yt_today = load_youtube_snapshot(today)
        
        # Extract today's counts
        today_counts = extract_all_counts(subscriptions, mc_today, ig_today, yt_today)
        
        # Load yesterday's data for comparison
        yesterday_snapshot = load_historical_snapshot(yesterday)
        mc_yesterday = load_mailchimp_snapshot(yesterday)
        ig_yesterday = load_instagram_snapshot(yesterday)
        yt_yesterday = load_youtube_snapshot(yesterday)
        
        # Extract yesterday's counts
        yesterday_subs = yesterday_snapshot["subscriptions"] if yesterday_snapshot else []
        yesterday_counts = extract_all_counts(yesterday_subs, mc_yesterday, ig_yesterday, yt_yesterday)
        
        # Compare counts
        changes = compare_counts(today_counts, yesterday_counts)
        
        # Determine if we should send
        should_send = False
        send_reason = ""
        
        if is_monday():
            should_send = True
            send_reason = "Monday (weekly report)"
        elif changes:
            should_send = True
            send_reason = f"Data changed: {len(changes)} metric(s)"
        else:
            send_reason = "No changes from yesterday"
        
        print(f"\nSend decision: {'YES' if should_send else 'NO'} - {send_reason}")
        
        if not should_send:
            print("\n✓ Skipping report (no changes)")
            return 0
        
        # Format message
        message = format_report(subscriptions, today, changes)
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
