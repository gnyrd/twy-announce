"""next_live_classes: the next published classes, written as a member reads them."""
from datetime import datetime, timezone

import pytest

import next_live_classes as nlc

# 2026-09-28 21:00 Mountain.
NOW = datetime(2026, 9, 29, 3, 0, tzinfo=timezone.utc)
PAGE = "https://studio.tiffanywoodyoga.com/event/details/1038364"


def plan(day, clock="08:00", title="Hip Flexor Flow", published=True,
         authored=True, event=1038364):
    return {"date": day, "time": clock, "title": title, "published": published,
            "authored": authored, "marvelous_event_id": event}


@pytest.fixture(autouse=True)
def switched_on(monkeypatch):
    monkeypatch.setattr(nlc.contribution, "continued", lambda feature=None: True)


def classes(plans, now=NOW):
    return nlc.upcoming_classes(now, fetch=lambda start, end: plans)


def test_a_class_reads_as_a_member_reads_it():
    assert classes([plan("2026-10-06")]) == [
        f"Tuesday, October 6, 8:00am Mountain: [Hip Flexor Flow]({PAGE})"
    ]


def test_an_evening_class_and_a_messy_title():
    assert classes([plan("2026-10-05", clock="17:30", title="  Quads  & Hip Flexors ")]) == [
        f"Monday, October 5, 5:30pm Mountain: [Quads & Hip Flexors]({PAGE})"
    ]


def test_the_next_two_soonest_first():
    lines = classes([plan("2026-10-06", title="C"), plan("2026-09-29", title="A"),
                     plan("2026-10-01", title="B")])
    assert [line.split("[")[1].split("]")[0] for line in lines] == ["A", "B"]


def test_only_published_authored_plans_with_an_event_count():
    assert classes([
        plan("2026-09-29", published=False),
        plan("2026-09-30", authored=False),
        plan("2026-10-01", event=None),
        plan("2026-10-02", title="   "),
    ]) == []


def test_a_class_already_started_or_past_two_weeks_out_is_not_named():
    # 08:00 on the 28th is thirteen hours gone; the 13th starts after the window.
    assert classes([plan("2026-09-28"), plan("2026-10-13")]) == []
    assert classes([plan("2026-10-12", clock="17:30")]) != []


def test_the_window_asked_for_is_in_mountain_dates():
    asked = []
    nlc.upcoming_classes(NOW, fetch=lambda start, end: asked.append((start, end)) or [])
    assert [(s.isoformat(), e.isoformat()) for s, e in asked] == [("2026-09-28", "2026-10-12")]


def test_switched_off_it_names_nothing_and_asks_nobody(monkeypatch):
    monkeypatch.setattr(nlc.contribution, "continued", lambda feature=None: False)

    def fetch(start, end):
        raise AssertionError("asked the classes app while switched off")

    assert nlc.upcoming_classes(NOW, fetch=fetch) == []


def test_a_classes_app_that_does_not_answer_names_nothing():
    def fetch(start, end):
        raise nlc.requests.ConnectionError("down")

    assert nlc.upcoming_classes(NOW, fetch=fetch) == []
