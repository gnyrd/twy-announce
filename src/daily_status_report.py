#!/usr/bin/env python3
"""Post daily status report to Slack with Marvelous subscription data."""

import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple

import sqlite3
import requests
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Configuration
PROJECT_ROOT = Path(__file__).parent.parent
MAILCHIMP_HISTORY_DIR = PROJECT_ROOT / "data/mailchimp/history"
INSTAGRAM_HISTORY_DIR = PROJECT_ROOT / "data/instagram/history"
YOUTUBE_HISTORY_DIR = PROJECT_ROOT / "data/youtube/history"
MARVY_DB = Path("/root/twy/marvy/marvy.db")


def get_marvelous_data() -> List[Dict[str, Any]]:
    """Read active subscription data from local SQLite database."""
    conn = sqlite3.connect(str(MARVY_DB))
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT pr.product_name,
               CASE
                   WHEN last_pay.amount_paid > pr.price * 3 THEN 'Annual'
                   ELSE 'Monthly'
               END as billing_cycle,
               COUNT(*) as active_count,
               COALESCE(SUM(last_pay.amount_paid), 0) as revenue_per_cycle
        FROM subscriptions s
        JOIN products pr ON pr.id = s.product_id
        JOIN (
            SELECT customer_id, product_id, amount_paid,
                   ROW_NUMBER() OVER (PARTITION BY customer_id, product_id ORDER BY created DESC) as rn
            FROM purchases WHERE amount_paid > 0
        ) last_pay ON last_pay.customer_id = s.customer_id
                   AND last_pay.product_id = s.product_id
                   AND last_pay.rn = 1
        WHERE s.subscription_active = 1
          AND pr.product_name != 'The Yoga Lifestyle: On-demand Library'
        GROUP BY pr.product_name, billing_cycle
        ORDER BY pr.product_name, billing_cycle
    """).fetchall()
    conn.close()
    return [
        {
            "Product Name": r["product_name"],
            "Billing Cycle": r["billing_cycle"],
            "# of Active Subscriptions": r["active_count"],
            "Revenue per Cycle": r["revenue_per_cycle"],
        }
        for r in rows
    ]


def get_member_count_ago(days: int) -> int:
    """Approximate active member count N days ago from purchase history."""
    cutoff = (datetime.now() - timedelta(days=days)).isoformat()
    conn = sqlite3.connect(str(MARVY_DB))
    result = conn.execute("""
        SELECT COUNT(*) FROM subscriptions s
        JOIN products pr ON pr.id = s.product_id
        JOIN (SELECT DISTINCT customer_id, product_id FROM purchases
              WHERE amount_paid > 0) paid
          ON paid.customer_id = s.customer_id AND paid.product_id = s.product_id
        WHERE pr.product_name != 'The Yoga Lifestyle: On-demand Library'
          AND s.first_purchase < ?
          AND (s.subscription_active = 1 OR s.last_time_purchase > ?)
    """, (cutoff, cutoff)).fetchone()[0]
    conn.close()
    return result


def get_product_counts_ago(days: int) -> Dict[str, Dict[str, int]]:
    """Get per-product, per-billing-cycle active subscription counts N days ago from DB."""
    cutoff = (datetime.now() - timedelta(days=days)).isoformat()
    conn = sqlite3.connect(str(MARVY_DB))
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT pr.product_name,
               CASE
                   WHEN last_pay.amount_paid > pr.price * 3 THEN 'Annual'
                   ELSE 'Monthly'
               END as billing_cycle,
               COUNT(*) as active_count
        FROM subscriptions s
        JOIN products pr ON pr.id = s.product_id
        JOIN (
            SELECT customer_id, product_id, amount_paid,
                   ROW_NUMBER() OVER (PARTITION BY customer_id, product_id ORDER BY created DESC) as rn
            FROM purchases WHERE amount_paid > 0
        ) last_pay ON last_pay.customer_id = s.customer_id
                   AND last_pay.product_id = s.product_id
                   AND last_pay.rn = 1
        WHERE pr.product_name != 'The Yoga Lifestyle: On-demand Library'
          AND s.first_purchase < ?
          AND (s.subscription_active = 1 OR s.last_time_purchase > ?)
        GROUP BY pr.product_name, billing_cycle
    """, (cutoff, cutoff)).fetchall()
    conn.close()

    counts = {}
    for row in rows:
        product = row["product_name"]
        if product not in counts:
            counts[product] = {"Monthly": 0, "Annual": 0}
        counts[product][row["billing_cycle"]] = row["active_count"]
    return counts


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


