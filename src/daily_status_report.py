#!/usr/bin/env python3
"""Post daily status report to Slack with Marvelous subscription data."""

import argparse
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
from twy_paths import load_env, marvy_db_path
load_env()
load_dotenv(override=False)

# Configuration
PROJECT_ROOT = Path(__file__).parent.parent
MAILCHIMP_HISTORY_DIR = PROJECT_ROOT / "data/mailchimp/history"
INSTAGRAM_HISTORY_DIR = PROJECT_ROOT / "data/instagram/history"
YOUTUBE_HISTORY_DIR = PROJECT_ROOT / "data/youtube/history"
MARVY_DB = marvy_db_path()


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
    """Total active recurring-subscription count N days ago.

    Delegates to historical_active_counts.active_count_at, which computes
    coverage per-purchase (monthly=31d, annual=366d window). Replaces the
    prior proxy-based query that silently excluded members who churned
    between the target date and now.
    """
    from historical_active_counts import active_count_at
    return active_count_at(datetime.now() - timedelta(days=days))


def get_product_counts_ago(days: int) -> Dict[str, Dict[str, int]]:
    """Per-product, per-billing-cycle active subscription counts N days ago.

    Delegates to historical_active_counts.active_at. Billing cycle for each
    count bucket is taken from the purchase that actually covered the target
    date (not from the customer's most recent payment ever, which was the
    prior implementation's bug).
    """
    from historical_active_counts import active_at
    return active_at(datetime.now() - timedelta(days=days))


def get_next_habit_event() -> Optional[Dict[str, Any]]:
    """Return the next upcoming Habit class.

    Matches both placeholder rows ('The Yoga Habit') and published titles
    ('Habit: <theme>'). Excludes cancelled events. Returns None when nothing
    upcoming is on the calendar.
    """
    now_utc = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    conn = sqlite3.connect(str(MARVY_DB))
    row = conn.execute(
        """
        SELECT event_start_datetime, number_of_registrations
        FROM events
        WHERE (event_name LIKE 'Habit:%' OR event_name = 'The Yoga Habit')
          AND is_cancelled = 0
          AND event_start_datetime >= :now
        ORDER BY event_start_datetime
        LIMIT 1
        """,
        {"now": now_utc},
    ).fetchone()
    conn.close()
    if not row:
        return None
    return {"start": row[0], "registrations": row[1]}


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
    if product == "The Yoga Lifestyle Membership":
        return "The Yoga Lifestyle"
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


def format_trend_arrow(delta: int) -> str:
    """Return a plain trend arrow for a day-over-day change. No numeral, no bold.

    Used as an inline glyph beside a current value to express direction only:
    up arrow when the value rose since yesterday, down arrow when it fell,
    empty string otherwise (caller omits the separator).
    """
    if delta > 0:
        return "↑"
    if delta < 0:
        return "↓"
    return ""


