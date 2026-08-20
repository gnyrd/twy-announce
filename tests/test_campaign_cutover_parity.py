"""Characterization of the campaign-model cutover: does the new campaign
scheduling model reproduce the live SendGrid workflow's per-mailing schedule?

These are the proof behind the staged per-mailing cutover. The live workflow
(sendgrid_mailings.mailing_schedule) is the ground truth for what sends today.
The campaign model schedules an email as an anchor (first_weekday or class_date)
plus a fixed offset_days, at a single campaign send time. These tests pin, with
the real functions, exactly where the two agree and where the model still cannot
express the live behavior, so the cutover can proceed message by message with a
target rather than a guess.

They guard three things going forward:
1. For the normal Saturday class, fixed offsets reproduce every mailing's DATE.
2. The campaign first_weekday anchor equals the live workflow's first business day.
3. The two known gaps stay characterized: a non-Saturday class breaks the three
   weekday-anchored mailings (they need a weekday anchor, not a day offset), and
   five mailings send at a different time of day than the model's single time.
"""
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from sendgrid_mailings import MailingPurpose as P
from sendgrid_mailings import _first_business_day, mailing_schedule

# The campaign model, mirrored from campaign_launch: an anchored email lands on
# (anchor + offset_days) at SEND_HOUR:SEND_MINUTE Mountain, in UTC.
from campaign_launch import SEND_HOUR, SEND_MINUTE, _first_weekday

MOUNTAIN = ZoneInfo("America/Denver")

# The offsets from the class date that reproduce each live mailing's date when
# the class is a Saturday, the standing Yoga Habit cadence.
SATURDAY_OFFSETS = {
    P.GENERAL_INVITATION: -5,   # Monday before a Saturday
    P.RESEND: -3,               # Wednesday before a Saturday
    P.GENTLE_REMINDER: -1,      # Friday before a Saturday
    P.REGISTERED_REMINDER: -1,  # class date minus one day
    P.CLASS_RECORDING: 1,       # class date plus one day
    P.FOLLOW_UP_1: 1,           # class date plus one day
    P.FOLLOW_UP_2: 7,           # class date plus seven days
}
# The three whose live schedule is a weekday relative to the class, not a fixed
# day count. These are the ones a non-Saturday class breaks.
WEEKDAY_ANCHORED = {P.GENERAL_INVITATION, P.RESEND, P.GENTLE_REMINDER}


def _model_send_at(anchor_day, offset_days):
    day = anchor_day + timedelta(days=offset_days)
    local = datetime.combine(day, time(SEND_HOUR, SEND_MINUTE), tzinfo=MOUNTAIN)
    return local.astimezone(timezone.utc)


def test_saturday_class_reproduces_every_mailing_date():
    """The common case: with a Saturday class, the campaign offsets land every
    class-anchored mailing on the same calendar date the live workflow does."""
    class_date = date(2026, 9, 12)  # a Saturday
    assert class_date.weekday() == 5
    for purpose, offset in SATURDAY_OFFSETS.items():
        old = mailing_schedule(2026, 9, purpose, class_date)
        new = _model_send_at(class_date, offset)
        assert old.date() == new.date(), (
            f"{purpose.value}: live {old.date()} vs model {new.date()}"
        )


def test_campaign_first_weekday_matches_live_monthly_anchor():
    """The members' Monthly newsletter anchors on the first weekday of the
    month in both models, so the anchor functions must not drift apart."""
    for year in (2026, 2027):
        for month in range(1, 13):
            assert _first_weekday(year, month) == _first_business_day(year, month)


def test_monthly_date_reproduced_by_first_weekday_anchor():
    class_date = date(2026, 9, 12)
    old = mailing_schedule(2026, 9, P.MONTHLY, class_date)
    new = _model_send_at(_first_weekday(2026, 9), 0)
    assert old.date() == new.date()


def test_non_saturday_class_breaks_the_weekday_anchored_mailings():
    """GAP, characterized: a class on any other weekday moves the Monday, the
    Wednesday and the Friday before it, but the fixed offsets do not follow. A
    weekday anchor is the model change that closes this. Until it lands, the
    campaign representation of these three is only correct for a Saturday class."""
    class_date = date(2026, 9, 13)  # a Sunday
    assert class_date.weekday() != 5
    diverged = set()
    for purpose in WEEKDAY_ANCHORED:
        old = mailing_schedule(2026, 9, purpose, class_date)
        new = _model_send_at(class_date, SATURDAY_OFFSETS[purpose])
        if old.date() != new.date():
            diverged.add(purpose)
    assert diverged == WEEKDAY_ANCHORED


def test_five_mailings_send_at_a_different_time_than_the_model():
    """GAP, characterized: the model sends every campaign email at one time
    (SEND_HOUR:SEND_MINUTE). Five live mailings send at a different time of day.
    Reproducing the exact hour needs a per-message send time in the model, a
    separate JP decision from whether it matters."""
    class_date = date(2026, 9, 12)
    differ = set()
    for purpose, offset in SATURDAY_OFFSETS.items():
        old = mailing_schedule(2026, 9, purpose, class_date)
        new = _model_send_at(class_date, offset)
        if old.timetz() != new.timetz():
            differ.add(purpose)
    assert differ == {
        P.GENTLE_REMINDER,
        P.REGISTERED_REMINDER,
        P.CLASS_RECORDING,
        P.FOLLOW_UP_1,
        P.FOLLOW_UP_2,
    }
