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
# TWY Reporter. #twy-status, the old default, does not exist.
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


def collect(query: Query, site_id: str, day: date) -> dict[str, Any]:
    yesterday = span(day, day)
    mtd = month_to_date(day)
    ytd = year_to_date(day)
    sources = breakdown(query, site_id, yesterday, "visit:source")
    channels_day = channel_visitors(query, site_id, yesterday)
    channels_mtd = channel_visitors(query, site_id, mtd)
    return {
        "site_id": site_id,
        "day": day.isoformat(),
        "yesterday": totals(query, site_id, yesterday),
        "last_week_same_day": totals(query, site_id, same_weekday_last_week(day)),
        "sources": sources,
        "pages": breakdown(query, site_id, yesterday, "event:page"),
        "search_day": channels_day.get("Organic Search", 0),
        "ai_day": ai_visitors(sources),
        "mtd": totals(query, site_id, mtd),
        "previous_month_same_span": totals(query, site_id, previous_month_same_span(day)),
        "previous_month_full": totals(query, site_id, previous_month_full(day)),
        "search_mtd": channels_mtd.get("Organic Search", 0),
        "ai_mtd": ai_visitors(breakdown(query, site_id, mtd, "visit:source")),
        "ytd": totals(query, site_id, ytd),
        "previous_year_same_span": totals(query, site_id, previous_year_same_span(day)),
    }


def collect_brief(query: Query, site_id: str, day: date) -> dict[str, Any]:
    return {
        "site_id": site_id,
        "yesterday": totals(query, site_id, span(day, day)),
        "mtd": totals(query, site_id, month_to_date(day)),
        "previous_month_same_span": totals(query, site_id, previous_month_same_span(day)),
    }


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------


def delta(current: int, previous: int) -> str:
    if previous == 0:
        return "no earlier data" if current == 0 else "no earlier data to compare"
    change = current - previous
    pct = round(100 * change / previous)
    sign = "+" if change >= 0 else ""
    return f"{sign}{change} ({sign}{pct}%)"


def top_list(rows: list[tuple[str, int]], n: int = TOP_N) -> str:
    kept = [(name or "(direct)", count) for name, count in rows if count > 0][:n]
    if not kept:
        return "none"
    return "  |  ".join(f"{name} {count}" for name, count in kept)


def source_label(source: str) -> str:
    return "Direct" if source in ("Direct / None", "") else source


def pipe(parts: list[str]) -> str:
    return "  |  ".join(parts)


def format_report(main: dict[str, Any], briefs: list[dict[str, Any]], day: date) -> str:
    """One label per line with its number, deltas on an indented line.

    Matches the daily membership report JP already reads: bold label, colon,
    the number, then a Delta line. No sentences, no bullets, no footer.
    """
    y = main["yesterday"]
    lw = main["last_week_same_day"]
    mtd = main["mtd"]
    pm_same = main["previous_month_same_span"]
    pm_full = main["previous_month_full"]
    ytd = main["ytd"]
    py = main["previous_year_same_span"]
    month_name = day.strftime("%B")
    previous_month_name = (day.replace(day=1) - timedelta(days=1)).strftime("%B")

    lines = [
        f"*{day.strftime('%A, %B %-d')}* ({SITE_LABELS.get(main['site_id'], main['site_id'])})",
        "",
        f"*Visitors*: {y['visitors']}",
        f"    \u0394 same day last week: {delta(y['visitors'], lw['visitors'])}",
        f"*Pageviews*: {y['pageviews']}",
        "",
        f"*{month_name}*: {mtd['visitors']}",
        "    "
        + pipe(
            [
                f"\u0394 same span in {previous_month_name}: {delta(mtd['visitors'], pm_same['visitors'])}",
                f"all of {previous_month_name}: {pm_full['visitors']}",
            ]
        ),
        f"*{day.year}*: {ytd['visitors']}",
    ]
    if py["visitors"]:
        lines.append(
            f"    \u0394 same span in {day.year - 1}: {delta(ytd['visitors'], py['visitors'])}"
        )
    lines += [
        "",
        "*Search*: " + pipe([f"day: {main['search_day']}", f"month: {main['search_mtd']}"]),
        "*AI tools*: " + pipe([f"day: {main['ai_day']}", f"month: {main['ai_mtd']}"]),
        "",
        f"*Top pages*: {top_list(main['pages'])}",
        f"*Top sources*: {top_list([(source_label(s), c) for s, c in main['sources']])}",
    ]
    if briefs:
        lines.append("")
        for brief in briefs:
            label = SITE_LABELS.get(brief["site_id"], brief["site_id"])
            lines.append(
                f"*{label}*: "
                + pipe(
                    [
                        f"day: {brief['yesterday']['visitors']}",
                        f"month: {brief['mtd']['visitors']}",
                    ]
                )
            )
    return "\n".join(lines)


def ordinal(n: int) -> str:
    if 11 <= n % 100 <= 13:
        return "th"
    return {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")


def build_report(query: Query, site_ids: list[str], day: date) -> str:
    main_id = MAIN_SITE if MAIN_SITE in site_ids else site_ids[0]
    main = collect(query, main_id, day)
    briefs = [collect_brief(query, site_id, day) for site_id in site_ids if site_id != main_id]
    return format_report(main, briefs, day)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="TWY daily website traffic report")
    parser.add_argument("--dry-run", action="store_true", help="Print the report, do not post")
    parser.add_argument("--date", help="Report day (YYYY-MM-DD); default is yesterday in Mountain time")
    args = parser.parse_args(argv)
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
