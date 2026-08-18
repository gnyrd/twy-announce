#!/usr/bin/env python3
"""Post daily status report to Slack with Marvelous subscription data."""

import argparse
import csv
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Dict, Any, Optional

import sqlite3
import requests
from twy_paths import (
    email_history_dir,
    facebook_history_dir,
    hm_subscriptions_dir,
    instagram_history_dir,
    load_env,
    mailchimp_history_dir,
    marvy_db_path,
    youtube_history_dir,
)
from marvelous_memberships import latest_fresh_snapshot
from twy_platform.membership import cycle_of, is_member_row, report_date
from twy_platform import meta

# Load environment variables
load_env()

# Configuration
EMAIL_HISTORY_DIR = email_history_dir()
LEGACY_EMAIL_HISTORY_DIR = mailchimp_history_dir()
INSTAGRAM_HISTORY_DIR = instagram_history_dir()
FACEBOOK_HISTORY_DIR = facebook_history_dir()
YOUTUBE_HISTORY_DIR = youtube_history_dir()
REPORTS_DIR = hm_subscriptions_dir()
MARVY_DB = marvy_db_path()


ANNUAL_SPLIT = "1year"
ONDEMAND_PRODUCT = "The Yoga Lifestyle: On-demand Library"


def _latest_report(reports_dir: Path = None) -> Optional[Path]:
    """Newest HM Active Subscriptions CSV, or None if none exist."""
    reports_dir = reports_dir or REPORTS_DIR
    hits = sorted(reports_dir.glob("active_subscriptions_*.csv"))
    return hits[-1] if hits else None


def _report_on_or_before(target: datetime, reports_dir: Path = None) -> Optional[Path]:
    """Newest HM Active Subscriptions CSV dated on or before `target`.

    Returns None for dates earlier than the first snapshot (2026-03-19), which
    is the caller's signal to fall back to reconstruction.
    """
    reports_dir = reports_dir or REPORTS_DIR
    stamp = target.strftime("%Y%m%d")
    hits = sorted(p for p in reports_dir.glob("active_subscriptions_*.csv")
                  if p.name[len("active_subscriptions_"):len("active_subscriptions_") + 8] <= stamp)
    return hits[-1] if hits else None


def counts_from_report(path: Path) -> Dict[str, Dict[str, int]]:
    """{product: {"Monthly": n, "Annual": n}} from an HM report CSV.

    Billing cycle comes from the report's REAL `split_part` column
    ('1year' = annual, 'month'/'1month' = monthly). Never infer it from the
    amount paid: `amount_paid > price * 3` invents an annual tier that does
    not exist and misfiled a $545 payment against a $99 monthly price as
    annual, posting TYL 25/3 where the truth was 26/2 (2026-07-21).
    """
    as_of = report_date(path)
    out: Dict[str, Dict[str, int]] = {}
    with open(path, newline="") as fh:
        for r in csv.DictReader(fh):
            if not is_member_row(r, as_of):
                continue
            product = (r.get("Product Name") or "").strip()
            cycle = cycle_of(r).capitalize()
            out.setdefault(product, {"Monthly": 0, "Annual": 0})
            out[product][cycle] += 1
    return out


def _revenue_from_report(path: Path) -> Dict[str, Dict[str, float]]:
    """{product: {cycle: summed recurring Price}} from an HM report CSV."""
    as_of = report_date(path)
    out: Dict[str, Dict[str, float]] = {}
    with open(path, newline="") as fh:
        for r in csv.DictReader(fh):
            if not is_member_row(r, as_of):
                continue
            product = (r.get("Product Name") or "").strip()
            cycle = cycle_of(r).capitalize()
            try:
                price = float(r.get("Price") or 0)
            except ValueError:
                price = 0.0
            out.setdefault(product, {"Monthly": 0.0, "Annual": 0.0})
            out[product][cycle] += price
    return out


