"""The daily traffic report reads Plausible at three scales and says so plainly.

Dates are pinned because the report's whole value is comparing like with
like: yesterday against the same weekday last week, this month's first N
days against last month's first N days, this year's first N days against
last year's.
"""

import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import traffic_report as tr


def test_report_day_is_yesterday_in_mountain_time():
    # 02:30 UTC on Sep 3 is 20:30 MDT on Sep 2, so "yesterday" is Sep 1.
    now = datetime(2026, 9, 3, 2, 30, tzinfo=ZoneInfo("UTC"))
    assert tr.report_day(now) == date(2026, 9, 1)
    # 13:10 UTC on Sep 3 is 07:10 MDT on Sep 3, so "yesterday" is Sep 2.
    now = datetime(2026, 9, 3, 13, 10, tzinfo=ZoneInfo("UTC"))
    assert tr.report_day(now) == date(2026, 9, 2)


def test_spans_compare_like_with_like():
    day = date(2026, 9, 2)
    assert tr.month_to_date(day) == ["2026-09-01", "2026-09-02"]
    assert tr.previous_month_same_span(day) == ["2026-08-01", "2026-08-02"]
    assert tr.previous_month_full(day) == ["2026-08-01", "2026-08-31"]
    assert tr.year_to_date(day) == ["2026-01-01", "2026-09-02"]
    assert tr.previous_year_same_span(day) == ["2025-01-01", "2025-09-02"]
    assert tr.same_weekday_last_week(day) == ["2026-08-26", "2026-08-26"]
    # The 31st of a month after a 30-day month compares with the 30th.
    assert tr.previous_month_same_span(date(2026, 7, 31)) == ["2026-06-01", "2026-06-30"]
    # A leap day compares with 28 February the year before.
    assert tr.previous_year_same_span(date(2028, 2, 29)) == ["2027-01-01", "2027-02-28"]


def test_delta_wording():
    assert tr.delta(120, 100) == "+20 (+20%)"
    assert tr.delta(80, 100) == "-20 (-20%)"
    assert tr.delta(5, 0) == "no earlier data to compare"
    assert tr.delta(0, 0) == "no earlier data"


def test_ai_sources_are_counted_case_insensitively():
    rows = [("Google", 30), ("chatgpt.com", 2), ("Perplexity", 1), ("Direct / None", 40)]
    assert tr.ai_visitors(rows) == 3


def fake_query(calls):
    """A Plausible stand-in that answers by date range and dimension."""

    def query(body):
        calls.append(body)
        rng = tuple(body["date_range"])
        dims = body.get("dimensions") or []
        if dims == ["visit:source"]:
            return {"results": [
                {"dimensions": ["Google"], "metrics": [12]},
                {"dimensions": ["Direct / None"], "metrics": [9]},
                {"dimensions": ["chatgpt.com"], "metrics": [2]},
            ]}
        if dims == ["event:page"]:
            return {"results": [
                {"dimensions": ["/"], "metrics": [15]},
                {"dimensions": ["/membership/"], "metrics": [6]},
            ]}
        if dims == ["visit:channel"]:
            return {"results": [
                {"dimensions": ["Direct"], "metrics": [9]},
                {"dimensions": ["Organic Search"], "metrics": [12]},
            ]}
        table = {
            ("2026-09-02", "2026-09-02"): [23, 31],
            ("2026-08-26", "2026-08-26"): [20, 25],
            ("2026-09-01", "2026-09-02"): [45, 60],
            ("2026-08-01", "2026-08-02"): [30, 40],
            ("2026-08-01", "2026-08-31"): [333, 420],
            ("2026-01-01", "2026-09-02"): [900, 1200],
            ("2025-01-01", "2025-09-02"): [0, 0],
        }
        return {"results": [{"metrics": table.get(rng, [1, 1])}]}

    return query


def test_report_reads_three_scales_and_the_other_sites():
    calls = []
    text = tr.build_report(
        fake_query(calls),
        ["tiffanywoodyoga.com", "studio.tiffanywoodyoga.com", "habit.tiffanywoodyoga.com"],
        date(2026, 9, 2),
    )
    assert text.startswith("*Website traffic: Wednesday Sep 2, 2026*")
    assert "• 23 visitors, 31 pageviews. Same day last week: 20 visitors, +3 (+15%)." in text
    assert "• From search: 12. From AI tools: 2." in text
    assert "• Top sources: Google 12, Direct 9, chatgpt.com 2." in text
    assert "• Top pages: / 15, /membership/ 6." in text
    assert "*September so far* (through the 2nd)" in text
    assert "• 45 visitors, 60 pageviews. Same span in August: 30 visitors, +15 (+50%)." in text
    assert "• All of August: 333 visitors." in text
    assert "*2026 so far*" in text
    assert "• 900 visitors, 1200 pageviews. Same span in 2025: no data (tracking started in 2026)." in text
    # The fake answers by date range, so the other sites echo the main numbers.
    assert "• Studio (HeyMarvelous): 23 yesterday, 45 this month (+15 (+50%) vs the same span last month)." in text
    assert "• Yoga Habit page: 23 yesterday, 45 this month" in text
    assert "—" not in text and "–" not in text
    # Every query names the site it is about.
    assert {call["site_id"] for call in calls} == {
        "tiffanywoodyoga.com", "studio.tiffanywoodyoga.com", "habit.tiffanywoodyoga.com"
    }


def test_main_dry_run_prints_without_posting(monkeypatch, capsys):
    posted = []
    monkeypatch.setattr(tr, "load_env", lambda: None)
    monkeypatch.setattr(tr, "plausible_query_from_env", lambda: fake_query([]))
    monkeypatch.setattr(tr, "site_ids_from_env", lambda: ["tiffanywoodyoga.com"])
    monkeypatch.setattr(
        tr, "post_slack_as_reporter", lambda channel, text: posted.append((channel, text))
    )
    assert tr.main(["--dry-run", "--date", "2026-09-02"]) == 0
    assert posted == []
    assert "[DRY RUN] not posted" in capsys.readouterr().out
    assert tr.main(["--date", "2026-09-02"]) == 0
    assert posted and posted[0][0] == "#status-traffic"


def test_main_fails_loudly_when_plausible_is_down(monkeypatch, capsys):
    def broken():
        raise RuntimeError("PLAUSIBLE_API_KEY is not set")

    monkeypatch.setattr(tr, "load_env", lambda: None)
    monkeypatch.setattr(tr, "plausible_query_from_env", broken)
    assert tr.main(["--dry-run", "--date", "2026-09-02"]) == 1
    assert "traffic report failed" in capsys.readouterr().err


def test_report_posts_as_the_reporter_bot_to_status_traffic() -> None:
    """JP reads #status-traffic and the cron identity is TWY Reporter.

    The previous default, #twy-status, does not exist in the workspace, so
    every post was refused with channel_not_found and swallowed.
    """
    source = Path(tr.__file__).read_text(encoding="utf-8")
    assert 'SLACK_CHANNEL = "#status-traffic"' in source
    assert "post_slack_as_reporter" in source
    assert "post_slack(" not in source
