#!/usr/bin/env python3
"""
Create or update MailChimp campaigns for the current month's newsletters,
schedule them, and post confirmation to #status-newsletters.

Usage: python3 run_campaigns.py [YYYY-MM]
Default: current month in Mountain Time.
"""
import os
import sys
import requests as req
from zoneinfo import ZoneInfo
from datetime import datetime, date
from pathlib import Path

from twy_paths import load_env

load_env()

sys.path.insert(0, str(Path(__file__).parent))

from newsletter import newsletter_path
from mailchimp_campaigns import create_or_update_draft
from slack import post_slack

MOUNTAIN             = ZoneInfo("America/Denver")
SLACK_STATUS_CHANNEL = os.getenv("SLACK_STATUS_CHANNEL", "#status-newsletters")
MAILCHIMP_API_KEY    = os.getenv("MAILCHIMP_API_KEY", "")
MAILCHIMP_SERVER_PREFIX = os.getenv("MAILCHIMP_SERVER_PREFIX", "")

AUDIENCE_ID  = "a221e4ba21"
TEMPLATE_ID  = 10576833
SEGMENTS     = {"lifestyle": 3019143, "non-lifestyle": 3019144}
LABELS       = {"lifestyle": "Lifestyle", "non-lifestyle": "Non-Lifestyle"}

# Schedule: first weekday (Mon-Fri) on/after the 1st of the target month, at 9am MT.
# Computed in MT then converted to UTC (handles DST automatically via ZoneInfo).
from datetime import time, timedelta, timezone

def first_weekday_9am_mt_for_month(year: int, month: int) -> str:
    d = date(year, month, 1)
    while d.weekday() >= 5:  # 5=Sat, 6=Sun -> push to Monday
        d += timedelta(days=1)
    dt_mt = datetime.combine(d, time(9, 0), tzinfo=MOUNTAIN)
    dt_utc = dt_mt.astimezone(timezone.utc)
    return dt_utc.strftime("%Y-%m-%dT%H:%M:%S+00:00")


def mc_url(path):
    return f"https://{MAILCHIMP_SERVER_PREFIX}.api.mailchimp.com/3.0{path}"


def mc_auth():
    return ("anystring", MAILCHIMP_API_KEY)


def main():
    if len(sys.argv) > 1:
        year, month = [int(x) for x in sys.argv[1].split("-")]
    else:
        now = datetime.now(MOUNTAIN)
        year, month = now.year, now.month

    month_label = date(year, month, 1).strftime("%B %Y")
    send_time = first_weekday_9am_mt_for_month(year, month)
    print(f"Running campaigns for {month_label}, schedule: {send_time}")

    # Guard: if computed send_time is in the past, abort before creating campaigns.
    # Usually means cron fired with a UTC-shifted clock that landed in the prior month MT.
    send_dt = datetime.strptime(send_time, "%Y-%m-%dT%H:%M:%S%z")
    if send_dt < datetime.now(timezone.utc):
        msg = (
            f"refusing to schedule {month_label}: send_time {send_time} is in the past "
            f"(now={datetime.now(timezone.utc).isoformat()}). "
            "Likely cron timezone mismatch. Check that cron fires after the 1st in MT."
        )
        print("ERROR:", msg)
        post_slack(SLACK_STATUS_CHANNEL, f":warning: run_campaigns aborted: {msg}")
        sys.exit(1)

    results = {}
    errors = []

    for audience in ("lifestyle", "non-lifestyle"):
        nl_path = newsletter_path(year, month, audience)
        if not nl_path.exists():
            errors.append(f"{audience}: newsletter file not found at {nl_path}")
            continue

        content = nl_path.read_text()
        # First line is "# Subject", rest is body
        lines = content.split("\n", 2)
        subject = lines[0].lstrip("# ").strip()
        body_md = lines[2].strip() if len(lines) > 2 else ""

        label = LABELS[audience]
        campaign_title = f"{year}-{month:02d} \u2014 {label} \u2014 Yoga Habit"

        try:
            result = create_or_update_draft(
                subject=subject,
                body_md=body_md,
                list_id=AUDIENCE_ID,
                segment_id=SEGMENTS[audience],
                campaign_title=campaign_title,
            )
            campaign_id = result["id"]
            print(f"  {label}: {result['action']} campaign {campaign_id}")

            # Schedule
            r = req.post(
                mc_url(f"/campaigns/{campaign_id}/actions/schedule"),
                auth=mc_auth(),
                json={"schedule_time": send_time},
                timeout=15,
            )
            if r.status_code == 400 and "already scheduled" in r.text:
                req.post(mc_url(f"/campaigns/{campaign_id}/actions/unschedule"),
                         auth=mc_auth(), timeout=15)
                r = req.post(mc_url(f"/campaigns/{campaign_id}/actions/schedule"),
                             auth=mc_auth(), json={"schedule_time": send_time}, timeout=15)
            if r.status_code != 204:
                errors.append(f"{label}: schedule failed {r.status_code} {r.text[:80]}")
                continue

            archive_url = f"https://us21.campaign-archive.com/?u=a6369901d6f0c448fbcc61e6e&id={campaign_id}"
            results[audience] = {"label": label, "id": campaign_id, "archive": archive_url, "action": result["action"]}
            print(f"  {label}: scheduled. Archive: {archive_url}")

        except Exception as e:
            errors.append(f"{label}: {e}")

    # Post to #status-newsletters
    if errors:
        post_slack(SLACK_STATUS_CHANNEL, f":warning: Campaign errors for {month_label}:\n" + "\n".join(errors))

    if results:
        send_display = datetime.strptime(send_time[:10], "%Y-%m-%d").strftime("%A %b %-d") + " 9am MT"
        lines = [f":calendar: *{month_label} campaigns scheduled* \u2014 sending {send_display}"]
        for r in results.values():
            lines.append(f"  *{r['label']}* ({r['action']}): {r['archive']}")
        post_slack(SLACK_STATUS_CHANNEL, "\n".join(lines))

    if errors:
        print("ERRORS:", errors)
        sys.exit(1)


if __name__ == "__main__":
    main()