def extract_subscriber_counts(
    mailchimp: Optional[Dict[str, Any]],
    instagram: Optional[Dict[str, Any]],
    youtube: Optional[Dict[str, Any]]
) -> Dict[str, int]:
    """Extract email/social subscriber counts into a flat dict for comparison."""
    counts = {}
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
    current_totals = calculate_totals(subscriptions)

    now = datetime.now()
    week_ago_date = (now - timedelta(days=7)).strftime("%Y-%m-%d")
    month_ago_date = (now - timedelta(days=30)).strftime("%Y-%m-%d")
    year_ago_date = (now - timedelta(days=365)).strftime("%Y-%m-%d")

    # Load Mailchimp data
    mc_today_snapshot = load_mailchimp_snapshot(today)
    mc_week_snapshot = load_mailchimp_snapshot(week_ago_date)
    mc_month_snapshot = load_mailchimp_snapshot(month_ago_date)
    mc_year_snapshot = load_mailchimp_snapshot(year_ago_date)

    # Load Instagram data
    ig_today_snapshot = load_instagram_snapshot(today)

    # Load YouTube data
    yt_today_snapshot = load_youtube_snapshot(today)
    yt_week_snapshot = load_youtube_snapshot(week_ago_date)
    yt_month_snapshot = load_youtube_snapshot(month_ago_date)
    yt_year_snapshot = load_youtube_snapshot(year_ago_date)

    # HM historical product counts from DB
    day_counts = get_product_counts_ago(1)
    week_counts = get_product_counts_ago(7)
    month_counts = get_product_counts_ago(30)
    year_counts = get_product_counts_ago(365)

    today_formatted = now.strftime("%a, %b %d")

    lines = [
        f"*TWY Status* {today_formatted}",
        "",
    ]

    # Subscribers section
    if mc_today_snapshot or ig_today_snapshot or yt_today_snapshot:
        lines.append("*Subscribers:*")

        if mc_today_snapshot:
            subscriber_count = mc_today_snapshot["subscriber_count"]
            change_key = "mailchimp:subscriber_count"
            if change_key in changes:
                lines.append(f" Email: {subscriber_count:,} {format_change_highlighted(changes[change_key])}")
            else:
                lines.append(f" Email: {subscriber_count:,}")
            week_val = mc_week_snapshot["subscriber_count"] if mc_week_snapshot else None
            month_val = mc_month_snapshot["subscriber_count"] if mc_month_snapshot else None
            year_val = mc_year_snapshot["subscriber_count"] if mc_year_snapshot else None
            lines.extend(format_subscriber_deltas(subscriber_count, week_val, month_val, year_val))

        if ig_today_snapshot:
            ig_week_snapshot = load_instagram_snapshot(week_ago_date)
            ig_month_snapshot = load_instagram_snapshot(month_ago_date)
            ig_year_snapshot = load_instagram_snapshot(year_ago_date)
            follower_count = ig_today_snapshot["follower_count"]
            change_key = "instagram:follower_count"
            if change_key in changes:
                lines.append(f" Instagram: {follower_count:,} {format_change_highlighted(changes[change_key])}")
            else:
                lines.append(f" Instagram: {follower_count:,}")
            week_val = ig_week_snapshot["follower_count"] if ig_week_snapshot else None
            month_val = ig_month_snapshot["follower_count"] if ig_month_snapshot else None
            year_val = ig_year_snapshot["follower_count"] if ig_year_snapshot else None
