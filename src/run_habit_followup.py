#!/usr/bin/env python3
"""
Day-of cron: runs after the Yoga Habit free class.
Checks if today is a Habit class day. If so:
  1. Reads pre-written PH1/PH2 copy
  2. Injects coupon URL
  3. Creates MC segment (Yoga Habit - YYYY-MM AND NOT Lifestyle)
  4. Creates and schedules MC campaigns
  5. Posts campaign links to #review-newsletters

Run daily at 11am MT (17:00 UTC) — after class ends at 10am MT.
"""
import os
import sys
import requests
import logging
from datetime import datetime, date, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "paths"))
from twy_paths import load_env

load_env()

sys.path.insert(0, str(Path(__file__).parent))
from newsletter import newsletter_path
from mailchimp_campaigns import create_or_update_draft
from slack import post_slack

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

MOUNTAIN             = ZoneInfo("America/Denver")
CLASSES_API          = "http://localhost:5003"
MEMBERSHIP_PRODUCT   = 52025
LIFESTYLE_TAG_ID     = 3018884   # "Membership - Yoga Lifestyle" — stable, does not change monthly
SLACK_REVIEW_CHANNEL = os.getenv("SLACK_REVIEW_CHANNEL", "#review-newsletters")
MAILCHIMP_API_KEY    = os.getenv("MAILCHIMP_API_KEY", "")
MAILCHIMP_LIST_ID    = os.getenv("MAILCHIMP_AUDIENCE_ID", "")
MC_SERVER            = os.getenv("MAILCHIMP_SERVER_PREFIX", "")


def mc_url(path: str) -> str:
    return f"https://{MC_SERVER}.api.mailchimp.com/3.0{path}"


def mc_auth() -> tuple:
    return ("anystring", MAILCHIMP_API_KEY)


def marvy_client():
    sys.path.insert(0, "/root/twy/marvy")
    from marvy import Client
    resp = requests.post(
        "https://api.namastream.com/auth/login/",
        json={"email": os.environ["MARVELOUS_USERNAME"], "password": os.environ["MARVELOUS_PASSWORD"]},
        headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"},
        timeout=15,
    )
    resp.raise_for_status()
    return Client(auth_token=resp.json()["key"])


def is_habit_class_today(today: date) -> bool:
    """Check the classes API for a Habit class plan today."""
    try:
        resp = requests.get(f"{CLASSES_API}/api/plans/{today.isoformat()}", timeout=10)
        if resp.ok:
            plan = resp.json()
            return plan.get("class_type") == "Habit"
    except requests.RequestException as e:
        log.error("Failed to check classes API: %s", e)
    return False


def find_existing_campaign(title: str) -> dict | None:
    """Look up existing campaign by title. Returns campaign dict or None."""
    resp = requests.get(
        mc_url("/campaigns"),
        params={"count": 50, "status": "save,schedule,sending,sent"},
        auth=mc_auth(),
        timeout=15,
    )
    if not resp.ok:
        return None
    for c in resp.json().get("campaigns", []):
        if c.get("settings", {}).get("title") == title:
            return c
    return None


def get_habit_coupon_url(today: date) -> str:
    """Look up this month's Habit coupon via marvy, return checkout URL.
    Falls back to HABIT_[MON][YYYY] convention if marvy lookup fails."""
    mon  = today.strftime("%b").upper()
    yyyy = str(today.year)
    expected_code = f"HABIT_{mon}{yyyy}"
    try:
        c = marvy_client()
        results = c.list_coupons(page=1, search=expected_code).get("results", [])
        for coupon in results:
            if coupon["code"] == expected_code:
                return f"https://studio.tiffanywoodyoga.com/buy/product/{MEMBERSHIP_PRODUCT}?coupon={coupon['code']}"
        log.warning("Coupon %s not found via marvy; using convention", expected_code)
    except (requests.RequestException, KeyError, ValueError) as e:
        log.warning("marvy coupon lookup failed: %s; using convention", e)
    return f"https://studio.tiffanywoodyoga.com/buy/product/{MEMBERSHIP_PRODUCT}?coupon={expected_code}"


def get_followup_copy(year: int, month: int, audience: str) -> tuple[str, str]:
    """Read pre-written ph1 or ph2 copy. Returns (subject, body)."""
    path = newsletter_path(year, month, audience)
    if not path.exists():
        raise FileNotFoundError(f"No {audience} copy at {path} - has Tweee generated it?")
    content = path.read_text()
    lines = content.split("\n", 2)
    subject = lines[0].lstrip("# ").strip()
    body = lines[2].strip() if len(lines) > 2 else ""
    return subject, body


