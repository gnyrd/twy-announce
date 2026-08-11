#!/usr/bin/env python3
"""Send the journey email each enrolled person is owed, once, or send nothing.

Enrollment decided who is in a sequence. This decides what leaves the building.

Three rules shape all of it. Nobody receives the same email twice, which is why a
claim is written before the provider call and blocks a retry it cannot vouch for.
Nobody receives an email the copy did not fill in, which is why an unresolved
merge token raises instead of shipping. And nobody who has bounced, been cleaned,
or unsubscribed receives anything at all, re-checked at send time rather than
trusted from enrollment, because weeks can pass between the two.

Timing is plain elapsed time with no sending window. Whoever bought at 3:30am is
somebody who was awake at 3:30am (JP 2026-08-11). Do not add a window without
asking him again.

NOT YET DECIDED, so deliberately absent: what happens to somebody mid-sequence
when they cancel their membership. Turning a journey off pauses everybody in it
without ending anybody, which is reversible; cancellation needs JP's answer
before it can be anything.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sqlite3
import sys

import journey_enrollment
from journey_personalization import personalize_email
from newsletter_rendering import render_newsletter

SENDER_NAME = "Tiffany Wood Yoga"

# Why a sequence stopped. Every one of these is a fact about the person, not a
# judgement, so the reporting page can show them to Tiff as they are.
COMPLETED = "completed"
BOUNCED = "bounced"
CLEANED = "cleaned"
UNSUBSCRIBED = "unsubscribed"

# Why a tick passed somebody over without changing anything. A skip is reversible
# and a finish is not, so anything uncertain has to be a skip.
JOURNEY_OFF = "journey_off"
ALREADY_CLAIMED = "already_claimed"


@dataclass(frozen=True)
class Verdict:
    action: str
    reason: str = ""
    email: dict | None = None
    email_index: int = 0
    next_index: int | None = None
    next_due_at: str | None = None


def decide(
    enrollment: dict,
    journey: dict | None,
    *,
    ineligible: str = "",
) -> Verdict:
    """What this tick owes one enrolled person. Pure, so it can be tested flat.

    Order matters. A finished sequence finishes even for somebody who has since
    unsubscribed, because there is nothing left to suppress. An off journey
    pauses before eligibility is consulted, because a paused person should not be
    quietly terminated by a bounce nobody was going to mail anyway.
    """
    if journey is None or not journey.get("active"):
        return Verdict(action="skip", reason=JOURNEY_OFF)

    emails = journey.get("emails") or []
    index = int(enrollment.get("next_index") or 0)
    if index >= len(emails):
        return Verdict(action="finish", reason=COMPLETED)

    if ineligible:
        return Verdict(action="finish", reason=ineligible)

    return Verdict(
        action="send",
        email=emails[index],
        email_index=index,
    )


def next_step(journey: dict, enrollment: dict) -> tuple[int | None, str | None]:
    """Where somebody stands after the email that just went out.

    None for both means the sequence is done. Offsets are cumulative days from
    enrollment, so this recomputes from enrolled_at rather than from now: a tick
    that runs late must not push the rest of the sequence later with it.
    """
    from twy_platform.journeys import due_offsets

    offsets = due_offsets(journey)
    index = int(enrollment.get("next_index") or 0) + 1
    if index >= len(offsets):
        return None, None
    enrolled_at = journey_enrollment.parse_timestamp(enrollment["enrolled_at"])
    due = enrolled_at + timedelta(days=offsets[index])
    return index, due.isoformat()


def first_name_for(marvy_connection, enrollment: dict) -> str:
    """The buyer's first name, by customer id, falling back to their address."""
    if marvy_connection is None:
        return ""
    row = marvy_connection.execute(
        "SELECT first_name FROM customers WHERE CAST(id AS TEXT) = ?",
        (str(enrollment.get("customer_id") or ""),),
    ).fetchone()
    if row is None:
        row = marvy_connection.execute(
            "SELECT first_name FROM customers WHERE lower(email) = ?",
            (str(enrollment.get("email") or "").strip().lower(),),
        ).fetchone()
    return (row["first_name"] if row and row["first_name"] else "") or ""


def build_payload(email: dict, *, recipient: str, registry) -> dict:
    """One /mail/send body, the same shape the editor's Send test already proves.

    Subscription tracking stays off and the footer stays disabled because the
    delivery template carries its own, exactly as every other TWY send does.
    """
    subject = str(email.get("subject") or "").strip()
    body = str(email.get("body") or "").strip()
    preheader = str(email.get("preheader") or "").strip()
    if not subject or not body:
        raise ValueError("a journey email needs a subject and a body")
    rendered = render_newsletter(body, use_template=True, preheader=preheader)
    return {
        "personalizations": [{"to": [{"email": recipient}]}],
        "from": {"email": registry.sender_email, "name": SENDER_NAME},
        "reply_to": {"email": registry.sender_email, "name": SENDER_NAME},
        "subject": subject,
        "content": [
            {"type": "text/plain", "value": rendered.plain_text},
            {"type": "text/html", "value": rendered.html},
        ],
        "asm": {
            "group_id": registry.suppression_group_id,
            "groups_to_display": [registry.suppression_group_id],
        },
        "mail_settings": {"footer": {"enable": False}},
        "tracking_settings": {"subscription_tracking": {"enable": False}},
    }