def get_marvelous_data() -> List[Dict[str, Any]]:
    """Active subscription counts per product and billing cycle.

    Source of truth is the nightly HM Active Subscriptions report, whose
    `split_part` column carries the real billing cycle. Fails loudly rather
    than falling back to marvy.db: the old query inferred the cycle from
    `amount_paid > price * 3`, and posting a wrong split is worse than
    posting nothing.
    """
    path = latest_fresh_snapshot(
        reports_dir=REPORTS_DIR,
        prefix="active_subscriptions",
        max_age_hours=26,
    )
    counts = counts_from_report(path)
    revenue = _revenue_from_report(path)
    rows: List[Dict[str, Any]] = []
    for product in sorted(counts):
        for cycle in ("Annual", "Monthly"):
            n = counts[product][cycle]
            if n == 0:
                continue
            rows.append({
                "Product Name": product,
                "Billing Cycle": cycle,
                "# of Active Subscriptions": n,
                "Revenue per Cycle": revenue.get(product, {}).get(cycle, 0.0),
            })
    return rows



def get_member_count_ago(days: int) -> int:
    """Total active recurring-subscription count N days ago.

    Prefers the HM Active Subscriptions snapshot for that date, which is an
    exact count. This also keeps the send-decision comparison apples-to-apples:
    the current total now comes from the newest snapshot, so both sides of
    `current_total != hm_yesterday_total` share one source instead of pitting
    a marvy.db query against a reconstruction.

    Falls back to historical_active_counts.active_count_at only for dates
    before the first snapshot (2026-03-19). That path picks its per-purchase
    coverage window (31d vs 366d) by classifying the billing cycle from the
    amount paid, the same unreliable rule this module was fixed to stop using,
    so it is deliberately off the hot path.
    """
    target = datetime.now() - timedelta(days=days)
    path = _report_on_or_before(target)
    if path is not None:
        counts = counts_from_report(path)
        return sum(c["Monthly"] + c["Annual"] for c in counts.values())
    from historical_active_counts import active_count_at
    return active_count_at(target)


def get_product_counts_ago(days: int) -> Dict[str, Dict[str, int]]:
    """Per-product, per-billing-cycle active subscription counts N days ago.

    Prefers the HM Active Subscriptions snapshot for that date, which carries
    the real `split_part` billing cycle, so the week and month deltas are
    exact. Only for dates before the first snapshot (2026-03-19) does it fall
    back to historical_active_counts.active_at, whose cycle split is inferred
    from amount paid and is therefore approximate. In practice that fallback
    only affects the year delta.
    """
    target = datetime.now() - timedelta(days=days)
    path = _report_on_or_before(target)
    if path is not None:
        return counts_from_report(path)
    # No snapshot that far back (before 2026-03-19). Fall back to the same
    # reconstruction that backs the verified TYL chart: a 31-day purchase
    # window for the monthly count, and the annual count taken from the
    # known-annuals list rather than an amount-paid guess. Accurate to about
    # +/-1 on monthly. Deliberately NOT historical_active_counts.active_at,
    # whose amount-paid classification is the defect this function was fixed
    # to stop repeating.
    from membership_history import known_annuals, from_purchase_window
    monthly, annual, _total = from_purchase_window(target, known_annuals())
    return {"The Yoga Lifestyle Membership": {"Monthly": monthly, "Annual": annual}}


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


def load_email_snapshot(date: str) -> Optional[Dict[str, Any]]:
    """Load current email count, with read only historical archive fallback."""
    current = EMAIL_HISTORY_DIR / f"{date}.json"
    legacy = LEGACY_EMAIL_HISTORY_DIR / f"{date}.json"
    filepath = current if current.exists() else legacy
    if not filepath.exists():
        return None
    try:
        with open(filepath) as f:
            return json.load(f)
    except Exception as e:
        print(f"Warning: Could not load email snapshot for {date}: {e}")
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


def load_facebook_snapshot(date: str) -> Optional[Dict[str, Any]]:
    """Load Facebook snapshot for a specific date."""
    filepath = FACEBOOK_HISTORY_DIR / f"{date}.json"
    if not filepath.exists():
        return None
    try:
        with open(filepath) as f:
            return json.load(f)
    except Exception as e:
        print(f"Warning: Could not load Facebook snapshot for {date}: {e}")
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


def fetch_instagram_follower_count() -> Optional[int]:
    """Instagram follower count from Meta (shared twy_platform.meta reader)."""
    count = meta.page_followers(meta.page_access_token())["instagram"]
    if count is None:
        print("Warning: could not read Instagram follower count from Meta")
    return count


