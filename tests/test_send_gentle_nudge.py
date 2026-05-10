"""Date-guard tests for send_gentle_nudge.py — Friday strictly before class."""
from datetime import date
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
from send_gentle_nudge import is_today_due

MAY_CLASS = date(2026, 5, 16)  # Sat

def test_fires_on_friday_before_class():
    assert is_today_due(date(2026, 5, 15), MAY_CLASS) is True

def test_silent_on_non_friday_in_class_week():
    for d in (date(2026, 5, 11), date(2026, 5, 12), date(2026, 5, 13),
              date(2026, 5, 14), date(2026, 5, 16), date(2026, 5, 17)):
        assert is_today_due(d, MAY_CLASS) is False, f"fired on {d}"

def test_silent_on_friday_two_weeks_before():
    assert is_today_due(date(2026, 5, 8), MAY_CLASS) is False

def test_silent_on_friday_after_class():
    assert is_today_due(date(2026, 5, 22), MAY_CLASS) is False

def test_midweek_class_friday_is_5_days_before():
    wed = date(2026, 6, 3)
    fri_before = date(2026, 5, 29)
    assert is_today_due(fri_before, wed) is True
