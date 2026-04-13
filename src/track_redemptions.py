#!/usr/bin/env python3
"""Daily cron: tag Habit attendees who redeemed their coupon.

For each active HABIT_* coupon:
  1. Compute target amount = product.price - coupon.discount_amount (from marvy.db)
  2. Find local purchases in redeem window where amount_paid == target_amount
  3. Cross-ref purchase emails with MC 'Yoga Habit - YYYY-MM' attendee tag
  4. For matches: tag 'Yoga Habit - YYYY-MM - Redeemed' (idempotent)

Reads from marvy.db only - no live HM API calls.
"""
import calendar
import hashlib
import json
import logging
import os
import sqlite3
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "paths"))
from twy_paths import load_env
load_env()

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

MAILCHIMP_API_KEY = os.getenv("MAILCHIMP_API_KEY", "")
MAILCHIMP_LIST_ID = os.getenv("MAILCHIMP_AUDIENCE_ID", "")
MC_SERVER         = os.getenv("MAILCHIMP_SERVER_PREFIX", "")

MARVY_DB = "/root/twy/data/marvy.db"


def mc_url(path: str) -> str:
    return f"https://{MC_SERVER}.api.mailchimp.com/3.0{path}"


def mc_auth() -> tuple[str, str]:
    return ("anystring", MAILCHIMP_API_KEY)


def get_open_habit_coupons(db: sqlite3.Connection) -> list[sqlite3.Row]:
    """Return HABIT_* coupons whose window is still open."""
    today = date.today().isoformat()
    return db.execute("""
        SELECT code, discount_amount, products_json, redeem_start, redeem_end
        FROM coupons
        WHERE code LIKE 'HABIT_%' AND redeem_end >= ?
        ORDER BY redeem_start
    """, (today,)).fetchall()


def parse_habit_month(coupon_code: str) -> tuple[int, int] | None:
    """Parse HABIT_MAY2026 -> (2026, 5)."""
    try:
        suffix = coupon_code.replace("HABIT_", "")
        mon_abbr = suffix[:3]
        yyyy = int(suffix[3:])
        months = {m.upper(): i for i, m in enumerate(calendar.month_abbr) if m}
        month = months.get(mon_abbr)
        if month:
            return yyyy, month
    except (ValueError, KeyError, IndexError):
        pass
    return None


def find_redeemer_emails(db: sqlite3.Connection, coupon: sqlite3.Row) -> set[str]:
    """Find purchase emails where amount_paid matches the discounted price."""
    products = json.loads(coupon["products_json"])
    discount = float(coupon["discount_amount"])

    targets: list[tuple[int, float]] = []
    for p in products:
        pid = p["id"]
        row = db.execute("SELECT price FROM products WHERE id=?", (pid,)).fetchone()
        if row is None or row["price"] is None:
            log.warning("coupon %s: no cached price for product %s - skipping",
                        coupon["code"], pid)
            continue
        target = round(float(row["price"]) - discount, 2)
        targets.append((pid, target))

    if not targets:
        return set()

    start = coupon["redeem_start"]
    end_plus = coupon["redeem_end"] + "T23:59:59Z"

    emails: set[str] = set()
    for pid, target in targets:
        rows = db.execute("""
            SELECT customer_email FROM purchases
            WHERE product_id = ? AND amount_paid = ?
              AND created >= ? AND created <= ?
              AND is_canceled = 0
        """, (pid, target, start, end_plus)).fetchall()
        for r in rows:
            if r["customer_email"]:
                emails.add(r["customer_email"].lower())
    return emails


def get_habit_attendees(year: int, month: int) -> set[str]:
    """Return email set of contacts tagged 'Yoga Habit - YYYY-MM' in MC."""
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
        log.info("MC tag '%s' not found - no attendees for %s-%02d", tag_name, year, month)
        return set()
    tag_id = tags[0]["id"]

    emails: set[str] = set()
    offset = 0
    while True:
        r = requests.get(
            mc_url(f"/lists/{MAILCHIMP_LIST_ID}/members"),
            auth=mc_auth(),
            params={"count": 1000, "offset": offset,
                    "fields": "members.email_address,members.tags"},
            timeout=30,
        )
        r.raise_for_status()
        members = r.json().get("members", [])
        for m in members:
            for t in m.get("tags", []):
                if t.get("id") == tag_id:
                    emails.add(m["email_address"].lower())
                    break
        if len(members) < 1000:
            break
        offset += 1000
    return emails


def has_redeemed_tag(email: str, redeemed_tag: str) -> bool:
    h = hashlib.md5(email.lower().encode()).hexdigest()
    r = requests.get(
        mc_url(f"/lists/{MAILCHIMP_LIST_ID}/members/{h}"),
        auth=mc_auth(),
        params={"fields": "tags"},
        timeout=15,
    )
    if not r.ok:
        return False
    tags = [t["name"] for t in r.json().get("tags", [])]
    return redeemed_tag in tags


def apply_redeemed_tag(email: str, redeemed_tag: str) -> None:
    h = hashlib.md5(email.lower().encode()).hexdigest()
    r = requests.post(
        mc_url(f"/lists/{MAILCHIMP_LIST_ID}/members/{h}/tags"),
        auth=mc_auth(),
        json={"tags": [{"name": redeemed_tag, "status": "active"}]},
        timeout=15,
    )
    if r.status_code not in (200, 204):
        log.error("failed to tag %s with %s: %s", email, redeemed_tag, r.text[:120])


def main() -> None:
    db = sqlite3.connect(MARVY_DB)
    db.row_factory = sqlite3.Row

    coupons = get_open_habit_coupons(db)
    if not coupons:
        log.info("No open HABIT_ coupons - nothing to do")
        return

    for coupon in coupons:
        code = coupon["code"]
        parsed = parse_habit_month(code)
        if not parsed:
            log.warning("Cannot parse month from %s - skipping", code)
            continue
        year, month = parsed
        redeemed_tag = f"Yoga Habit - {year:04d}-{month:02d} - Redeemed"

        redeemers = find_redeemer_emails(db, coupon)
        if not redeemers:
            log.info("%s: 0 candidate redemptions in window", code)
            continue

        attendees = get_habit_attendees(year, month)
        confirmed = redeemers & attendees
        log.info("%s: %d candidate redemptions, %d attendees, %d confirmed",
                 code, len(redeemers), len(attendees), len(confirmed))

        if not confirmed:
            continue

        newly = 0
        already = 0
        for email in confirmed:
            if has_redeemed_tag(email, redeemed_tag):
                already += 1
                continue
            apply_redeemed_tag(email, redeemed_tag)
            log.info("Tagged %s as redeemed for %s", email, code)
            newly += 1
        log.info("%s: %d newly tagged, %d already tagged", code, newly, already)

    db.close()


if __name__ == "__main__":
    main()