def ensure_instagram_snapshot(date: str) -> None:
    """Write today's Instagram snapshot from Meta if it doesn't already exist."""
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
    print(f"✓ Wrote Instagram snapshot for {date}: {follower_count} followers (via Meta)")


def fetch_facebook_follower_count() -> Optional[int]:
    """Facebook Page follower count from Meta (shared twy_platform.meta reader)."""
    count = meta.page_followers(meta.page_access_token())["facebook"]
    if count is None:
        print("Warning: could not read Facebook follower count from Meta")
    return count


def ensure_facebook_snapshot(date: str) -> None:
    """Write today Facebook snapshot from Meta if it does not already exist."""
    filepath = FACEBOOK_HISTORY_DIR / f"{date}.json"
    if filepath.exists():
        return
    follower_count = fetch_facebook_follower_count()
    if follower_count is None:
        return
    FACEBOOK_HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    with open(filepath, "w") as f:
        json.dump({
            "date": date,
            "timestamp": datetime.now().isoformat(),
            "follower_count": follower_count,
        }, f, indent=2)
    print(f"✓ Wrote Facebook snapshot for {date}: {follower_count} followers (via Meta)")


def extract_subscriber_counts(
    email_snapshot: Optional[Dict[str, Any]],
    instagram: Optional[Dict[str, Any]],
    facebook: Optional[Dict[str, Any]],
    youtube: Optional[Dict[str, Any]]
) -> Dict[str, int]:
    """Extract email/social subscriber counts into a flat dict for comparison."""
    counts = {}
    if email_snapshot:
        counts["email:subscriber_count"] = email_snapshot.get(
            "subscriber_count",
            0,
        )
    if instagram:
        counts["instagram:follower_count"] = instagram.get("follower_count", 0)
    if facebook:
        counts["facebook:follower_count"] = facebook.get("follower_count", 0)
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

    email_today_snap = load_email_snapshot(today)
    email_week_snap = load_email_snapshot(week_ago_date)
    email_month_snap = load_email_snapshot(month_ago_date)
    email_year_snap = load_email_snapshot(year_ago_date)

    ig_today_snap = load_instagram_snapshot(today)
    ig_week_snap = load_instagram_snapshot(week_ago_date)
    ig_month_snap = load_instagram_snapshot(month_ago_date)
    ig_year_snap = load_instagram_snapshot(year_ago_date)

    fb_today_snap = load_facebook_snapshot(today)
    fb_week_snap = load_facebook_snapshot(week_ago_date)
    fb_month_snap = load_facebook_snapshot(month_ago_date)
    fb_year_snap = load_facebook_snapshot(year_ago_date)

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
        (
            "Email",
            email_today_snap,
            email_week_snap,
            email_month_snap,
            email_year_snap,
            "subscriber_count",
        ),
        ("Instagram", ig_today_snap, ig_week_snap, ig_month_snap, ig_year_snap, "follower_count"),
        ("Facebook", fb_today_snap, fb_week_snap, fb_month_snap, fb_year_snap, "follower_count"),
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


def main(dry_run: bool = False):
    """Main entry point."""
    print("=" * 60)
    print("Daily Status Report" + (" [DRY RUN]" if dry_run else ""))
    print("=" * 60)

    today = datetime.now().strftime("%Y-%m-%d")
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

    try:
        ensure_instagram_snapshot(today)
        ensure_facebook_snapshot(today)

        subscriptions = get_marvelous_data()

        # Load today's subscriber snapshots (email/social only)
        email_today = load_email_snapshot(today)
        ig_today = load_instagram_snapshot(today)
        fb_today = load_facebook_snapshot(today)
        yt_today = load_youtube_snapshot(today)

        # Load yesterday's subscriber snapshots for comparison
        email_yesterday = load_email_snapshot(yesterday)
        ig_yesterday = load_instagram_snapshot(yesterday)
        fb_yesterday = load_facebook_snapshot(yesterday)
        yt_yesterday = load_youtube_snapshot(yesterday)

        today_counts = extract_subscriber_counts(
            email_today,
            ig_today,
            fb_today,
            yt_today,
        )
        yesterday_counts = extract_subscriber_counts(
            email_yesterday,
            ig_yesterday,
            fb_yesterday,
            yt_yesterday,
        )
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
