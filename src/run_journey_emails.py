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
import resend_events
from newsletter_rendering import render_newsletter
from twy_platform.journeys import (
    journey_campaign_id,
    journey_display_label,
)

SENDER_NAME = "Tiffany Wood Yoga"

# The custom argument SendGrid echoes back on every event for a journey
# message. The event store reads it as the campaign id when no singlesend_id
# is present, which is always the case for /mail/send.
TWY_CAMPAIGN_ARG = "twy_campaign_id"

# Why a sequence stopped. Every one of these is a fact about the person, not a
# judgement, so the reporting page can show them to Tiff as they are.
COMPLETED = "completed"
BOUNCED = "bounced"
BLOCKED = "blocked"
INVALID = "invalid"
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


def build_payload(
    email: dict,
    *,
    recipient: str,
    registry,
    campaign_id: str = "",
) -> dict:
    """One /mail/send body, the same shape the editor's Send test already proves.

    Subscription tracking stays off and the footer stays disabled because the
    delivery template carries its own, exactly as every other TWY send does.

    campaign_id rides along as a custom argument. SendGrid echoes custom args
    back on every event for this message, and that echo is the only way an
    open or a bounce weeks later can be traced to a journey and an email
    number: /mail/send has no campaign of its own. A send made without it is
    permanently unattributable, which is why it is stamped here at the one
    place the payload is built rather than by each caller.
    """
    subject = str(email.get("subject") or "").strip()
    body = str(email.get("body") or "").strip()
    preheader = str(email.get("preheader") or "").strip()
    if not subject or not body:
        raise ValueError("a journey email needs a subject and a body")
    rendered = render_newsletter(
        body,
        use_template=True,
        preheader=preheader,
        unsubscribe_footer=False,
    )
    payload = {
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
    if campaign_id:
        payload["custom_args"] = {TWY_CAMPAIGN_ARG: campaign_id}
    return payload


def ineligibility(
    email: str,
    *,
    api,
    registry,
    legacy_denylist: set,
    resend_suppressions: dict | None = None,
) -> str:
    """Why this address cannot be mailed, or an empty string when it can.

    Checked at send time, not carried from enrollment. Somebody can bounce or
    unsubscribe in the days between joining a sequence and reaching its third
    email, and the sequence has to notice.

    The four answers are four different things and Tiff sees them as four.
    Unsubscribed is a choice the person made. Bounced is the receiving server
    saying no such recipient. Blocked is that server refusing for reputation,
    greylisting or content, which is often transient and frequently says
    nothing about the address itself. Invalid is malformed or nonexistent.

    Consent is checked before deliverability, so somebody who both opted out
    and bounced reads as opted out. That is the truthful answer and also the
    safe one, because an unsubscribe is the reason you never retry.

    legacy_denylist is the frozen MailChimp import. Those addresses were
    cleaned by MailChimp before the migration, which meant undeliverable, so
    they answer invalid. The provider word never reaches anybody.

    resend_suppressions closes the gap the 2026-08-14 cutover opened. The
    message now leaves through Resend while this ledger stays on SendGrid,
    so a hard bounce or a spam complaint on a drip happens somewhere
    SendGrid cannot see. Without it a member whose address bounced on email
    1 was sent email 2 regardless. Only permanent bounces and complaints
    appear in it; a transient bounce is a full mailbox and must be retried.
    """
    address = str(email).strip().lower()
    from_resend = (resend_suppressions or {}).get(address, "")
    if api.search_group_suppressions(registry.suppression_group_id, [address]):
        return UNSUBSCRIBED
    if api.get_global_unsubscribe(address) is not None:
        return UNSUBSCRIBED
    if from_resend == resend_events.COMPLAINED:
        return UNSUBSCRIBED
    if api.get_bounce(address) is not None:
        return BOUNCED
    if from_resend == resend_events.BOUNCED:
        return BOUNCED
    if api.get_block(address) is not None:
        return BLOCKED
    if api.get_invalid_email(address) is not None:
        return INVALID
    if address in legacy_denylist:
        return INVALID
    return ""


def sent_announcement(
    journey: dict,
    *,
    email_index: int,
    recipient: str,
    linker=None,
) -> str:
    """The Slack line for one journey email going out.

    NOT WIRED TO PRODUCTION. Per JP 2026-08-16 journey drips do not post to
    the member activity channel, so main() passes no announce and this runs
    only from tests. The member activity feed (joins, renewals, cancels) is
    a different module and is untouched. Re-enabling is one argument at the
    run() call site.

    Reads as a sentence: "Yoga Lifestyle: 2024_05 sent 2 of 8 to Jane". The
    position is composed here from the index and the journey, never stored,
    so there is one place it can be wrong. Jane links to her HeyMarvelous
    customer record through the same linker the member activity feed uses.
    """
    total = len(journey.get("emails") or [])
    label = journey_display_label(journey)
    who = str(recipient)
    if linker is not None:
        who = linker(recipient)
    return f"{label} sent {email_index + 1} of {total} to {who}"


def run(
    *,
    connection,
    journeys_by_id: dict,
    api,
    sender,
    registry,
    legacy_denylist: set,
    resend_suppressions: dict | None = None,
    marvy_connection=None,
    now: datetime,
    dry_run: bool,
    limit: int | None = None,
    linker=None,
    announce=None,
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
                enrollment["email"],
                api=api,
                registry=registry,
                legacy_denylist=legacy_denylist,
                resend_suppressions=resend_suppressions,
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
            sender.send_mail(
                build_payload(
                    filled,
                    recipient=enrollment["email"],
                    registry=registry,
                    campaign_id=journey_campaign_id(
                        enrollment["journey_id"], verdict.email_index
                    ),
                )
            )
        except Exception as exc:
            # The claim stays unresolved on purpose: nobody knows whether this
            # left the provider, so nobody may send it again automatically.
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
        if announce is not None:
            # A failed post must not undo a real send, so it is counted and
            # reported rather than raised. The email did go out.
            try:
                announce(
                    sent_announcement(
                        journey,
                        email_index=verdict.email_index,
                        recipient=enrollment["email"],
                        linker=linker,
                    )
                )
            except Exception as exc:
                counts["unannounced"] = counts.get("unannounced", 0) + 1
                failures.append(
                    f"slack post failed for {enrollment['email']}: {exc}"
                )

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
    from resend_api import ResendAPI
    from sendgrid_api import SendGridAPI
    from sendgrid_campaigns import EXPECTED_ACCOUNT_EMAIL, SendGridRegistry
    from sync_sendgrid_products import load_cleaned_emails
    from twy_paths import (
        journey_enrollments_db_path,
        journeys_dir,
        load_env,
        marvy_db_path,
        resend_event_log_path,
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

    # Two providers, on purpose, and the split is not arbitrary.
    #
    # SendGrid stays the consent and deliverability ledger: `ineligibility`
    # reads its suppression group, global unsubscribes, bounces, blocks and
    # invalids before every single send, and that history lives nowhere else.
    #
    # The send TRANSPORT is chosen by TWY_JOURNEY_PROVIDER (default resend).
    # Resend carries the message because this SendGrid account has no Email
    # API and `/mail/send` answers 401 Maximum credits exceeded. sendgrid is
    # the documented revert lever for if that Email plan is ever bought.
    journey_provider = os.getenv("TWY_JOURNEY_PROVIDER", "resend").strip().lower()
    if journey_provider == "resend":
        resend_key = os.getenv("RESEND_API_KEY", "")
        if not resend_key:
            raise SystemExit("RESEND_API_KEY is not configured")
        resend_from = os.getenv("RESEND_FROM_EMAIL", "")
        if not resend_from:
            raise SystemExit("RESEND_FROM_EMAIL is not configured")
        resend_name = os.getenv("RESEND_FROM_NAME", "Tiffany Wood Yoga")
        sender = ResendAPI(
            resend_key,
            from_address=f"{resend_name} <{resend_from}>",
        )
    elif journey_provider == "sendgrid":
        # Revert lever: reuse the SendGrid client as the transport. Its
        # /mail/send answers 401 until an Email API plan exists.
        sender = api
    else:
        raise SystemExit(
            f"unknown TWY_JOURNEY_PROVIDER: {journey_provider!r} "
            "(expected resend or sendgrid)"
        )

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
            sender=sender,
            registry=SendGridRegistry.load(sendgrid_registry_path()),
            legacy_denylist=load_cleaned_emails(sendgrid_cleaned_denylist_path()),
            resend_suppressions=resend_events.load_suppressions(
                resend_event_log_path()
            ),
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
