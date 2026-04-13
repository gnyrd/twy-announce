#!/usr/bin/env python3
"""
Daily cron: tag Yoga Habit attendees who became Yoga Lifestyle subscribers.

For each open HABIT_* coupon window:
  1. Find MC contacts tagged 'Yoga Habit - YYYY-MM'
  2. Cross-ref with contacts tagged 'Membership - Yoga Lifestyle'
  3. Tag matches with 'Yoga Habit - YYYY-MM - Redeemed' (idempotent)

marvy has no purchase API so this uses MC tag cross-reference as a redemption proxy.
"""
import os
import sys
import hashlib
import requests
import logging
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "paths"))
from twy_paths import load_env

load_env()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

MAILCHIMP_API_KEY  = os.getenv("MAILCHIMP_API_KEY", "")
MAILCHIMP_LIST_ID  = os.getenv("MAILCHIMP_AUDIENCE_ID", "")
MC_SERVER          = os.getenv("MAILCHIMP_SERVER_PREFIX", "")
LIFESTYLE_TAG_NAME = "Membership - Yoga Lifestyle"


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


def get_open_habit_coupons() -> list[dict]:
    """Return HABIT_* coupons whose redeem_end is today or in the future."""
    today = date.today().isoformat()
    c = marvy_client()
    open_coupons = []
    page = 1
    while True:
        resp = c.list_coupons(page=page)
        for coupon in resp.get("results", []):
            code = coupon.get("code") or ""
            if code.startswith("HABIT_") and coupon.get("redeem_end", "") >= today:
                open_coupons.append(coupon)
        if not resp.get("next"):
            break
        page += 1
    return open_coupons


def parse_habit_tag_month(coupon_code: str) -> tuple[int, int] | None:
    """Parse HABIT_MAY2026 -> (2026, 5). Returns None if unparseable."""
    import calendar
    try:
        # HABIT_[MON][YYYY] e.g. HABIT_MAY2026
        suffix = coupon_code.replace("HABIT_", "")  # MAY2026
        mon_abbr = suffix[:3]   # MAY
        yyyy = int(suffix[3:])  # 2026
        months = {m.upper(): i for i, m in enumerate(calendar.month_abbr) if m}
        month = months.get(mon_abbr)
        if month:
            return yyyy, month
    except (ValueError, KeyError, IndexError):
        pass
    return None


def lookup_tag_id(tag_name: str) -> int | None:
    """Return MC tag id for a name, or None if not found."""
    resp = requests.get(
        mc_url(f"/lists/{MAILCHIMP_LIST_ID}/tag-search"),
        params={"name": tag_name},
        auth=mc_auth(),
        timeout=15,
    )
    resp.raise_for_status()
    tags = resp.json().get("tags", [])
    return tags[0]["id"] if tags else None


def get_emails_with_tag(tag_id: int) -> set[str]:
    """Paginate list members and return emails tagged with tag_id."""
    emails = set()
    offset = 0
    while True:
        r = requests.get(
            mc_url(f"/lists/{MAILCHIMP_LIST_ID}/members"),
            auth=mc_auth(),
            params={"count": 1000, "offset": offset, "fields": "members.email_address,members.tags"},
            timeout=30,
        )
        r.raise_for_status()
        members = r.json().get("members", [])
        for m in members:
            for tag in m.get("tags", []):
                if tag.get("id") == tag_id:
                    emails.add(m["email_address"].lower())
                    break
        if len(members) < 1000:
            break
        offset += 1000
    return emails


def get_habit_tag_members(year: int, month: int) -> set[str]:
    """Return emails of contacts tagged 'Yoga Habit - YYYY-MM'."""
    tag_name = f"Yoga Habit - {year:04d}-{month:02d}"
    tag_id = lookup_tag_id(tag_name)
    if tag_id is None:
        log.info("Tag '%s' not found — no attendees to process", tag_name)
        return set()
    return get_emails_with_tag(tag_id)


def get_lifestyle_members(lifestyle_tag_id: int) -> set[str]:
    """Return emails of all Lifestyle subscribers given tag ID."""
    return get_emails_with_tag(lifestyle_tag_id)


def apply_redeemed_tag(email: str, year: int, month: int) -> None:
    """Tag a contact with 'Yoga Habit - YYYY-MM - Redeemed'."""
    tag_name = f"Yoga Habit - {year:04d}-{month:02d} - Redeemed"
    # Find the contact hash (MD5 of lowercase email)
    member_hash = hashlib.md5(email.encode()).hexdigest()
    resp = requests.post(
        mc_url(f"/lists/{MAILCHIMP_LIST_ID}/members/{member_hash}/tags"),
        auth=mc_auth(),
        json={"tags": [{"name": tag_name, "status": "active"}]},
        timeout=15,
    )
    if resp.status_code not in (200, 204):
        log.error("Failed to tag %s: %s", email, resp.text[:80])


def main():
    coupons = get_open_habit_coupons()
    if not coupons:
        log.info("No open HABIT_ coupon windows - nothing to do")
        return

    # Lookup Lifestyle tag once, reuse for all coupons
    lifestyle_tag_id = lookup_tag_id(LIFESTYLE_TAG_NAME)
    if lifestyle_tag_id is None:
        log.warning("Lifestyle tag '%s' not found - cannot process conversions", LIFESTYLE_TAG_NAME)
        return

    lifestyle_emails = get_lifestyle_members(lifestyle_tag_id)
    log.info("Found %d Lifestyle subscribers", len(lifestyle_emails))

    for coupon in coupons:
        code = coupon["code"]
        parsed = parse_habit_tag_month(code)
        if not parsed:
            log.warning("Could not parse month from coupon code %s - skipping", code)
            continue

        year, month = parsed
        redeemed_tag = f"Yoga Habit - {year:04d}-{month:02d} - Redeemed"

        habit_emails = get_habit_tag_members(year, month)
        log.info("Coupon %s: %d Habit attendees, %d Lifestyle subscribers",
                 code, len(habit_emails), len(lifestyle_emails))

        converted = habit_emails & lifestyle_emails
        if not converted:
            log.info("No conversions found for %s", code)
            continue

        # Check which already have the Redeemed tag (idempotent)
        already_tagged = 0
        newly_tagged = 0
        for email in converted:
            member_hash = hashlib.md5(email.encode()).hexdigest()
            m_resp = requests.get(
                mc_url(f"/lists/{MAILCHIMP_LIST_ID}/members/{member_hash}"),
                auth=mc_auth(),
                params={"fields": "tags"},
                timeout=15,
            )
            if m_resp.ok:
                existing_tags = [t["name"] for t in m_resp.json().get("tags", [])]
                if redeemed_tag in existing_tags:
                    already_tagged += 1
                    continue
            apply_redeemed_tag(email, year, month)
            newly_tagged += 1
            log.info("Tagged %s as redeemed for %s", email, code)

        log.info("Coupon %s: %d newly tagged, %d already tagged", code, newly_tagged, already_tagged)


if __name__ == "__main__":
    main()
