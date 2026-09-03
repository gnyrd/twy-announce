#!/usr/bin/env python3
"""Daily website traffic report to Slack, from Plausible.

Sibling of daily_status_report.py (memberships and followers). This one
answers "how many people found the sites, and from where" at three scales:
yesterday, month to date, and year to date, for tiffanywoodyoga.com in full
and one line each for the studio and the Yoga Habit page.

Everything is read from the self-hosted Plausible at
analytics.tiffanywoodyoga.com through its stats API. No other source, no
state file: the numbers are whatever Plausible holds at run time, so a rerun
on the same day posts the same report.

Usage:
    python3 src/traffic_report.py            # post to Slack
    python3 src/traffic_report.py --dry-run  # print only
    python3 src/traffic_report.py --date 2026-09-02   # report as of that day
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))

from twy_paths import load_env
from slack_post import post_slack_as_reporter

MT = ZoneInfo("America/Denver")
DEFAULT_PLAUSIBLE_BASE_URL = "https://analytics.tiffanywoodyoga.com"
MAIN_SITE = "tiffanywoodyoga.com"
# JP, 2026-09-03: the traffic report goes to #status-traffic, posted as
# TWY Reporter. An earlier default channel did not exist in the workspace.
SLACK_CHANNEL = "#status-traffic"
SITE_LABELS = {
    "tiffanywoodyoga.com": "tiffanywoodyoga.com",
    "studio.tiffanywoodyoga.com": "Studio (HeyMarvelous)",
    "habit.tiffanywoodyoga.com": "Yoga Habit page",
}
# Plausible names the referrer; these are the answer engines and assistants
# that send people to a site when they cite it.
AI_SOURCES = {
    "chatgpt.com",
    "chat.openai.com",
    "openai.com",
    "perplexity.ai",
    "perplexity",
    "claude.ai",
    "gemini.google.com",
    "copilot.microsoft.com",
    "bing.com/chat",
    "you.com",
    "duckduckgo.com/aichat",
}
TOP_N = 3

Query = Callable[[dict[str, Any]], dict[str, Any]]


# ---------------------------------------------------------------------------
# Dates. Plausible counts days in the site's own timezone (Mountain), so the
# report day is "yesterday in Denver" regardless of where the job runs.
# ---------------------------------------------------------------------------


def report_day(now: datetime | None = None) -> date:
    now = now or datetime.now(MT)
    return now.astimezone(MT).date() - timedelta(days=1)


def span(start: date, end: date) -> list[str]:
    return [start.isoformat(), end.isoformat()]


def month_to_date(day: date) -> list[str]:
    return span(day.replace(day=1), day)


def previous_month_same_span(day: date) -> list[str]:
    first = day.replace(day=1)
    last_of_previous = first - timedelta(days=1)
    start = last_of_previous.replace(day=1)
    end_day = min(day.day, last_of_previous.day)
    return span(start, start.replace(day=end_day))


def previous_month_full(day: date) -> list[str]:
    first = day.replace(day=1)
    last_of_previous = first - timedelta(days=1)
    return span(last_of_previous.replace(day=1), last_of_previous)


def year_to_date(day: date) -> list[str]:
    return span(day.replace(month=1, day=1), day)


def previous_year_same_span(day: date) -> list[str]:
    try:
        end = day.replace(year=day.year - 1)
    except ValueError:  # 29 February
        end = day.replace(year=day.year - 1, day=28)
    return span(end.replace(month=1, day=1), end)


def same_weekday_last_week(day: date) -> list[str]:
    last = day - timedelta(days=7)
    return span(last, last)


# ---------------------------------------------------------------------------
# Plausible
# ---------------------------------------------------------------------------


def plausible_query_from_env() -> Query:
    api_key = os.getenv("PLAUSIBLE_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("PLAUSIBLE_API_KEY is not set")
    base_url = os.getenv("PLAUSIBLE_BASE_URL", DEFAULT_PLAUSIBLE_BASE_URL).rstrip("/")

    def post_query(body: dict[str, Any]) -> dict[str, Any]:
        response = requests.post(
            f"{base_url}/api/v2/query",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=body,
            timeout=30,
        )
        if response.status_code >= 400:
            raise RuntimeError(f"Plausible query failed: {response.status_code} {response.text[:200]}")
        return response.json()

    return post_query


def site_ids_from_env() -> list[str]:
    configured = [s.strip() for s in os.getenv("PLAUSIBLE_SITE_IDS", "").split(",") if s.strip()]
    return configured or [MAIN_SITE]


def totals(query: Query, site_id: str, date_range: list[str]) -> dict[str, int]:
    payload = query(
        {"site_id": site_id, "metrics": ["visitors", "pageviews"], "date_range": date_range}
    )
    metrics = (payload.get("results") or [{}])[0].get("metrics") or [0, 0]
    return {"visitors": int(metrics[0] or 0), "pageviews": int(metrics[1] or 0)}


def breakdown(
    query: Query, site_id: str, date_range: list[str], dimension: str, limit: int = 20
) -> list[tuple[str, int]]:
    payload = query(
        {
            "site_id": site_id,
            "metrics": ["visitors"],
            "date_range": date_range,
            "dimensions": [dimension],
            "order_by": [["visitors", "desc"]],
            "pagination": {"limit": limit},
        }
    )
    rows = []
    for row in payload.get("results") or []:
        dimensions = row.get("dimensions") or [""]
        metrics = row.get("metrics") or [0]
        rows.append((str(dimensions[0]), int(metrics[0] or 0)))
    return rows


def channel_visitors(query: Query, site_id: str, date_range: list[str]) -> dict[str, int]:
    return dict(breakdown(query, site_id, date_range, "visit:channel"))


def ai_visitors(sources: list[tuple[str, int]]) -> int:
    return sum(count for source, count in sources if source.strip().lower() in AI_SOURCES)


def channel_metrics(query: Query, site_id: str, day: date) -> dict[str, int]:
    """Search and AI visitors across the four windows the deltas need.

    Search is Plausible's Organic Search channel; AI is the answer-engine
    referrers in ai_visitors. Same day / same weekday last week / month to
    date / previous month same span, so Search and AI each get a week and a
    month delta like the visitor line.
    """
    def counts(date_range: list[str]) -> tuple[int, int]:
        sources = breakdown(query, site_id, date_range, "visit:source")
        search = channel_visitors(query, site_id, date_range).get("Organic Search", 0)
        return search, ai_visitors(sources)

    search_day, ai_day = counts(span(day, day))
    search_week, ai_week = counts(same_weekday_last_week(day))
    search_mtd, ai_mtd = counts(month_to_date(day))
    search_pm, ai_pm = counts(previous_month_same_span(day))
    return {
        "search_day": search_day, "search_week": search_week,
        "search_mtd": search_mtd, "search_pm": search_pm,
        "ai_day": ai_day, "ai_week": ai_week,
        "ai_mtd": ai_mtd, "ai_pm": ai_pm,
    }


def collect(query: Query, site_id: str, day: date) -> dict[str, Any]:
    yesterday = span(day, day)
    mtd = month_to_date(day)
    ytd = year_to_date(day)
    return {
        "site_id": site_id,
        "day": day.isoformat(),
        "yesterday": totals(query, site_id, yesterday),
        "last_week_same_day": totals(query, site_id, same_weekday_last_week(day)),
        "pages": breakdown(query, site_id, yesterday, "event:page"),
        "mtd": totals(query, site_id, mtd),
        "previous_month_same_span": totals(query, site_id, previous_month_same_span(day)),
        "previous_month_full": totals(query, site_id, previous_month_full(day)),
        "ytd": totals(query, site_id, ytd),
        "previous_year_same_span": totals(query, site_id, previous_year_same_span(day)),
        **channel_metrics(query, site_id, day),
    }


def collect_brief(query: Query, site_id: str, day: date) -> dict[str, Any]:
    return {
        "site_id": site_id,
        "yesterday": totals(query, site_id, span(day, day)),
        "last_week_same_day": totals(query, site_id, same_weekday_last_week(day)),
        "mtd": totals(query, site_id, month_to_date(day)),
        "previous_month_same_span": totals(query, site_id, previous_month_same_span(day)),
        **channel_metrics(query, site_id, day),
    }


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------


def delta(current: int, previous: int) -> str:
    """Signed change with percent, or a raw `+N` when there is no baseline.

    Returns "" when nothing moved from nothing (both zero), so the caller
    drops the period rather than printing a meaningless zero.
    """
    if previous == 0:
        return "" if current == 0 else f"+{current}"
    change = current - previous
    pct = round(100 * change / previous)
    sign = "+" if change >= 0 else ""
    return f"{sign}{change} ({sign}{pct}%)"


def top_list(rows: list[tuple[str, int]], n: int = TOP_N) -> str:
    kept = [(name or "(direct)", count) for name, count in rows if count > 0][:n]
    if not kept:
        return "none"
    return ", ".join(f"{name} {count}" for name, count in kept)


def source_label(source: str) -> str:
    return "Direct" if source in ("Direct / None", "") else source


def pipe(parts: list[str]) -> str:
    return "  |  ".join(part for part in parts if part)


SHORT_LABELS = {
    "tiffanywoodyoga.com": "main",
    "studio.tiffanywoodyoga.com": "studio",
    "habit.tiffanywoodyoga.com": "habit",
}


def short_label(site_id: str) -> str:
    return SHORT_LABELS.get(site_id, site_id)


def period_delta(current: int, previous: int, label: str) -> str:
    """`label: <delta>`, or empty when nothing moved from nothing."""
    text = delta(current, previous)
    return f"{label}: {text}" if text else ""


def delta_line(record: dict[str, Any], *, weekly: bool) -> str:
    parts = []
    if weekly:
        parts.append(
            period_delta(
                record["yesterday"]["visitors"],
                record["last_week_same_day"]["visitors"],
                "week",
            )
        )
    parts.append(
        period_delta(
            record["mtd"]["visitors"],
            record["previous_month_same_span"]["visitors"],
            "month",
        )
    )
    body = pipe(parts)
    return f"    \u0394 {body}" if body else ""


def sub_metric(label: str, day: int, mtd: int, pm: int) -> list[str]:
    """A bold `*Label*: <day>` line and its month delta, or nothing.

    Suppressed when the channel has no traffic (day and month both zero).
    Month only: a channel's day-to-day counts are too small to read
    week over week.
    """
    if not day:
        return []
    lines = [f"    *{label}*: {day}"]
    body = period_delta(mtd, pm, "month")
    if body:
        lines.append(f"        \u0394 {body}")
    return lines


def format_site(record: dict[str, Any], *, with_top: bool, weekly: bool) -> list[str]:
    """One site block: headline visitors, then only the lines that have data.

    Mirrors the daily Reports post: a value and a labeled delta line, no
    zeros (JP, 2026-09-03). The week delta appears on the main site only;
    every other line is month over month. Search and AI are bold
    sub-metrics that show once a site has that traffic.
    """
    label = short_label(record["site_id"]).capitalize()
    lines = [
        f"*{label}*: {record['yesterday']['visitors']}",
        delta_line(record, weekly=weekly),
    ]
    if with_top:
        lines.append(f"    top: {top_list(record['pages'])}")
    lines += sub_metric("Search", record["search_day"], record["search_mtd"], record["search_pm"])
    lines += sub_metric("AI", record["ai_day"], record["ai_mtd"], record["ai_pm"])
    return [line for line in lines if line]


def format_report(main: dict[str, Any], briefs: list[dict[str, Any]], day: date) -> str:
    blocks = [format_site(main, with_top=True, weekly=True)]
    blocks += [format_site(brief, with_top=False, weekly=False) for brief in briefs]
    return "\n\n".join("\n".join(block) for block in blocks)


def ordinal(n: int) -> str:
    if 11 <= n % 100 <= 13:
        return "th"
    return {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")


def build_report(query: Query, site_ids: list[str], day: date) -> str:
    main_id = MAIN_SITE if MAIN_SITE in site_ids else site_ids[0]
    main = collect(query, main_id, day)
    briefs = [collect_brief(query, site_id, day) for site_id in site_ids if site_id != main_id]
    return format_report(main, briefs, day)


def sample_data() -> tuple[dict[str, Any], list[dict[str, Any]], date]:
    """Invented numbers where every field is non-zero, for --sample.

    Not real traffic: this exists only to show the maximal shape of the
    report (week and month deltas, search, AI and top pages on every site).
    """
    def block(site_id: str, y: int, lw: int, m: int, pms: int) -> dict[str, Any]:
        return {
            "site_id": site_id,
            "yesterday": {"visitors": y, "pageviews": y * 3},
            "last_week_same_day": {"visitors": lw, "pageviews": lw * 3},
            "mtd": {"visitors": m, "pageviews": m * 3},
            "previous_month_same_span": {"visitors": pms, "pageviews": pms * 3},
            "search_day": 4, "search_week": 3, "search_mtd": 37, "search_pm": 25,
            "ai_day": 2, "ai_week": 1, "ai_mtd": 9, "ai_pm": 4,
        }

    main = block("tiffanywoodyoga.com", 48, 39, 512, 430)
    main["pages"] = [("/", 22), ("/membership/", 9), ("/blog/", 6)]
    studio = block("studio.tiffanywoodyoga.com", 31, 26, 288, 240)
    habit = block("habit.tiffanywoodyoga.com", 12, 7, 64, 41)
    return main, [studio, habit], date(2026, 9, 2)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="TWY daily website traffic report")
    parser.add_argument("--dry-run", action="store_true", help="Print the report, do not post")
    parser.add_argument("--date", help="Report day (YYYY-MM-DD); default is yesterday in Mountain time")
    parser.add_argument("--sample", action="store_true", help="Print a fully-populated sample report and exit")
    args = parser.parse_args(argv)
    if args.sample:
        print(format_report(*sample_data()))
        return 0
    load_env()
    day = date.fromisoformat(args.date) if args.date else report_day()
    try:
        text = build_report(plausible_query_from_env(), site_ids_from_env(), day)
    except Exception as exc:  # the twy-run wrapper turns a non-zero exit into a Slack alert
        print(f"traffic report failed: {exc}", file=sys.stderr)
        return 1
    print(text)
    if args.dry_run:
        print("\n[DRY RUN] not posted")
        return 0
    post_slack_as_reporter(os.getenv("TRAFFIC_SLACK_CHANNEL", SLACK_CHANNEL), text)
    print("posted")
    return 0


if __name__ == "__main__":
    sys.exit(main())
