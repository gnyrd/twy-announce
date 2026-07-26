"""Locked TWY SendGrid mailing names, schedules, and segment queries."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from enum import Enum
import re
from zoneinfo import ZoneInfo


MOUNTAIN = ZoneInfo("America/Denver")
FORBIDDEN_NAME_CHARACTERS = frozenset({"-", "\u2013", "\u2014"})
EMAIL_SUBSCRIBED = "Email: Subscribed"
EMAIL_UNSUBSCRIBED = "Email: Unsubscribed"
MEMBER_YOGA_LIFESTYLE = "Member: Yoga Lifestyle"
MEMBER_ARCHIVE = "Member: Archive"


class MailingPurpose(str, Enum):
    MONTHLY = "Monthly"
    GENERAL_INVITATION = "General Invitation"
    RESEND = "Resend"
    GENTLE_REMINDER = "Gentle Reminder"
    REGISTERED_REMINDER = "Registered Reminder"
    FOLLOW_UP_1 = "Follow Up 1"
    FOLLOW_UP_2 = "Follow Up 2"


def validate_sendgrid_name(name: str) -> str:
    normalized = name.strip()
    if not normalized:
        raise ValueError("SendGrid name must not be empty")
    if any(character in normalized for character in FORBIDDEN_NAME_CHARACTERS):
        raise ValueError("SendGrid name contains prohibited punctuation")
    return normalized


def mailing_name(
    year: int,
    month: int,
    purpose: MailingPurpose,
) -> str:
    if not 1 <= month <= 12:
        raise ValueError("month must be from 1 through 12")
    program = (
        "Yoga Lifestyle"
        if purpose is MailingPurpose.MONTHLY
        else "Yoga Habit"
    )
    return validate_sendgrid_name(
        f"{program}: {year:04d}_{month:02d}: {purpose.value}"
    )


def habit_activity_name(year: int, month: int, activity: str) -> str:
    if activity not in {"Interested", "Registered"}:
        raise ValueError("unsupported Yoga Habit activity")
    if not 1 <= month <= 12:
        raise ValueError("month must be from 1 through 12")
    return validate_sendgrid_name(
        f"Yoga Habit: {activity}: {year:04d}_{month:02d}"
    )


def _previous_weekday_strictly_before(day: date, weekday: int) -> date:
    delta = (day.weekday() - weekday) % 7
    if delta == 0:
        delta = 7
    return day - timedelta(days=delta)


def _first_business_day(year: int, month: int) -> date:
    day = date(year, month, 1)
    while day.weekday() >= 5:
        day += timedelta(days=1)
    return day


def _at_mountain(day: date, hour: int, minute: int) -> datetime:
    local = datetime.combine(day, time(hour, minute), tzinfo=MOUNTAIN)
    return local.astimezone(timezone.utc)


def mailing_schedule(
    year: int,
    month: int,
    purpose: MailingPurpose,
    class_date: date | None,
) -> datetime:
    if purpose is MailingPurpose.MONTHLY:
        return _at_mountain(_first_business_day(year, month), 9, 39)
    if class_date is None:
        raise ValueError("Habit mailing requires a class date")
    if (class_date.year, class_date.month) != (year, month):
        raise ValueError("class date must be in the mailing period")
    if purpose is MailingPurpose.GENERAL_INVITATION:
        return _at_mountain(
            _previous_weekday_strictly_before(class_date, 0),
            9,
            39,
        )
    if purpose is MailingPurpose.RESEND:
        return _at_mountain(
            _previous_weekday_strictly_before(class_date, 2),
            9,
            39,
        )
    if purpose is MailingPurpose.GENTLE_REMINDER:
        return _at_mountain(
            _previous_weekday_strictly_before(class_date, 4),
            17,
            0,
        )
    if purpose is MailingPurpose.REGISTERED_REMINDER:
        return _at_mountain(class_date - timedelta(days=1), 10, 0)
    if purpose is MailingPurpose.FOLLOW_UP_1:
        return _at_mountain(class_date + timedelta(days=1), 10, 0)
    if purpose is MailingPurpose.FOLLOW_UP_2:
        return _at_mountain(class_date + timedelta(days=7), 10, 0)
    raise ValueError("unsupported mailing purpose")


_PROVIDER_ID = re.compile(r"^[A-Za-z0-9-]+$")


def _provider_id(value: str) -> str:
    if not value or not _PROVIDER_ID.fullmatch(value):
        raise ValueError("invalid SendGrid provider identifier")
    return value


def general_invitation_query(
    *,
    subscribed_list_id: str,
    member_list_id: str,
) -> tuple[str, list[str]]:
    subscribed = _provider_id(subscribed_list_id)
    member = _provider_id(member_list_id)
    query = (
        "SELECT contact_id, updated_at "
        "FROM contact_data "
        f"WHERE NOT array_contains(list_ids, ['{member}'])"
    )
    return query, [subscribed]


def non_opener_query(single_send_id: str) -> str:
    identifier = _provider_id(single_send_id)
    return (
        "SELECT c.contact_id, c.updated_at "
        "FROM contact_data c "
        "JOIN event_data e1 ON c.contact_id = e1.contact_id "
        "WHERE e1.event_source = 'mail' "
        "AND e1.event_type = 'processed' "
        "AND e1.DATA:payload.unique_args.singlesend_id = "
        f"'{identifier}' "
        "AND c.contact_id NOT IN ("
        "SELECT e2.contact_id "
        "FROM event_data e2 "
        "WHERE e2.event_source = 'mail' "
        "AND e2.event_type = 'open' "
        "AND e2.DATA:payload.unique_args.singlesend_id = "
        f"'{identifier}'"
        ")"
    )


def opener_not_registered_query(
    single_send_id: str,
    registered_list_id: str,
) -> str:
    send_identifier = _provider_id(single_send_id)
    registered_identifier = _provider_id(registered_list_id)
    return (
        "SELECT c.contact_id, c.updated_at "
        "FROM contact_data c "
        "JOIN event_data e ON c.contact_id = e.contact_id "
        "WHERE e.event_source = 'mail' "
        "AND e.event_type = 'open' "
        "AND e.DATA:payload.unique_args.singlesend_id = "
        f"'{send_identifier}' "
        f"AND NOT array_contains(c.list_ids, ['{registered_identifier}'])"
    )


def interested_nonmember_query(
    *,
    interested_list_id: str,
    member_list_id: str,
) -> tuple[str, list[str]]:
    interested = _provider_id(interested_list_id)
    member = _provider_id(member_list_id)
    query = (
        "SELECT contact_id, updated_at "
        "FROM contact_data "
        f"WHERE NOT array_contains(list_ids, ['{member}'])"
    )
    return query, [interested]
