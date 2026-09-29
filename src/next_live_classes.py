#!/usr/bin/env python3
"""The next live classes, written the way a welcome email names them.

The welcome emails carry {{next_class_1}} and {{next_class_2}}
(journey_personalization), and this is what fills them: the next classes on
Tiff's class plans that are published to HeyMarvelous and start within the
next two weeks, each as a member reads it, "Tuesday, October 6, 8:00am
Mountain:" and the class name linking to its HeyMarvelous page. Members who
came to live classes in their first week stayed far longer (the membership
retention report of 2026-09-28), and until then the welcome emails named no
class at all.

Class plans are the single source of truth, so this reads the classes app's
plan list, the same one the Habit and Integration emails read. A plan counts
only when it is published, authored and carries its HeyMarvelous event, so a
calendar placeholder is never promised to anybody.

Behind the maintenance switch, twy_platform.contribution.continued(), with no
name of its own (JP 2026-09-28, the same as the member check-ins). Switched
off, it names no class, every {{#next_classes}} section is left out, and the
welcome emails read exactly as they did before. A classes app that does not
answer does the same for that one send, and says so in the log.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
import logging
from zoneinfo import ZoneInfo

import requests

from habit_newsletter_prompt import CLASSES_API
from twy_platform import contribution
from twy_platform.urls import build_register_url

log = logging.getLogger(__name__)

MOUNTAIN = ZoneInfo("America/Denver")

# The draft JP approved 2026-09-28: the next two classes, none that start more
# than two weeks out.
HOW_MANY = 2
WINDOW_DAYS = 14


def fetch_plans(start: date, end: date) -> list:
    """Every plan dated start through end, from the classes app."""
    resp = requests.get(
        f"{CLASSES_API}/api/plans",
        params={"from": start.isoformat(), "to": end.isoformat()},
        timeout=10,
    )
    resp.raise_for_status()
    plans = resp.json()
    return plans if isinstance(plans, list) else []


def class_start(plan):
    """When the class starts, in Mountain time, or None if the plan cannot say."""
    try:
        day = date.fromisoformat(str(plan.get("date") or ""))
        moment = time.fromisoformat(str(plan.get("time") or ""))
    except (TypeError, ValueError):
        return None
    return datetime.combine(day, moment, tzinfo=MOUNTAIN)


def class_title(plan) -> str:
    """The plan's title with its stray spacing gone. Brackets would break the link."""
    title = " ".join(str(plan.get("title") or "").split())
    return title.replace("[", "(").replace("]", ")")


def class_line(plan, start: datetime) -> str:
    """`Tuesday, October 6, 8:00am Mountain: [Hip Flexor Flow](its HM page)`."""
    hour = start.hour % 12 or 12
    meridiem = "am" if start.hour < 12 else "pm"
    when = (
        f"{start:%A}, {start:%B} {start.day}, "
        f"{hour}:{start.minute:02d}{meridiem} Mountain"
    )
    url = build_register_url(plan.get("marvelous_event_id"))
    return f"{when}: [{class_title(plan)}]({url})"


def upcoming_classes(now: datetime = None, *, fetch=fetch_plans) -> list:
    """The class lines for the next classes, soonest first, at most HOW_MANY.

    Empty when the maintenance switch is off, when the classes app does not
    answer, or when nothing published starts in the next WINDOW_DAYS.
    """
    if not contribution.continued():
        return []
    now = now or datetime.now(timezone.utc)
    until = now + timedelta(days=WINDOW_DAYS)
    try:
        plans = fetch(
            now.astimezone(MOUNTAIN).date(), until.astimezone(MOUNTAIN).date()
        )
    except (requests.RequestException, ValueError) as exc:
        log.warning("classes app did not answer, so no class is named: %s", exc)
        return []

    upcoming = []
    for plan in plans:
        if not (
            plan.get("published")
            and plan.get("authored", True)
            and plan.get("marvelous_event_id")
            and class_title(plan)
        ):
            continue
        start = class_start(plan)
        if start is None or not now < start <= until:
            continue
        upcoming.append((start, plan))
    upcoming.sort(key=lambda pair: pair[0])
    return [class_line(plan, start) for start, plan in upcoming[:HOW_MANY]]
