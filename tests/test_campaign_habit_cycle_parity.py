"""The Monthly Habit Class Cycle campaign, represented in the model, reproduces
the live SendGrid workflow's schedule exactly.

This is the shadow proof for the cutover: the campaign is built here as it would
be authored (weekday anchors, class-date offsets, per-message send times, gates,
sections, dynamic audiences, the invitation's resend child), and its plan() send
times are asserted equal, to the minute, to sendgrid_mailings.mailing_schedule
for the matching mailing purpose. Nothing is sent; plan() touches no provider.
"""
import os
import sys
from datetime import date, datetime, timezone

sys.path.insert(0, os.path.dirname(__file__))

from campaign_launch import CampaignLauncher, GateContext
from sendgrid_mailings import MailingPurpose as P
from sendgrid_mailings import mailing_schedule
from test_campaign_launch import FakeAPI, FakeRegistry, SEGMENT_ID

NOW = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)
CLASS_DATE = date(2026, 9, 12)  # a Saturday


def _email(subject, section, **over):
    email = {"subject": subject, "preheader": "", "body": "placeholder", "section": section}
    email.update(over)
    return email


HABIT_CLASS_CYCLE = {
    "type": "campaign",
    "journey_id": "yoga_habit",
    "name": "Yoga Habit",
    "campaign_month": "2026_09",
    "recurrence": "monthly",
    "active": False,
    "segment_id": SEGMENT_ID,
    "segment_name": "Audience: Non Members",
    "emails": [
        # 0 General Invitation: the Monday before class, non-members, with a
        # resend child to non-openers two days later (the legacy Resend).
        _email("Come to the free class", "non_lifestyle",
               anchor="class_weekday_before", weekday=0, gate="class_exists",
               resend={"wait_days": 2}),
        # 1 Gentle Reminder: the Friday before, to invitation openers not registered.
        _email("A gentle nudge", "gentle_nudge",
               anchor="class_weekday_before", weekday=4, gate="class_exists",
               send_hour=17, send_minute=17,
               audience={"dynamic": "opener_not_registered", "of": 0}),
        # 2 Registered Reminder: the day before, to the registered.
        _email("See you tomorrow", "reminder",
               anchor="class_date", offset_days=-1, gate="class_exists",
               send_hour=10, send_minute=17,
               audience={"dynamic": "registered"}),
        # 3 Class Recording: the day after, to the registered, once ready.
        _email("Your class recording", "recording",
               anchor="class_date", offset_days=1, gate="recording_ready",
               send_hour=17, send_minute=17,
               audience={"dynamic": "registered"}),
        # 4 Follow Up 1: the day after, to interested non-members.
        _email("How was it", "ph1",
               anchor="class_date", offset_days=1, gate="class_happened",
               send_hour=10, send_minute=17,
               audience={"dynamic": "interested_nonmember"}),
        # 5 Follow Up 2: a week after, to interested non-members.
        _email("One more thing", "ph2",
               anchor="class_date", offset_days=7, gate="class_happened",
               send_hour=10, send_minute=17,
               audience={"dynamic": "interested_nonmember"}),
    ],
}

INDEX_PURPOSE = {
    0: P.GENERAL_INVITATION,
    1: P.GENTLE_REMINDER,
    2: P.REGISTERED_REMINDER,
    3: P.CLASS_RECORDING,
    4: P.FOLLOW_UP_1,
    5: P.FOLLOW_UP_2,
}


def _fmt(moment):
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _plan(tmp_path):
    launcher = CampaignLauncher(
        api=FakeAPI(), registry=FakeRegistry(), journey=HABIT_CLASS_CYCLE,
        state_path=tmp_path / "s.json", now_fn=lambda: NOW,
        gate_context=GateContext(
            class_exists=True, recording_ready=True, class_date=CLASS_DATE, now=CLASS_DATE,
        ),
    )
    return launcher.plan(CLASS_DATE)


def test_every_email_matches_the_live_schedule_to_the_minute(tmp_path):
    rows = {r["index"]: r for r in _plan(tmp_path)["emails"]}
    for index, purpose in INDEX_PURPOSE.items():
        expected = _fmt(mailing_schedule(2026, 9, purpose, CLASS_DATE))
        assert rows[index]["send_at"] == expected, (
            f"{purpose.value}: campaign {rows[index]['send_at']} vs live {expected}"
        )


def test_the_invitation_resend_child_lands_on_the_legacy_resend_date():
    # The resend fires wait_days after the invitation. Monday + 2 = Wednesday,
    # which is exactly where the live Resend mailing lands.
    invite = mailing_schedule(2026, 9, P.GENERAL_INVITATION, CLASS_DATE).date()
    resend = mailing_schedule(2026, 9, P.RESEND, CLASS_DATE).date()
    assert (resend - invite).days == 2


def test_each_email_carries_the_expected_audience(tmp_path):
    rows = {r["index"]: r for r in _plan(tmp_path)["emails"]}
    assert rows[0]["segment_name"] == "Audience: Non Members"  # static non-members
    assert rows[1]["dynamic_audience"] == "opener_not_registered"
    assert rows[2]["dynamic_audience"] == "registered"
    assert rows[3]["dynamic_audience"] == "registered"
    assert rows[4]["dynamic_audience"] == "interested_nonmember"
    assert rows[5]["dynamic_audience"] == "interested_nonmember"


TYL_MONTHLY = {
    "type": "campaign",
    "journey_id": "yoga_lifestyle",
    "name": "Yoga Lifestyle",
    "campaign_month": "2026_09",
    "recurrence": "monthly",
    "active": False,
    "segment_id": SEGMENT_ID,
    "segment_name": "Audience: Members",
    "emails": [
        _email("The monthly newsletter", "lifestyle",
               anchor="first_weekday", offset_days=0),
    ],
}


def test_tyl_monthly_matches_the_live_schedule(tmp_path):
    """The membership newsletter: the first weekday of the month, to members,
    the same instant the live Monthly mailing goes."""
    launcher = CampaignLauncher(
        api=FakeAPI(), registry=FakeRegistry(), journey=TYL_MONTHLY,
        state_path=tmp_path / "s.json", now_fn=lambda: NOW,
        gate_context=GateContext(class_date=CLASS_DATE),
    )
    row = launcher.plan(CLASS_DATE)["emails"][0]
    assert row["send_at"] == _fmt(mailing_schedule(2026, 9, P.MONTHLY, CLASS_DATE))
    assert row["segment_name"] == "Audience: Members"