#             lines.extend(format_subscriber_deltas(follower_count, week_val, month_val, year_val))

        if yt_today_snapshot:
            subscriber_count = yt_today_snapshot["subscriber_count"]
            change_key = "youtube:subscriber_count"
            if change_key in changes:
                lines.append(f" YouTube: {subscriber_count:,} {format_change_highlighted(changes[change_key])}")
            else:
                lines.append(f" YouTube: {subscriber_count:,}")
            week_val = yt_week_snapshot["subscriber_count"] if yt_week_snapshot else None
            month_val = yt_month_snapshot["subscriber_count"] if yt_month_snapshot else None
            year_val = yt_year_snapshot["subscriber_count"] if yt_year_snapshot else None
            lines.extend(format_subscriber_deltas(subscriber_count, week_val, month_val, year_val))

        lines.append("")

    # Membership section
    lines.append("*Membership:*")
    lines.append(f" Active: {current_totals['total_subs']:.0f}")


    lines.append("")

    # Group current subscriptions by product and billing cycle
    products = {}
    for row in subscriptions:
        product = row["Product Name"]
        if product not in products:
            products[product] = {"Monthly": 0, "Annual": 0}
        cycle = row["Billing Cycle"]
        if cycle == "Monthly":
            products[product]["Monthly"] = row["# of Active Subscriptions"]
        else:
            products[product]["Annual"] += row["# of Active Subscriptions"]

    for product in sorted(products.keys()):
        monthly = products[product]["Monthly"]
        annual = products[product]["Annual"]
        display_name = simplify_product_name(product)

        # Day-over-day deltas for highlighted arrows
        day_monthly = day_counts.get(product, {}).get("Monthly", 0)
        day_annual = day_counts.get(product, {}).get("Annual", 0)
        monthly_delta = monthly - day_monthly
        annual_delta = annual - day_annual

        # Determine max width for delta alignment
        all_monthly_deltas = [monthly_delta]
        all_annual_deltas = [annual_delta]
        for hist in [week_counts, month_counts, year_counts]:
            if product in hist:
                all_monthly_deltas.append(monthly - hist[product]["Monthly"])
                all_annual_deltas.append(annual - hist[product]["Annual"])

        def delta_width(val):
            return len(f"+{abs(val)}") if val >= 0 else len(str(val))

        max_monthly_width = max(delta_width(v) for v in all_monthly_deltas)
        max_annual_width = max(delta_width(v) for v in all_annual_deltas)

        annual_str = str(annual)
        if annual_delta != 0:
            annual_str = f"{annual} {format_change_highlighted(annual_delta)}"

        monthly_str = str(monthly)
        if monthly_delta != 0:
            monthly_str = f"{monthly} {format_change_highlighted(monthly_delta)}"

        lines.append(f" {display_name}: ")
        lines.append(f"   Annual: {annual_str}")

        for label, hist in [("week", week_counts), ("month", month_counts), ("year", year_counts)]:
            if product in hist:
                a_diff = annual - hist[product]["Annual"]
                if a_diff != 0:
                    a_str = f"+{a_diff}" if a_diff >= 0 else str(a_diff)
                    lines.append(f"   𝚫 {label}:  {a_str:>{max_annual_width}}")

        lines.append(f"   Monthly: {monthly_str}")

        for label, hist in [("week", week_counts), ("month", month_counts), ("year", year_counts)]:
            if product in hist:
                m_diff = monthly - hist[product]["Monthly"]
                if m_diff != 0:
                    m_str = f"+{m_diff}" if m_diff >= 0 else str(m_diff)
                    lines.append(f"   𝚫 {label}:  {m_str:>{max_monthly_width}}")

        lines.append("")

    return "\n".join(lines)


def post_to_slack(message: str):
    """Post message to Slack."""
    webhook_url = os.getenv("SLACK_WEBHOOK_URL")
    bot_token = os.getenv("SLACK_BOT_TOKEN")
    channel = os.getenv("SLACK_CHANNEL", "#twy-status")

    if webhook_url:
        print("Posting to Slack via webhook...")
        resp = requests.post(
            webhook_url,
            json={"text": message},
            headers={"Content-Type": "application/json"},
        )
        resp.raise_for_status()
        print("✓ Posted to Slack")

    elif bot_token:
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
        subscriptions = get_marvelous_data()

        # Load today's subscriber snapshots (email/social only)
        mc_today = load_mailchimp_snapshot(today)
        ig_today = load_instagram_snapshot(today)
        yt_today = load_youtube_snapshot(today)

        # Load yesterday's subscriber snapshots for comparison
        mc_yesterday = load_mailchimp_snapshot(yesterday)
        ig_yesterday = load_instagram_snapshot(yesterday)
        yt_yesterday = load_youtube_snapshot(yesterday)

        today_counts = extract_subscriber_counts(mc_today, ig_today, yt_today)
        yesterday_counts = extract_subscriber_counts(mc_yesterday, ig_yesterday, yt_yesterday)
        changes = compare_counts(today_counts, yesterday_counts)

        # Check HM membership change (DB query, no snapshot needed)
        current_total = int(calculate_totals(subscriptions)["total_subs"])
        hm_yesterday_total = get_member_count_ago(1)
        hm_changed = current_total != hm_yesterday_total

        should_send = False
        send_reason = ""

        if is_monday():
            should_send = True
            send_reason = "Monday (weekly report)"
        elif changes:
            should_send = True
            send_reason = f"Subscriber data changed: {len(changes)} metric(s)"
        elif hm_changed:
            should_send = True
            send_reason = f"HM membership changed: {hm_yesterday_total} -> {current_total}"
        else:
            send_reason = "No changes from yesterday"

        print(f"\nSend decision: {'YES' if should_send else 'NO'} - {send_reason}")

        if not should_send:
            print("\n✓ Skipping report (no changes)")
            return 0

        message = format_report(subscriptions, today, changes)
        print("\nReport preview:")
        print("-" * 60)
        print(message)
        print("-" * 60)

        post_to_slack(message)

        print("\n✓ Daily status report completed successfully")
        return 0

    except Exception as e:
        print(f"\n✗ Error: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
