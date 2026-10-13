"""YouTube's Data API rounds subscriberCount to three significant figures, so
the daily report takes the exact net change from YouTube Analytics
(JP 2026-10-13, "doesn't seem to include the new YT subscriber")."""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from daily_status_report import format_youtube_exact_line, youtube_exact_changes

NOW = datetime(2026, 10, 13, 7, 0)
ROWS = [
    ["2026-09-13", 0, 1],
    ["2026-09-18", 1, 0],
    ["2026-10-04", 0, 1],
    ["2026-10-08", 1, 0],
    ["2026-10-09", 1, 0],
    ["2026-10-10", 0, 0],
]


def test_net_change_counts_back_from_the_last_day_analytics_has():
    exact = youtube_exact_changes(NOW, fetch=lambda s, e: ROWS)
    assert exact["through"] == "2026-10-10"
    assert exact["week"] == 1          # 10-04 to 10-10: -1 +1 +1
    assert exact["month"] == 1         # 09-11 to 10-10, the 09-13 loss included
    assert exact["year"] == 1


def test_line_shows_zeros_and_the_through_date():
    line = format_youtube_exact_line({"through": "2026-10-10", "week": 0, "month": 3, "year": -1})
    assert "week: 0" in line and "month: +3" in line and "year: -1" in line
    assert line.endswith("(exact, through Oct 10)")


def test_analytics_failure_falls_back():
    def boom(s, e):
        raise RuntimeError("token expired")
    assert youtube_exact_changes(NOW, fetch=boom) is None
    assert youtube_exact_changes(NOW, fetch=lambda s, e: []) is None
