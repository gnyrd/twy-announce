#!/usr/bin/env python3
"""Sync exact Marvelous Yoga Habit registrations to SendGrid lists."""

from __future__ import annotations

import calendar
from datetime import date, datetime
import logging
import os
from pathlib import Path
import sys
from zoneinfo import ZoneInfo

import requests

from sendgrid_api import SendGridAPI
from sendgrid_campaigns import EXPECTED_ACCOUNT_EMAIL, SendGridRegistry
from sendgrid_list_sync import ensure_list, sync_exact_list
from sendgrid_mailings import habit_activity_name
from twy_paths import data_root, load_env


MOUNTAIN = ZoneInfo("America/Denver")
CLASSES_API = "http://localhost:5003"
REGISTRATION_WINDOW_DAYS = 35

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("sync_sendgrid_habit_registrations")


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
            response = requests.get(
                f"{CLASSES_API}/api/plans",
                params={
                    "from": f"{year:04d}-{month:02d}-01",
                    "to": f"{year:04d}-{month:02d}-{last:02d}",
                },
                timeout=10,
            )
            if not response.ok:
                failures.append(
                    f"{year:04d}_{month:02d}: HTTP {response.status_code}"
                )
                continue
            for plan in response.json():
                if plan.get("class_type") != "Habit":
                    continue
                event_id = plan.get("marvelous_event_id")
                if not event_id:
                    continue
                event_date = date.fromisoformat(plan["date"])
                days_out = (event_date - today).days
                if 0 <= days_out <= REGISTRATION_WINDOW_DAYS:
                    events.append((event_date, int(event_id)))
        except (requests.RequestException, ValueError, KeyError) as exc:
            failures.append(f"{year:04d}_{month:02d}: {exc}")
    return events, failures


def registrants_for_event(client, event_id: int) -> list[dict]:
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
        contact = {"email": email}
        if first_name:
            contact["first_name"] = first_name
        if last_name:
            contact["last_name"] = last_name
        by_email[email] = contact
    return [by_email[email] for email in sorted(by_email)]


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
    result = sync_exact_list(
        api=api,
        destination_list_id=registered_list_id,
        desired_contacts=registrants,
        additive_list_ids=[interested_list_id],
    )
    return {
        **result,
        "interested_list_id": interested_list_id,
        "registered_list_id": registered_list_id,
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
        data_root() / "sendgrid" / "production_objects.json"
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