def format_subscriber_deltas(current: int, week_val: Optional[int], month_val: Optional[int], year_val: Optional[int]) -> str:
    """Format inline delta suffix for a subscriber metric."""
    segments: List[str] = []
    for label, val in (("week", week_val), ("month", month_val), ("year", year_val)):
        if val is None:
            continue
        diff = current - val
        if diff == 0:
            continue
        change = f"+{diff}" if diff > 0 else str(diff)
        segments.append(f"{label}: {change}")
    if not segments:
        return ""
    return "    |    𝚫  " + "  ".join(segments)


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

    lines: list = []

    # Followers section
    if mc_today_snapshot or ig_today_snapshot or yt_today_snapshot:
        lines.append("Followers:")

        if mc_today_snapshot:
            subscriber_count = mc_today_snapshot["subscriber_count"]
            arrow = format_trend_arrow(changes.get("mailchimp:subscriber_count", 0))
            suffix = f" {arrow}" if arrow else ""
            week_val = mc_week_snapshot["subscriber_count"] if mc_week_snapshot else None
            month_val = mc_month_snapshot["subscriber_count"] if mc_month_snapshot else None
            year_val = mc_year_snapshot["subscriber_count"] if mc_year_snapshot else None
            lines.append(f" Email: {subscriber_count:,}{suffix}" + format_subscriber_deltas(subscriber_count, week_val, month_val, year_val))

        if ig_today_snapshot:
            ig_week_snapshot = load_instagram_snapshot(week_ago_date)
            ig_month_snapshot = load_instagram_snapshot(month_ago_date)
            ig_year_snapshot = load_instagram_snapshot(year_ago_date)
            follower_count = ig_today_snapshot["follower_count"]
            arrow = format_trend_arrow(changes.get("instagram:follower_count", 0))
            suffix = f" {arrow}" if arrow else ""
            week_val = ig_week_snapshot["follower_count"] if ig_week_snapshot else None
            month_val = ig_month_snapshot["follower_count"] if ig_month_snapshot else None
            year_val = ig_year_snapshot["follower_count"] if ig_year_snapshot else None
            lines.append(f" Instagram: {follower_count:,}{suffix}" + format_subscriber_deltas(follower_count, week_val, month_val, year_val))

        if yt_today_snapshot:
            subscriber_count = yt_today_snapshot["subscriber_count"]
            arrow = format_trend_arrow(changes.get("youtube:subscriber_count", 0))
            suffix = f" {arrow}" if arrow else ""
            week_val = yt_week_snapshot["subscriber_count"] if yt_week_snapshot else None
            month_val = yt_month_snapshot["subscriber_count"] if yt_month_snapshot else None
            year_val = yt_year_snapshot["subscriber_count"] if yt_year_snapshot else None
            lines.append(f" YouTube: {subscriber_count:,}{suffix}" + format_subscriber_deltas(subscriber_count, week_val, month_val, year_val))

        lines.append("")

    # Group current subscriptions by product and billing cycle
    products: Dict[str, Dict[str, int]] = {}
    for row in subscriptions:
        product = row["Product Name"]
        if product not in products:
            products[product] = {"Monthly": 0, "Annual": 0}
        cycle = row["Billing Cycle"]
        if cycle == "Monthly":
            products[product]["Monthly"] = row["# of Active Subscriptions"]
        else:
            products[product]["Annual"] += row["# of Active Subscriptions"]

    # Explicit display order: TYL first, Archive second, anything else alpha.
    preferred_order = ["The Yoga Lifestyle Membership", "The Archive"]
    ordered = [p for p in preferred_order if p in products] + \
              sorted(k for k in products if k not in preferred_order)

    for product in ordered:
        monthly = products[product]["Monthly"]
        annual = products[product]["Annual"]
        display_name = simplify_product_name(product)

        # Day-over-day trend arrows beside the current Monthly / Yearly values
        day_monthly = day_counts.get(product, {}).get("Monthly", 0)
        day_annual = day_counts.get(product, {}).get("Annual", 0)
        monthly_arrow = format_trend_arrow(monthly - day_monthly)
        annual_arrow = format_trend_arrow(annual - day_annual)
        monthly_suffix = f" {monthly_arrow}" if monthly_arrow else ""
        annual_suffix = f" {annual_arrow}" if annual_arrow else ""

        lines.append(f"{display_name}:")

        monthly_segs: List[str] = []
        for label, hist in [("week", week_counts), ("month", month_counts), ("year", year_counts)]:
            if product not in hist:
                continue
            diff = monthly - hist[product]["Monthly"]
            if diff == 0:
                continue
            change = f"+{diff}" if diff > 0 else str(diff)
            monthly_segs.append(f"{label}: {change}")
        monthly_delta = "    |    𝚫  " + "  ".join(monthly_segs) if monthly_segs else ""
        lines.append(f"  Monthly: {monthly}{monthly_suffix}" + monthly_delta)

        annual_segs: List[str] = []
        for label, hist in [("week", week_counts), ("month", month_counts), ("year", year_counts)]:
            if product not in hist:
                continue
            diff = annual - hist[product]["Annual"]
            if diff == 0:
                continue
            change = f"+{diff}" if diff > 0 else str(diff)
            annual_segs.append(f"{label}: {change}")
        annual_delta = "    |    𝚫  " + "  ".join(annual_segs) if annual_segs else ""
        lines.append(f"  Yearly: {annual}{annual_suffix}" + annual_delta)

        lines.append("")

    # Next Habit class
    habit = get_next_habit_event()
    if habit:
        start = datetime.fromisoformat(habit["start"].replace("Z", "+00:00"))
        date_str = f"{start.strftime('%B')} {start.day}"
        lines.append(f"Habit: {date_str} - {habit['registrations']} registered")

    # Trim trailing blank line
    while lines and lines[-1] == "":
        lines.pop()

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


def main(dry_run: bool = False):
    """Main entry point."""
    print("=" * 60)
    print("Daily Status Report" + (" [DRY RUN]" if dry_run else ""))
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

        if dry_run:
            print("\n[DRY RUN] Skipping Slack post")
        else:
            post_to_slack(message)

        print("\n✓ Daily status report completed successfully")
        return 0

    except Exception as e:
        print(f"\n✗ Error: {e}")
        return 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="TWY daily status report")
    parser.add_argument("--dry-run", action="store_true", help="Print the report but do not post to Slack")
    args = parser.parse_args()
    sys.exit(main(dry_run=args.dry_run))