def ineligibility(email: str, *, api, registry, cleaned: set) -> str:
    """Why this address cannot be mailed, or an empty string when it can.

    Checked at send time, not carried from enrollment. Somebody can bounce or
    unsubscribe in the days between joining a sequence and reaching its third
    email, and the sequence has to notice.
    """
    address = str(email).strip().lower()
    if address in cleaned:
        return CLEANED
    if api.get_bounce(address) is not None:
        return BOUNCED
    if api.search_group_suppressions(registry.suppression_group_id, [address]):
        return UNSUBSCRIBED
    return ""


def run(
    *,
    connection,
    journeys_by_id: dict,
    api,
    registry,
    cleaned: set,
    marvy_connection=None,
    now: datetime,
    dry_run: bool,
    limit: int | None = None,
) -> dict:
    """Work the due queue. Returns what happened, in counts and reasons."""
    due = journey_enrollment.due_enrollments(connection, now=now)
    if limit is not None:
        due = due[:limit]

    counts = {
        "due": len(due),
        "sent": 0,
        "finished": 0,
        "skipped": 0,
        "failed": 0,
        "dry_run": dry_run,
    }
    reasons: dict = {}
    failures: list = []

    for enrollment in due:
        journey = journeys_by_id.get(enrollment["journey_id"])
        verdict = decide(enrollment, journey)

        if verdict.action == "send":
            reason = ineligibility(
                enrollment["email"], api=api, registry=registry, cleaned=cleaned
            )
            if reason:
                verdict = Verdict(action="finish", reason=reason)

        if verdict.action == "skip":
            counts["skipped"] += 1
            reasons[verdict.reason] = reasons.get(verdict.reason, 0) + 1
            continue

        if verdict.action == "finish":
            if not dry_run:
                journey_enrollment.finish(
                    connection,
                    journey_id=enrollment["journey_id"],
                    email=enrollment["email"],
                    reason=verdict.reason,
                )
            counts["finished"] += 1
            reasons[verdict.reason] = reasons.get(verdict.reason, 0) + 1
            continue

        filled = personalize_email(
            verdict.email,
            first_name=first_name_for(marvy_connection, enrollment),
        )

        if dry_run:
            counts["sent"] += 1
            continue

        claimed = journey_enrollment.claim_send(
            connection,
            journey_id=enrollment["journey_id"],
            email=enrollment["email"],
            email_index=verdict.email_index,
            subject=filled["subject"],
        )
        if not claimed:
            # An earlier run already took this one and could not confirm it. A
            # missing email can be fixed by hand; a duplicate cannot.
            counts["skipped"] += 1
            reasons[ALREADY_CLAIMED] = reasons.get(ALREADY_CLAIMED, 0) + 1
            continue

        try:
            api.send_mail(
                build_payload(
                    filled, recipient=enrollment["email"], registry=registry
                )
            )
        except Exception as exc:
            # The claim stays unresolved on purpose: nobody knows whether this
            # left SendGrid, so nobody may send it again automatically.
            counts["failed"] += 1
            failures.append(f"{enrollment['journey_id']}:{enrollment['email']}: {exc}")
            continue

        journey_enrollment.mark_sent(
            connection,
            journey_id=enrollment["journey_id"],
            email=enrollment["email"],
            email_index=verdict.email_index,
        )
        index, due_at = next_step(journey, enrollment)
        if index is None:
            journey_enrollment.finish(
                connection,
                journey_id=enrollment["journey_id"],
                email=enrollment["email"],
                reason=COMPLETED,
            )
        else:
            journey_enrollment.advance(
                connection,
                journey_id=enrollment["journey_id"],
                email=enrollment["email"],
                next_index=index,
                next_due_at=due_at,
            )
        counts["sent"] += 1

    if reasons:
        counts["reasons"] = dict(sorted(reasons.items()))
    if failures:
        counts["failures"] = failures
        raise RuntimeError(
            f"{len(failures)} journey email(s) could not be confirmed: "
            + "; ".join(failures)
        )
    return counts


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="report what would send and touch nothing",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="process at most this many due enrollments",
    )
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    from sendgrid_api import SendGridAPI
    from sendgrid_campaigns import EXPECTED_ACCOUNT_EMAIL, SendGridRegistry
    from sync_sendgrid_products import load_cleaned_emails
    from twy_paths import (
        journey_enrollments_db_path,
        journeys_dir,
        load_env,
        marvy_db_path,
        sendgrid_cleaned_denylist_path,
        sendgrid_registry_path,
    )
    from twy_platform.journeys import list_journeys

    load_env()
    api_key = os.getenv("SENDGRID_API_KEY", "")
    if not api_key:
        raise SystemExit("SENDGRID_API_KEY is not configured")
    api = SendGridAPI(api_key)
    if api.user_email() != EXPECTED_ACCOUNT_EMAIL:
        raise SystemExit("unexpected SendGrid account")

    journeys_by_id = {
        journey["journey_id"]: journey
        for journey in list_journeys(journeys_dir())
        if journey.get("active")
    }
    connection = journey_enrollment.connect(journey_enrollments_db_path())
    marvy = sqlite3.connect(f"file:{marvy_db_path()}?mode=ro", uri=True)
    marvy.row_factory = sqlite3.Row
    try:
        result = run(
            connection=connection,
            journeys_by_id=journeys_by_id,
            api=api,
            registry=SendGridRegistry.load(sendgrid_registry_path()),
            cleaned=load_cleaned_emails(sendgrid_cleaned_denylist_path()),
            marvy_connection=marvy,
            now=datetime.now(timezone.utc),
            dry_run=args.dry_run,
            limit=args.limit,
        )
    finally:
        marvy.close()
        connection.close()
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
