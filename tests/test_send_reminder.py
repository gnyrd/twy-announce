"""Date-guard tests for send_reminder.py — class is exactly tomorrow."""
from datetime import date
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
from send_reminder import is_today_due

MAY_CLASS = date(2026, 5, 16)  # Sat

def test_fires_when_class_is_tomorrow():
    assert is_today_due(date(2026, 5, 15), MAY_CLASS) is True

def test_silent_two_days_before():
    assert is_today_due(date(2026, 5, 14), MAY_CLASS) is False

def test_silent_on_class_day_itself():
    assert is_today_due(date(2026, 5, 16), MAY_CLASS) is False

def test_silent_after_class():
    assert is_today_due(date(2026, 5, 17), MAY_CLASS) is False

def test_works_for_midweek_class():
    wed = date(2026, 6, 3)
    assert is_today_due(date(2026, 6, 2), wed) is True
    assert is_today_due(date(2026, 6, 1), wed) is False
