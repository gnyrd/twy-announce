#!/usr/bin/env python3
"""Put everyone who claims a free class recording on the newsletter audience.

Claiming a FREE CLASS product on HeyMarvelous forces account creation, which
is the point: it turns an anonymous viewer into a customer record with a name
and an email. Per JP (2026-08-13), that email belongs on the mailing audience,
the same way registering for a free Habit class already puts a person there
(sync_sendgrid_habit_registrations, JP 2026-08-08).

Where the claims live, and where they do NOT:

- `marvy.db` `purchases` is the source. A free claim writes a purchase row with
  amount_paid zero, carrying customer_id, email and a created timestamp.
- `product_students` looks like the obvious table and is a fossil. Only
  marvy/db.py defines its schema and nothing anywhere writes to it, so its
  coverage is whatever an older sync left behind: 19 rows for one product, 0
  for another with 12 customers, 0 for the August recording with 4. Do not
  read it.
- No HeyMarvelous report can answer this. All 36 report `has_product_id`
  false. Sales by product (4) gives units and revenue with no identities;
  Customer Contact Details (17) and Active Customers (95) give identities with
  no product attribution.

Latency is a day by design (JP): `purchases` refreshes on the marvy_sync cron
at 07:05, so this runs after it.

ADDITIVE ONLY, and that is load-bearing. `Email: Subscribed` is the whole
newsletter audience. sync_exact_list would treat everyone not claiming a free
class as stale and remove them, so this upserts and never removes.

Anyone currently in the unsubscribe suppression group is skipped outright.
Membership of `Email: Subscribed` is an audience list rather than a consent
record, so adding a suppressed contact would send them nothing either way, but
it would still record somebody as part of the audience after they asked not to
be. On the first live run 8 of the 13 people this would have added had
unsubscribed, which is most of them.

The Habit registration sync goes further and clears suppressions, treating
registration as an opt-in that supersedes an earlier opt-out (JP 2026-08-08).
That call was made for registration and is deliberately NOT extended here.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import sys

from sendgrid_api import SendGridAPI
from sendgrid_campaigns import EXPECTED_ACCOUNT_EMAIL, SendGridRegistry
from sendgrid_contact_source import SOURCE_FREE_CLASS_CLAIM, stamp_new_contacts
from sendgrid_list_sync import ensure_list
from sendgrid_mailings import EMAIL_SUBSCRIBED
from twy_paths import load_env, marvy_db_path, sendgrid_registry_path

FREE_CLASS_PREFIX = "FREE CLASS:"

logging.basicConfig(format="%(asctime)s %(levelname)s %(message)s", level=logging.INFO)
log = logging.getLogger("sync_sendgrid_free_class_claims")


def free_class_claimants(db_path=None) -> list[dict]:
    """Everyone holding a purchase of a FREE CLASS product, deduplicated by email.

    Joins customers for the email, because the purchase row's own
    customer_email can be absent on older rows. Newest claim wins on a
    duplicate so the name is the most recent one the person gave.
    """
    connection = sqlite3.connect(str(db_path or marvy_db_path()))
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            """
            SELECT COALESCE(cu.email, pu.customer_email) AS email,
                   cu.first_name AS first_name,
                   cu.last_name  AS last_name,
                   pu.product_name AS product_name,
                   pu.created AS created
            FROM purchases pu
            LEFT JOIN customers cu ON cu.id = pu.customer_id
            WHERE pu.product_name LIKE ? || '%'
            ORDER BY pu.created ASC
            """,
            (FREE_CLASS_PREFIX,),
        ).fetchall()
    finally:
        connection.close()

    by_email: dict[str, dict] = {}
    skipped = 0
    for row in rows:
        email = str(row["email"] or "").strip().lower()
        if not email or "@" not in email:
            skipped += 1
            continue
        contact = {"email": email}
        for field in ("first_name", "last_name"):
            value = str(row[field] or "").strip()
            if value:
                contact[field] = value
        by_email[email] = contact
    if skipped:
        log.warning("%d free class purchase rows had no usable email", skipped)
    return [by_email[email] for email in sorted(by_email)]


def missing_from_list(api, list_id: str, claimants: list[dict]) -> list[dict]:
    """Claimants who are not already on the list.

    Checked rather than blindly upserted so a quiet day costs one read and
    starts no contact job at all.
    """
    present = {
        str(contact.get("email") or "").strip().lower()
        for contact in api.list_contacts(list_id)
    }
    return [c for c in claimants if c["email"] not in present]


def main() -> int:
    load_env()
    api_key = os.getenv("SENDGRID_API_KEY", "")
    if not api_key:
        raise SystemExit("SENDGRID_API_KEY is not configured")
    api = SendGridAPI(api_key)
    if api.user_email() != EXPECTED_ACCOUNT_EMAIL:
        raise SystemExit("unexpected SendGrid account")
    registry = SendGridRegistry.load(sendgrid_registry_path())

    claimants = free_class_claimants()
    if not claimants:
        log.info("no free class claims found")
        return 0

    subscribed_list_id = ensure_list(api, registry, EMAIL_SUBSCRIBED)
    missing = missing_from_list(api, subscribed_list_id, claimants)
    if not missing:
        log.info("%d free class claimants, all already subscribed", len(claimants))
        return 0

    suppressed = set(
        api.search_group_suppressions(
            registry.suppression_group_id, [c["email"] for c in missing]
        )
    )
    eligible = [c for c in missing if c["email"] not in suppressed]
    if not eligible:
        log.info(
            "%d free class claimants, %d missing, all %d of them unsubscribed; nothing added",
            len(claimants), len(missing), len(suppressed),
        )
        return 0

    job_id = api.upsert_contacts(
        [subscribed_list_id],
        stamp_new_contacts(api, eligible, source=SOURCE_FREE_CLASS_CLAIM),
    )
    api.wait_contact_job(job_id, timeout_s=300)
    log.info(
        "%d free class claimants, %d added to %s, %d skipped as unsubscribed",
        len(claimants), len(eligible), EMAIL_SUBSCRIBED, len(suppressed),
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
