#!/usr/bin/env python3
"""Post daily status report to Slack with Marvelous subscription data."""

import argparse
import csv
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple

import sqlite3
import requests
from twy_paths import load_env

# Load environment variables
from twy_paths import load_env, marvy_db_path
load_env()
load_env()

# Configuration
PROJECT_ROOT = Path(__file__).parent.parent
MAILCHIMP_HISTORY_DIR = PROJECT_ROOT / "data/mailchimp/history"
INSTAGRAM_HISTORY_DIR = PROJECT_ROOT / "data/instagram/history"
YOUTUBE_HISTORY_DIR = PROJECT_ROOT / "data/youtube/history"
REPORTS_DIR = PROJECT_ROOT / "data/reports"
MOVEMENT_CHANNEL = os.getenv("SLACK_MOVEMENT_CHANNEL", "C0BH3142LNP")
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



def hm_customer_link(email: str, name: str, db_path: Path = None) -> str:
    """Slack-linked name pointing at the HM customer page; plain name if unknown."""
    try:
        conn = sqlite3.connect(str(db_path or MARVY_DB))
        row = conn.execute(
            "SELECT id FROM customers WHERE lower(email) = ?", (email.strip().lower(),)
        ).fetchone()
        conn.close()
        if row:
            return f"<https://app.heymarvelous.com/customers/{row[0]}|{name}>"
    except Exception as e:
        print(f"  (customer link lookup failed for {email}: {e})")
    return name


def _latest_two(reports_dir: Path, prefix: str) -> List[Path]:
    return sorted(reports_dir.glob(f"{prefix}_*.csv"))[-2:]


def _rows_by_email(path: Path) -> Dict[str, Dict[str, str]]:
    with open(path, newline="") as f:
        return {r["email"].strip().lower() if "email" in r else r["Email"].strip().lower(): r
                for r in csv.DictReader(f)}


def _short_date(iso: str) -> str:
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).strftime("%b %-d")
    except ValueError:
        return iso


def get_member_movement(reports_dir: Path = None, db_path: Path = None) -> Tuple[List[str], List[str]]:
    """Names joined / canceled since the previous nightly HM report snapshots.

    Joins = emails newly present in the actives report (dated by signup `Created`).
    Cancels = emails newly present in the canceled report (dated by `canceled_at`,
    with access-until). Names link to the HM customer page. Needs two snapshots
    of each report; returns ([], []) when history is missing.
    """
    reports_dir = reports_dir or REPORTS_DIR
    joins: List[str] = []
    cancels: List[str] = []

    actives = _latest_two(reports_dir, "active_subscriptions")
    if len(actives) == 2:
        prev, cur = (_rows_by_email(x) for x in actives)
        for email in sorted(set(cur) - set(prev)):
            r = cur[email]
            name = f"{r['First Name']} {r['Last Name']}".strip() or email
            joined = _short_date(r.get("Created", ""))
            joins.append(f"{hm_customer_link(email, name, db_path)} (signed up {joined})")

    canceled = _latest_two(reports_dir, "canceled_subscriptions")
    if len(canceled) == 2:
        prev, cur = (_rows_by_email(x) for x in canceled)
        for email in sorted(set(cur) - set(prev)):
            r = cur[email]
            if r.get("product_name") == "The Yoga Lifestyle: On-demand Library":
                continue
            name = f"{r.get('first_name', '')} {r.get('last_name', '')}".strip() or email
            when = _short_date(r.get("canceled_at", ""))
            until = _short_date(r.get("subscription_active_until", ""))
            cancels.append(f"{hm_customer_link(email, name, db_path)} (canceled {when}, access until {until})")

    return joins, cancels


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


ZERNIO_BASE_URL = os.getenv("ZERNIO_BASE_URL", "https://zernio.com/api/v1").rstrip("/")


