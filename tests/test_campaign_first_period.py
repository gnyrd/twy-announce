"""The first_period guard: a recurring campaign can be On and approved ahead of
the period it should start, and the tick holds it until then.

This is what lets the Habit and TYL campaigns be armed in August to start in
October, while September stays the Transitions one-off, without the daily tick
launching them early.
"""
import campaign_ticker as ct


def _campaign(**over):
    j = {
        "type": "campaign", "journey_id": "yoga_habit", "name": "Yoga Habit",
        "campaign_month": "2026_08", "recurrence": "monthly", "active": True,
        "segment_id": "seg-1",
        "emails": [{"subject": "A", "body": "x", "interval_days": 0,
                    "approved_at": "2026-08-19T00:00:00+00:00"}],
    }
    j.update(over)
    return j


def test_before_first_period_holds_earlier_periods():
    j = _campaign(first_period="2026_10")
    assert ct.before_first_period(j, 2026, 9) is True
    assert ct.before_first_period(j, 2026, 8) is True
    assert ct.before_first_period(j, 2026, 10) is False
    assert ct.before_first_period(j, 2026, 11) is False
    assert ct.before_first_period(j, 2027, 1) is False


def test_absent_first_period_runs_any_period():
    assert ct.before_first_period(_campaign(), 2026, 1) is False


def test_launch_due_holds_a_campaign_before_its_first_period():
    launched = []

    def one(pinned, year, month):
        launched.append((year, month))
        return "ok"

    j = _campaign(first_period="2026_10")
    ct.launch_due_campaigns([j], 2026, 9, launch_one=one)
    assert launched == []  # held before October
    ct.launch_due_campaigns([j], 2026, 10, launch_one=one)
    assert launched == [(2026, 10)]  # runs from October
