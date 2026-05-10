"""Date-guard tests for send_non_opener.py — Tuesday strictly before class."""
from datetime import date
from pathlib import Path
import sys
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
from send_non_opener import is_today_due

MAY_CLASS = date(2026, 5, 16)  # Sat

def test_fires_on_tuesday_before_class():
    assert is_today_due(date(2026, 5, 12), MAY_CLASS) is True

def test_silent_on_non_tuesday_in_class_week():
    for d in (date(2026, 5, 11), date(2026, 5, 13), date(2026, 5, 14),
              date(2026, 5, 15), date(2026, 5, 16), date(2026, 5, 17)):
        assert is_today_due(d, MAY_CLASS) is False, f"fired on {d}"

def test_silent_on_tuesday_two_weeks_before():
    assert is_today_due(date(2026, 5, 5), MAY_CLASS) is False

def test_silent_on_tuesday_after_class():
    assert is_today_due(date(2026, 5, 19), MAY_CLASS) is False

def test_midweek_class_tuesday_is_day_before():
    wed = date(2026, 6, 3)
    assert is_today_due(date(2026, 6, 2), wed) is True

def test_class_on_tuesday_self_collision_silent():
    # If class itself is on a Tuesday (impossible per TWY convention but defensive):
    # today=class_date is not strictly before, must be silent.
    tue = date(2026, 6, 2)
    assert is_today_due(tue, tue) is False
