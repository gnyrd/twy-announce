"""The weekday-before-class anchor and per-message send time in the launcher.

These are the scheduling half of the campaign cutover: they let a recurring
campaign reproduce the live SendGrid workflow's schedule exactly, for any class
weekday, not only a Saturday. The weekday anchor lands the invitation, resend and
gentle reminder on the Monday, Wednesday and Friday strictly before the class;
the per-message send time reproduces the workflow's staggered hours. Parity is
asserted against sendgrid_mailings.mailing_schedule, the live ground truth.
"""
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from campaign_launch import CampaignLauncher, GateContext, SEND_HOUR, SEND_MINUTE
from sendgrid_mailings import MailingPurpose as P
from sendgrid_mailings import mailing_schedule

MOUNTAIN = ZoneInfo("America/Denver")
SEGMENT_ID = "seg-x"


def _launcher(emails, class_date, tmp_path):
    journey = {
        "type": "campaign",
        "journey_id": "yoga_habit",
        "name": "Yoga Habit",
        "campaign_month": f"{class_date.year:04d}_{class_date.month:02d}",
        "segment_id": SEGMENT_ID,
        "emails": emails,
    }
    return CampaignLauncher(
        api=None,
        registry=None,
        journey=journey,
        state_path=tmp_path / "state.json",
        gate_context=GateContext(class_date=class_date),
    )


def test_weekday_anchor_reproduces_live_dates_for_any_class_weekday(tmp_path):
    """The correctness win: Invitation, Resend and Gentle land on the Monday,
    Wednesday and Friday before the class in both a Saturday and a Sunday
    class, matching the live workflow's date exactly."""
    emails = [
        {"subject": "Invite", "body": "x", "anchor": "class_weekday_before", "weekday": 0},
        {"subject": "Resend", "body": "x", "anchor": "class_weekday_before", "weekday": 2},
        {"subject": "Gentle", "body": "x", "anchor": "class_weekday_before", "weekday": 4},
    ]
    purposes = [P.GENERAL_INVITATION, P.RESEND, P.GENTLE_REMINDER]
    for class_date in (date(2026, 9, 12), date(2026, 9, 13)):  # Saturday, Sunday
        launcher = _launcher(emails, class_date, tmp_path)
        schedule = launcher._schedule(date(class_date.year, class_date.month, 1))
        for index, purpose in enumerate(purposes):
            old = mailing_schedule(class_date.year, class_date.month, purpose, class_date)
            got = schedule[index].astimezone(MOUNTAIN).date()
            assert got == old.astimezone(MOUNTAIN).date(), (
                f"{purpose.value} on a {class_date.strftime('%A')} class: "
                f"model {got} vs live {old.astimezone(MOUNTAIN).date()}"
            )


def test_per_message_send_time_overrides_the_default(tmp_path):
    class_date = date(2026, 9, 12)
    emails = [
        {"subject": "A", "body": "x", "anchor": "class_date", "offset_days": 1,
         "send_hour": 17, "send_minute": 17},
        {"subject": "B", "body": "x", "anchor": "class_date", "offset_days": 7},
    ]
    launcher = _launcher(emails, class_date, tmp_path)
    schedule = launcher._schedule(date(2026, 9, 1))
    a = schedule[0].astimezone(MOUNTAIN)
    b = schedule[1].astimezone(MOUNTAIN)
    assert (a.hour, a.minute) == (17, 17)
    assert (b.hour, b.minute) == (SEND_HOUR, SEND_MINUTE)


def test_weekday_anchor_with_matching_time_reproduces_the_full_live_datetime(tmp_path):
    """The hard case, fully closed: a Sunday class, the Gentle Reminder on the
    Friday before at 17:17 MT, reproduces the live workflow's exact datetime."""
    class_date = date(2026, 9, 13)  # Sunday
    emails = [
        {"subject": "Gentle", "body": "x", "anchor": "class_weekday_before",
         "weekday": 4, "send_hour": 17, "send_minute": 17},
    ]
    launcher = _launcher(emails, class_date, tmp_path)
    schedule = launcher._schedule(date(2026, 9, 1))
    old = mailing_schedule(2026, 9, P.GENTLE_REMINDER, class_date)
    assert schedule[0] == old


def test_weekday_anchor_offset_moves_off_the_weekday(tmp_path):
    """offset_days still applies on top of the weekday anchor."""
    class_date = date(2026, 9, 12)  # Saturday; Monday before is 09-07
    emails = [
        {"subject": "A", "body": "x", "anchor": "class_weekday_before",
         "weekday": 0, "offset_days": 1},
    ]
    launcher = _launcher(emails, class_date, tmp_path)
    schedule = launcher._schedule(date(2026, 9, 1))
    assert schedule[0].astimezone(MOUNTAIN).date() == date(2026, 9, 8)
