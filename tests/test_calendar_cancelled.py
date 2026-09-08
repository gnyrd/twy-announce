"""The calendar feed marks a cancelled class rather than dropping it."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import calendar_server


def _plan(**overrides):
    plan = {"id": "p1", "date": "2099-09-16", "time": "08:00", "duration": 30,
            "class_type": "Breath", "title": "Midline", "published": True,
            "marvelous_event_id": 1041388}
    plan.update(overrides)
    return plan


def _ics(monkeypatch, plan):
    monkeypatch.setattr(calendar_server, "_iter_published_plans", lambda: [(plan["date"], plan)])
    monkeypatch.setattr(calendar_server, "_build_event_index", lambda: {})
    return calendar_server._build_ics()


def test_a_cancelled_plan_is_marked_and_kept(monkeypatch):
    ics = _ics(monkeypatch, _plan(cancelled={"at": "x", "by": "classes", "reason": "cancelled"}))
    assert "STATUS:CANCELLED" in ics
    assert "SUMMARY:CANCELLED: Breath Awareness: Midline" in ics


def test_a_live_plan_is_still_confirmed(monkeypatch):
    ics = _ics(monkeypatch, _plan())
    assert "STATUS:CONFIRMED" in ics
    assert "CANCELLED" not in ics
