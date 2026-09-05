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
    assert tr.delta(5, 0) == "+5"
    assert tr.delta(0, 0) == ""


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
    assert text.startswith("*Main*: 23\n")
    assert "    \U0001D6AB week: *+3 (+15%)*  |  month: *+15 (+50%)*" in text
    assert "    top: / 15, /membership/ 6" in text
    assert "    *Search*: 12" in text
    assert "*Studio*: 23" in text
    assert "*Habit*: 23" in text
    # week delta only on main; studio/habit are month only
    studio_block = text.split("*Studio*:")[1].split("*Habit*:")[0]
    assert "week:" not in studio_block
    assert "MTD" not in text and "YTD" not in text
    assert "Sep" not in text and "Aug" not in text
    assert "\u2014" not in text and "\u2013" not in text and "\u2022" not in text

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

    An earlier default channel did not exist in the workspace, so every
    post was refused with channel_not_found and swallowed.
    """
    source = Path(tr.__file__).read_text(encoding="utf-8")
    assert 'SLACK_CHANNEL = "#status-traffic"' in source
    assert "post_slack_as_reporter" in source
    assert "post_slack(" not in source


def test_zeros_and_missing_baselines_are_suppressed():
    record = {
        "site_id": "studio.tiffanywoodyoga.com",
        "yesterday": {"visitors": 5, "pageviews": 9},
        "last_week_same_day": {"visitors": 0, "pageviews": 0},
        "mtd": {"visitors": 12, "pageviews": 20},
        "previous_month_same_span": {"visitors": 10, "pageviews": 15},
        "search_day": 0, "search_week": 0, "search_mtd": 3, "search_pm": 0,
        "ai_day": 0, "ai_week": 0, "ai_mtd": 0, "ai_pm": 0,
    }
    block = "\n".join(tr.format_site(record, with_top=False, weekly=False))
    # month only; Search and AI have zero visitors yesterday so both drop.
    assert block == "*Studio*: 5\n    \U0001D6AB month: *+2 (+20%)*"
    assert "week:" not in block
    assert "Search" not in block and "AI" not in block


def test_sample_report_populates_every_field():
    text = tr.format_report(*tr.sample_data())
    assert text.startswith("*Main*: 48")
    assert "\U0001D6AB week:" in text and "month:" in text
    for site in ("*Main*", "*Studio*", "*Habit*"):
        assert site in text
    assert text.count("*Search*:") == 3
    assert text.count("*AI*:") == 3
    assert "top:" in text and text.count("top:") == 1
    # week delta on main only
    assert text.count("week:") == 1
    assert "\u2014" not in text and "\u2013" not in text


def _conv_query(rows_by_key):
    """A fake Plausible that answers a conversion query keyed by
    (site_id, event:name filter, event:page filter)."""
    def query(body):
        event = page = None
        for f in body.get("filters") or []:
            if f[1] == "event:name":
                event = f[2][0]
            if f[1] == "event:page":
                page = f[2][0]
        rows = rows_by_key.get((body["site_id"], event, page), [])
        return {"results": [{"dimensions": [s], "metrics": [c]} for s, c in rows]}
    return query


def test_last_30_days_span():
    assert tr.last_30_days(date(2026, 9, 2)) == ["2026-08-04", "2026-09-02"]


def test_conversion_tiers_merges_registration_sources():
    q = _conv_query({
        ("studio.tiffanywoodyoga.com", "Form: Submission", "/buy/product/"):
            [("newsletter", 5), ("Direct / None", 2)],
        ("studio.tiffanywoodyoga.com", "Form: Submission", "/event/details/"):
            [("newsletter", 4)],
        ("habit.tiffanywoodyoga.com", "Habit Register Click", None):
            [("newsletter", 6), ("email", 1)],
        ("habit.tiffanywoodyoga.com", "Habit Signup Success", None):
            [("email", 3)],
    })
    tiers = dict(tr.conversion_tiers(q, date(2026, 9, 2)))
    assert tiers["Purchases"] == {"newsletter": 5, "Direct": 2}
    # studio event submits and habit register clicks, merged by source
    assert tiers["Registrations"] == {"newsletter": 10, "email": 1}
    assert tiers["Leads"] == {"email": 3}


def test_format_conversions_shows_total_and_top_sources():
    lines = tr.format_conversions([
        ("Purchases", {"Direct": 7, "newsletter": 3, "Gmail": 1}),
        ("Registrations", {"newsletter": 44, "email": 8}),
        ("Leads", {}),
    ])
    assert lines[0].startswith("*Conversions*")
    assert "*Purchases*: 11" in lines[1] and "Direct 7" in lines[1]
    assert "*Registrations*: 52" in lines[2]
    assert "*Leads*: 0" in lines[3]


def test_conversions_heading_is_short():
    lines = tr.format_conversions([("Purchases", {"Direct": 1})])
    assert lines[0] == "*Conversions* (30 days)"