def fetch_instagram_follower_count() -> Optional[int]:
    """Fetch current Instagram follower count from Zernio.

    Zernio holds an official Instagram Graph API connection (OAuth,
    Business account), so this can run from Hetzner directly -- unlike
    the old instaloader-based fetch, which Instagram blocks from
    datacenter IPs and had to run on the Mac mini + scp over Tailscale.
    """
    api_key = os.getenv("ZERNIO_API_KEY", "").strip()
    if not api_key:
        print("Warning: ZERNIO_API_KEY not set, skipping Instagram snapshot")
        return None
    try:
        resp = requests.get(
            f"{ZERNIO_BASE_URL}/accounts",
            params={"platform": "instagram"},
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=30,
        )
        resp.raise_for_status()
        accounts = resp.json().get("accounts", [])
        if not accounts:
            print("Warning: Zernio returned no Instagram accounts")
            return None
        return accounts[0]["metadata"]["profileData"]["followersCount"]
    except Exception as e:
        print(f"Warning: Could not fetch Instagram follower count from Zernio: {e}")
        return None


def ensure_instagram_snapshot(date: str) -> None:
    """Write today's Instagram snapshot from Zernio if it doesn't already exist."""
    filepath = INSTAGRAM_HISTORY_DIR / f"{date}.json"
    if filepath.exists():
        return
    follower_count = fetch_instagram_follower_count()
    if follower_count is None:
        return
    INSTAGRAM_HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    with open(filepath, "w") as f:
        json.dump({
            "date": date,
            "timestamp": datetime.now().isoformat(),
            "follower_count": follower_count,
        }, f, indent=2)
    print(f"✓ Wrote Instagram snapshot for {date}: {follower_count} followers (via Zernio)")


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


def format_change(current: float, previous: float) -> str:
    """Format change with sign."""
    diff = current - previous
    if diff > 0:
        return f"+{diff:.0f}"
    elif diff < 0:
        return f"{diff:.0f}"
    else:
        return "0"


def format_delta_line(current: int, week_val: Optional[int], month_val: Optional[int], year_val: Optional[int]) -> str:
    """Return a delta line like '   𝚫 week: -4  |  month: -8', or '' if all deltas zero/missing."""
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
    return "   𝚫 " + "  |  ".join(segments)


def format_product_delta_line(product: str, cycle: str, current: int,
                              week_counts: Dict[str, Dict[str, int]],
                              month_counts: Dict[str, Dict[str, int]],
                              year_counts: Dict[str, Dict[str, int]]) -> str:
    """Return a delta line for a product/cycle against historical counts, or '' if no deltas."""
    segments: List[str] = []
    for label, hist in (("week", week_counts), ("month", month_counts), ("year", year_counts)):
        if product not in hist:
            continue
        diff = current - hist[product][cycle]
        if diff == 0:
            continue
        change = f"+{diff}" if diff > 0 else str(diff)
        segments.append(f"{label}: {change}")
    if not segments:
        return ""
    return "   𝚫 " + "  |  ".join(segments)