def create_or_get_segment(year: int, month: int) -> int:
    """Create the 'Yoga Habit - YYYY-MM (Non-Lifestyle)' MC segment. Idempotent."""
    # Look up this month's Yoga Habit tag ID
    tag_name = f"Yoga Habit - {year:04d}-{month:02d}"
    resp = requests.get(
        mc_url(f"/lists/{MAILCHIMP_LIST_ID}/tag-search"),
        params={"name": tag_name},
        auth=mc_auth(),
        timeout=15,
    )
    resp.raise_for_status()
    tags = resp.json().get("tags", [])
    if not tags:
        raise ValueError(f"MC tag '{tag_name}' not found — has the Habit class synced yet?")
    habit_tag_id = tags[0]["id"]

    seg_name = f"Yoga Habit - {year:04d}-{month:02d} (Non-Lifestyle)"

    # Check if segment already exists
    seg_resp = requests.get(
        mc_url(f"/lists/{MAILCHIMP_LIST_ID}/segments"),
        params={"count": 100},
        auth=mc_auth(),
        timeout=15,
    )
    for seg in seg_resp.json().get("segments", []):
        if seg["name"] == seg_name:
            log.info("Segment already exists: %s (id=%s, %s members)", seg_name, seg["id"], seg["member_count"])
            return seg["id"]

    # Create segment
    payload = {
        "name": seg_name,
        "options": {
            "match": "all",
            "conditions": [
                {"condition_type": "StaticSegment", "field": "static_segment", "op": "static_is", "value": habit_tag_id},
                {"condition_type": "StaticSegment", "field": "static_segment", "op": "static_not", "value": LIFESTYLE_TAG_ID},
            ],
        },
    }
    create_resp = requests.post(
        mc_url(f"/lists/{MAILCHIMP_LIST_ID}/segments"),
        auth=mc_auth(),
        json=payload,
        timeout=15,
    )
    create_resp.raise_for_status()
    seg = create_resp.json()
    log.info("Created segment: %s (id=%s, %s members)", seg_name, seg["id"], seg["member_count"])
    return seg["id"]


def schedule_campaign(campaign_id: str, send_time: str) -> None:
    """Schedule a campaign. Unschedules first if already scheduled."""
    r = requests.post(mc_url(f"/campaigns/{campaign_id}/actions/schedule"),
                      auth=mc_auth(), json={"schedule_time": send_time}, timeout=15)
    if r.status_code == 400 and "already scheduled" in r.text:
        unsched = requests.post(mc_url(f"/campaigns/{campaign_id}/actions/unschedule"),
                                auth=mc_auth(), timeout=15)
        if unsched.status_code != 204:
            raise RuntimeError(
                f"Unschedule failed for {campaign_id}: {unsched.status_code} {unsched.text[:120]}"
            )
        r = requests.post(mc_url(f"/campaigns/{campaign_id}/actions/schedule"),
                          auth=mc_auth(), json={"schedule_time": send_time}, timeout=15)
    if r.status_code != 204:
        raise RuntimeError(f"Schedule failed: {r.status_code} {r.text[:120]}")


def main():
    today = datetime.now(MOUNTAIN).date()
    year, month = today.year, today.month
    month_label = today.strftime("%B %Y")

    if not is_habit_class_today(today):
        log.info("%s: no Habit class today, nothing to do", today)
        return

    # Short-circuit: if ph1 campaign for this month already exists AND is scheduled or sent,
    # assume the workflow has already run successfully today.
    existing_ph1_title = f"{year:04d}-{month:02d} — Yoga Habit — Post-Class 1"
    existing = find_existing_campaign(existing_ph1_title)
    if existing and existing.get("status") in ("schedule", "sending", "sent"):
        log.info("PH1 campaign already %s (id=%s) — skipping follow-up workflow", existing["status"], existing["id"])
        return

    log.info("Habit class detected for %s — running follow-up workflow", today)

    coupon_url = get_habit_coupon_url(today)
    log.info("Coupon URL: %s", coupon_url)

    segment_id = create_or_get_segment(year, month)

    # PH1: +24hrs from class end (class ends 10am MT = 16:00 UTC, so PH1 = tomorrow 16:00 UTC)
    ph1_send = (today + timedelta(days=1)).isoformat() + "T16:00:00+00:00"
    # PH2: +7 days from class end
    ph2_send = (today + timedelta(days=7)).isoformat() + "T16:00:00+00:00"

    results = []
    for audience, label, send_time in [
        ("ph1", "Post-Class 1", ph1_send),
        ("ph2", "Post-Class 2", ph2_send),
    ]:
        subject, body = get_followup_copy(year, month, audience)
        body_with_link = body.replace("[link]", coupon_url)
        campaign_title = f"{year:04d}-{month:02d} — Yoga Habit — {label}"

        result = create_or_update_draft(
            subject=subject,
            body_md=body_with_link,
            list_id=MAILCHIMP_LIST_ID,
            segment_id=segment_id,
            campaign_title=campaign_title,
        )
        campaign_id = result["id"]
        schedule_campaign(campaign_id, send_time)
        archive_url = f"https://us21.campaign-archive.com/?u=a6369901d6f0c448fbcc61e6e&id={campaign_id}"
        results.append((label, campaign_id, send_time[:10], archive_url, result["action"]))
        log.info("%s: %s campaign %s, scheduled %s", label, result["action"], campaign_id, send_time[:10])

    # Post to #review-newsletters
    lines = [f":calendar: *Yoga Habit follow-up campaigns — {month_label}*"]
    for label, cid, send_date, archive, action in results:
        lines.append(f"  *{label}* ({action}): {archive} — sends {send_date}")
    lines.append(f"  Coupon: `{coupon_url.split('?coupon=')[1]}`")
    post_slack(SLACK_REVIEW_CHANNEL, "\n".join(lines))

    log.info("Done. %d campaigns scheduled.", len(results))


if __name__ == "__main__":
    main()
