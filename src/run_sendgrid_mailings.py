#!/usr/bin/env python3
"""Schedule and verify TWY SendGrid mailings from persisted provider IDs."""

from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sys
from zoneinfo import ZoneInfo

import requests

from sendgrid_api import SendGridAPI
from sendgrid_campaigns import (
    EXPECTED_ACCOUNT_EMAIL,
    SendGridCampaigns,
    SendGridRegistry,
)
from sendgrid_newsletter_workflow import (
    PURPOSE_SECTIONS,
    apply_provider_report,
    ensure_recording_draft,
    lock_due_sections,
    mark_provider_error,
    provision_drafts,
    read_local_sections,
)
from sendgrid_scheduler import schedule_month
from slack import post_slack
from twy_paths import load_env, newsletters_dir, sendgrid_registry_path


MOUNTAIN = ZoneInfo("America/Denver")
CLASSES_API = "http://localhost:5003"


def habit_class_date(year: int, month: int) -> date | None:
    first = date(year, month, 1)
    if month == 12:
        following = date(year + 1, 1, 1)
    else:
        following = date(year, month + 1, 1)
    response = requests.get(
        f"{CLASSES_API}/api/plans",
        params={
            "from": first.isoformat(),
            "to": (following - timedelta(days=1)).isoformat(),
        },
        timeout=10,
    )
    response.raise_for_status()
    matches = [
        plan for plan in response.json()
        if plan.get("class_type") == "Habit"
    ]
    if len(matches) > 1:
        raise ValueError(
            f"expected at most one Yoga Habit class for "
            f"{year:04d}_{month:02d}, found {len(matches)}"
        )
    return date.fromisoformat(matches[0]["date"]) if matches else None


def _periods(now: datetime) -> list[tuple[int, int]]:
    current = (now.year, now.month)
    following = (
        (now.year + 1, 1)
        if now.month == 12
        else (now.year, now.month + 1)
    )
    return [current, following]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--month",
        help="one explicit period in YYYY_MM format",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    load_env()
    slack_status_channel = os.getenv(
        "SLACK_STATUS_CHANNEL",
        "#status-newsletters",
    )
    slack_warning_channel = os.getenv(
        "SLACK_SYSTEM_WARNINGS_CHANNEL",
        "#system-warnings",
    )
    api_key = os.getenv("SENDGRID_API_KEY", "")
    if not api_key:
        raise SystemExit("SENDGRID_API_KEY is not configured")
    api = SendGridAPI(api_key)
    if api.user_email() != EXPECTED_ACCOUNT_EMAIL:
        raise SystemExit("unexpected SendGrid account")

    registry = SendGridRegistry.load(
        sendgrid_registry_path()
    )
    now = datetime.now(timezone.utc)
    if arguments.month:
        year, month = [
            int(value) for value in arguments.month.split("_", 1)
        ]
        periods = [(year, month)]
        explicit = True
    else:
        local_now = now.astimezone(MOUNTAIN)
        periods = _periods(local_now)
        explicit = False

    reports = {}
    problems = []
    for year, month in periods:
        state_path = (
            newsletters_dir()
            / f"{year:04d}-{month:02d}"
            / ".sendgrid.json"
        )
        class_day = habit_class_date(year, month)
        if class_day:
            ensure_recording_draft(year, month)
        if not state_path.exists() and not explicit:
            local_sections = read_local_sections(year, month)
            if not local_sections:
                continue
        else:
            local_sections = read_local_sections(year, month)
        locked_sections = lock_due_sections(
            year=year,
            month=month,
            class_date=class_day,
            now=now,
        )
        if (
            not explicit
            and not state_path.exists()
            and local_sections
            and not locked_sections
        ):
            continue
        campaigns = SendGridCampaigns(
            api=api,
            registry=registry,
            state_path=state_path,
        )
        try:
            if locked_sections:
                provision_drafts(
                    campaigns=campaigns,
                    year=year,
                    month=month,
                    class_date=class_day,
                    sections=locked_sections,
                )
            report = schedule_month(
                campaigns=campaigns,
                year=year,
                month=month,
                class_date=class_day,
                now=now,
            )
            apply_provider_report(
                year=year,
                month=month,
                report=report,
                now=now,
            )
        except Exception as exc:
            affected = set(locked_sections)
            if not affected:
                affected = {
                    PURPOSE_SECTIONS[purpose.value]
                    for purpose in campaigns.expected_purposes()
                    if purpose.value in PURPOSE_SECTIONS
                }
            mark_provider_error(
                year=year,
                month=month,
                audiences=affected,
                error=str(exc),
                now=now,
            )
            period = f"{year:04d}_{month:02d}"
            reports[period] = {
                "Workflow": {
                    "status": "error",
                    "error": str(exc),
                }
            }
            problems.append(f"{period}: workflow: {exc}")
            continue
        period = f"{year:04d}_{month:02d}"
        reports[period] = report
        for purpose, item in report.items():
            if item["status"] in {"missing", "overdue", "unexpected"}:
                problems.append(
                    f"{period}: {purpose}: {item['status']}"
                )

    print(json.dumps(reports, indent=2, sort_keys=True))
    if problems:
        message = (
            ":warning: SendGrid mailing scheduler found problems:\n"
            + "\n".join(problems)
        )
        post_slack(slack_status_channel, message)
        if slack_warning_channel != slack_status_channel:
            post_slack(slack_warning_channel, message)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