def format_report(subscriptions: List[Dict[str, Any]], today: str, changes: Dict[str, int]) -> str:
    """Format subscription data into Slack message with historical comparisons."""
    now = datetime.now()
    week_ago_date = (now - timedelta(days=7)).strftime("%Y-%m-%d")
    month_ago_date = (now - timedelta(days=30)).strftime("%Y-%m-%d")
    year_ago_date = (now - timedelta(days=365)).strftime("%Y-%m-%d")

    mc_today_snap = load_mailchimp_snapshot(today)
    mc_week_snap = load_mailchimp_snapshot(week_ago_date)
    mc_month_snap = load_mailchimp_snapshot(month_ago_date)
    mc_year_snap = load_mailchimp_snapshot(year_ago_date)

    ig_today_snap = load_instagram_snapshot(today)
    ig_week_snap = load_instagram_snapshot(week_ago_date)
    ig_month_snap = load_instagram_snapshot(month_ago_date)
    ig_year_snap = load_instagram_snapshot(year_ago_date)

    yt_today_snap = load_youtube_snapshot(today)
    yt_week_snap = load_youtube_snapshot(week_ago_date)
    yt_month_snap = load_youtube_snapshot(month_ago_date)
    yt_year_snap = load_youtube_snapshot(year_ago_date)

    week_counts = get_product_counts_ago(7)
    month_counts = get_product_counts_ago(30)
    year_counts = get_product_counts_ago(365)

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

    groups: List[List[str]] = []

    # Followers (Email / Instagram / YouTube)
    followers: List[str] = []
    for label, today_snap, week_snap, month_snap, year_snap, key in (
        ("Email", mc_today_snap, mc_week_snap, mc_month_snap, mc_year_snap, "subscriber_count"),
        ("Instagram", ig_today_snap, ig_week_snap, ig_month_snap, ig_year_snap, "follower_count"),
        ("YouTube", yt_today_snap, yt_week_snap, yt_month_snap, yt_year_snap, "subscriber_count"),
    ):
        if not today_snap:
            continue
        current = today_snap[key]
        followers.append(f"*{label}*: {current:,}")
        week_val = week_snap[key] if week_snap else None
        month_val = month_snap[key] if month_snap else None
        year_val = year_snap[key] if year_snap else None
        delta = format_delta_line(current, week_val, month_val, year_val)
        if delta:
            followers.append(delta)
    if followers:
        groups.append(followers)

    # TYL (The Yoga Lifestyle Membership)
    tyl_product = "The Yoga Lifestyle Membership"
    tyl_lines: List[str] = []
    if tyl_product in products:
        for cycle, display_cycle in (("Monthly", "Month"), ("Annual", "Annual")):
            count = products[tyl_product][cycle]
            if count == 0:
                continue
            tyl_lines.append(f"*TYL {display_cycle}*: {count}")
            delta = format_product_delta_line(tyl_product, cycle, count, week_counts, month_counts, year_counts)
            if delta:
                tyl_lines.append(delta)
    if tyl_lines:
        groups.append(tyl_lines)

    # TWA (The Archive, yearly only)
    twa_product = "The Archive"
    twa_lines: List[str] = []
    if twa_product in products:
        count = products[twa_product]["Annual"]
        if count > 0:
            twa_lines.append(f"*TWA Yearly*: {count}")
            delta = format_product_delta_line(twa_product, "Annual", count, week_counts, month_counts, year_counts)
            if delta:
                twa_lines.append(delta)
    if twa_lines:
        groups.append(twa_lines)

    # Habit
    habit = get_next_habit_event()
    if habit:
        start = datetime.fromisoformat(habit["start"].replace("Z", "+00:00"))
        date_str = f"{start.strftime('%B')} {start.day}"
        groups.append([f"*Habit*: {date_str} - {habit['registrations']} registered"])

    return "\n\n".join("\n".join(g) for g in groups)


def post_to_slack(message: str, channel: str = None):
    """Post message to Slack. An explicit channel uses the bot token
    (the webhook is bound to its own channel and cannot be redirected)."""
    webhook_url = os.getenv("SLACK_WEBHOOK_URL")
    bot_token = os.getenv("SLACK_BOT_TOKEN")
    if channel is None:
        channel = os.getenv("SLACK_CHANNEL", "#twy-status")
    else:
        webhook_url = None  # explicit channel -> bot-token path only

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


def format_movement_post(movement: Tuple[List[str], List[str]]) -> str:
    """Standalone Slack post for member joins/cancels. Empty string when none."""
    joins, cancels = movement
    lines: List[str] = []
    for line in joins:
        lines.append(f"*Joined*: {line}")
    for line in cancels:
        lines.append(f"*Canceled*: {line}")
    return "\n".join(lines)


def main(dry_run: bool = False):
    """Main entry point."""
    print("=" * 60)
    print("Daily Status Report" + (" [DRY RUN]" if dry_run else ""))
    print("=" * 60)

    today = datetime.now().strftime("%Y-%m-%d")
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

    try:
        ensure_instagram_snapshot(today)

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

        # Member movement from the nightly HM report snapshots (fail-soft)
        try:
            movement = get_member_movement()
        except Exception as e:
            print(f"  (member movement unavailable: {e})")
            movement = ([], [])

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

        if should_send:
            message = format_report(subscriptions, today, changes)
            print("\nReport preview:")
            print("-" * 60)
            print(message)
            print("-" * 60)
            if dry_run:
                print("\n[DRY RUN] Skipping Slack post")
            else:
                post_to_slack(message)
        else:
            print("\n✓ Skipping report (no changes)")

        # Member movement goes out as its own post, only when there is any --
        # independent of the report's send decision (a cancel can be
        # count-neutral for weeks while access runs out).
        movement_msg = format_movement_post(movement)
        if movement_msg:
            print("\nMember movement post:")
            print("-" * 60)
            print(movement_msg)
            print("-" * 60)
            if dry_run:
                print("\n[DRY RUN] Skipping movement post")
            else:
                post_to_slack(movement_msg, channel=MOVEMENT_CHANNEL)

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
