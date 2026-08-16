#!/usr/bin/env python3
"""Sync exact Marvelous Yoga Habit registrations to SendGrid lists."""

from __future__ import annotations

import calendar
from datetime import date, datetime
import logging
import os
import sys
import time
from zoneinfo import ZoneInfo

from sendgrid_api import SendGridAPI
from sendgrid_campaigns import EXPECTED_ACCOUNT_EMAIL, SendGridRegistry
from sendgrid_list_sync import ensure_list, sync_exact_list
from sendgrid_mailings import EMAIL_SUBSCRIBED, habit_activity_name
from twy_paths import load_env, sendgrid_registry_path
from twy_platform.planning import PlanningClient, PlanningClientError


MOUNTAIN = ZoneInfo("America/Denver")
REGISTRATION_WINDOW_DAYS = 35

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("sync_sendgrid_habit_registrations")


# Keep a class in scope after it ends so late-marked attendance is captured.
ATTENDANCE_TRAIL_DAYS = 2


def upcoming_habit_events(today: date) -> tuple[list[tuple[date, int]], list[str]]:
    events = []
    failures = []
    for offset in range(2):
        year = today.year
        month = today.month + offset
        if month > 12:
            month -= 12
            year += 1
        last = calendar.monthrange(year, month)[1]
        try:
            client = PlanningClient.from_env()
            plans = client.list_plans(
                from_date=f"{year:04d}-{month:02d}-01",
                to_date=f"{year:04d}-{month:02d}-{last:02d}",
                timeout=10,
            )
            for plan in plans:
                if plan.get("class_type") != "Habit":
                    continue
                event_id = plan.get("marvelous_event_id")
                if not event_id:
                    continue
                event_date = date.fromisoformat(plan["date"])
                days_out = (event_date - today).days
                if -ATTENDANCE_TRAIL_DAYS <= days_out <= REGISTRATION_WINDOW_DAYS:
                    events.append((event_date, int(event_id)))
        except (PlanningClientError, ValueError, KeyError) as exc:
            failures.append(f"{year:04d}_{month:02d}: {exc}")
    return events, failures


def registrants_for_event(client, event_id: int) -> list[dict]:
    """Every registrant for an event, prospects and account holders alike.

    HeyMarvelous fills the two identity shapes exclusively. A prospect typed
    their details into the registration form, so the top-level student_* fields
    carry them and user_id is null. An existing student clicked register, so HM
    leaves those fields empty and the identity lives only on the nested student
    object. Measured on event 1012621, 2026-08-08: 21 prospects, 5 account
    holders, zero disagreements between the shapes. The top-level fields are
    really registrant fields, matching the UI. Both branches below are
    load-bearing, and collapsing either one silently drops a whole class of
    registrant.
    """
    event = client.get_event(event_id)
    by_email = {}
    for registration in event.get("registrations") or []:
        student = registration.get("student") or {}
        email = str(
            registration.get("student_email")
            or student.get("email")
            or ""
        ).strip().lower()
        if not email:
            continue
        first_name = str(
            registration.get("student_first_name")
            or student.get("first_name")
            or ""
        ).strip()
        last_name = str(
            registration.get("student_last_name")
            or student.get("last_name")
            or ""
        ).strip()
        contact = {"email": email, "attended": bool(registration.get("attended"))}
        if first_name:
            contact["first_name"] = first_name
        if last_name:
            contact["last_name"] = last_name
        by_email[email] = contact
    return [by_email[email] for email in sorted(by_email)]



def _wait_for_suppression_removal(
    *, api, suppression_group_id: int, emails: list[str]
) -> None:
    """SendGrid suppression removal is eventually consistent. Block until the
    address is genuinely gone, so the very next send can reach it."""
    attempts = 16
    for attempt in range(attempts):
        remaining = api.search_group_suppressions(suppression_group_id, emails)
        if not remaining:
            return
        if attempt < attempts - 1:
            time.sleep(1)
    raise RuntimeError("renewed consent suppression removal failed")


def sync_event_lists(
    *,
    api,
    registry,
    event_date: date,
    registrants: list[dict],
) -> dict:
    interested_name = habit_activity_name(
        event_date.year,
        event_date.month,
        "Interested",
    )
    registered_name = habit_activity_name(
        event_date.year,
        event_date.month,
        "Registered",
    )
    interested_list_id = ensure_list(api, registry, interested_name)
    registered_list_id = ensure_list(api, registry, registered_name)
    # Registering for a free Habit class puts a person in the mailing audience.
    # Email: Subscribed is an audience list, not a consent record: opt-out is
    # enforced by the ASM suppression group Email: Unsubscribed (id 35187),
    # which SendGrid applies at send time whatever list a contact sits on.
    # Additive, so leaving the Registered list never drops the subscription.
    subscribed_list_id = ensure_list(api, registry, EMAIL_SUBSCRIBED)

    # Registering is an opt-in that supersedes an earlier opt-out (JP,
    # 2026-08-08). SendGrid enforces the suppression group at send time, so a
    # returning registrant stays undeliverable until pulled out of it.
    renew = sorted(
        api.search_group_suppressions(
            registry.suppression_group_id,
            [contact["email"] for contact in registrants],
        )
    ) if registrants else []
    for email in renew:
        api.remove_group_suppression(registry.suppression_group_id, email)
    if renew:
        _wait_for_suppression_removal(
            api=api,
            suppression_group_id=registry.suppression_group_id,
            emails=renew,
        )

    attended_name = habit_activity_name(
        event_date.year,
        event_date.month,
        "Attended",
    )
    attended_list_id = ensure_list(api, registry, attended_name)
    attendees = [c for c in registrants if c.get("attended")]
    attended_result = sync_exact_list(
        api=api,
        destination_list_id=attended_list_id,
        desired_contacts=attendees,
        additive_list_ids=None,
    )

    result = sync_exact_list(
        api=api,
        destination_list_id=registered_list_id,
        desired_contacts=registrants,
        additive_list_ids=[interested_list_id, subscribed_list_id],
    )
    return {
        **result,
        "interested_list_id": interested_list_id,
        "registered_list_id": registered_list_id,
        "subscribed_list_id": subscribed_list_id,
        "attended_list_id": attended_list_id,
        "attended": attended_result["desired"],
        "renewed_consent": len(renew),
    }


def _marvelous_client():
    sys.path.insert(0, "/root/twy/marvy")
    sys.path.insert(0, "/root/twy/classes/scripts")
    from marvy.client import Client
    from sync import get_token
    return Client(auth_token=get_token())


def main() -> int:
    load_env()
    api_key = os.getenv("SENDGRID_API_KEY", "")
    if not api_key:
        raise SystemExit("SENDGRID_API_KEY is not configured")
    api = SendGridAPI(api_key)
    if api.user_email() != EXPECTED_ACCOUNT_EMAIL:
        raise SystemExit("unexpected SendGrid account")
    registry = SendGridRegistry.load(
        sendgrid_registry_path()
    )
    today = datetime.now(MOUNTAIN).date()
    events, failures = upcoming_habit_events(today)
    if not events:
        if failures:
            log.error("classes API failures: %s", "; ".join(failures))
            return 1
        log.info("no upcoming Yoga Habit events")
        return 0

    client = _marvelous_client()
    errors = []
    for event_date, event_id in events:
        try:
            registrants = registrants_for_event(client, event_id)
            result = sync_event_lists(
                api=api,
                registry=registry,
                event_date=event_date,
                registrants=registrants,
            )
            log.info(
                "%s: registered=%d previous=%d removed=%d",
                event_date,
                result["desired"],
                result["previous"],
                result["removed"],
            )
        except Exception as exc:
            errors.append(f"{event_id}: {exc}")
    errors.extend(failures)
    if errors:
        log.error("registration sync failures: %s", "; ".join(errors))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
