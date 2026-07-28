#!/usr/bin/env python3
"""Build a weekly TWY social growth review from daily snapshots."""

from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
from typing import Any

from twy_paths import data_root as default_data_root
from twy_paths import load_env


DEFAULT_DAYS = 7


def parse_date(value: str) -> date:
    return date.fromisoformat(value)


def read_json(path: Path) -> Any:
    return json.loads(path.read_text())


def load_daily_snapshots(snapshot_dir: Path, *, week_end: date, days: int = DEFAULT_DAYS) -> list[dict[str, Any]]:
    week_start = week_end - timedelta(days=days - 1)
    snapshots: list[dict[str, Any]] = []
    for path in sorted(snapshot_dir.glob("*.json")):
        if path.name.startswith("."):
            continue
        try:
            snapshot_date = parse_date(path.stem)
        except ValueError:
            continue
        if snapshot_date < week_start or snapshot_date > week_end:
            continue
        try:
            snapshot = read_json(path)
        except Exception:
            continue
        snapshots.append(snapshot)
    return snapshots


def _summary(snapshot: dict[str, Any]) -> dict[str, Any]:
    return snapshot.get("summary") or {}


def _value(snapshot: dict[str, Any], key: str) -> int | float | None:
    value = _summary(snapshot).get(key)
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return value
    return None


def _delta(snapshots: list[dict[str, Any]], key: str) -> dict[str, int | float | None]:
    values = [_value(snapshot, key) for snapshot in snapshots]
    values = [value for value in values if value is not None]
    if not values:
        return {"start": None, "end": None, "delta": None}
    start = values[0]
    end = values[-1]
    return {"start": start, "end": end, "delta": end - start}


def _sum(snapshots: list[dict[str, Any]], key: str) -> int | float:
    return sum(_value(snapshot, key) or 0 for snapshot in snapshots)


def _unique_summary_values(snapshots: list[dict[str, Any]], key: str) -> list[str]:
    seen: list[str] = []
    for snapshot in snapshots:
        values = _summary(snapshot).get(key) or []
        if not isinstance(values, list):
            continue
        for value in values:
            text = str(value or "").strip()
            if text and text not in seen:
                seen.append(text)
    return seen


def _recommendations(report: dict[str, Any]) -> list[str]:
    if report["status"] == "insufficient_data":
        return ["Collect at least two daily snapshots before changing the campaign."]

    metrics = report["metrics"]
    funnel = metrics["landing_page"]
    follower_delta = metrics["instagram_followers"]["delta"]
    subscriber_delta = metrics["email_subscribers"]["delta"]
    habit_delta = metrics["next_habit_registrations"]["delta"]
    upcoming = report["campaigns"]["upcoming_variants"]

    recommendations: list[str] = []
    if upcoming and funnel["visitors"] == 0:
        recommendations.append("Let the scheduled campaign publish before changing copy; no landing traffic was measured this week.")
    if funnel["visitors"] > 0 and funnel["habit_register_clicks"] == 0:
        recommendations.append("Review the Instagram bio link and landing-page CTA path; visits did not become register clicks.")
    if funnel["habit_register_clicks"] > 0 and habit_delta is not None and habit_delta <= 0:
        recommendations.append("Inspect the HeyMarvelous registration handoff; register clicks did not increase Habit registrations.")
    if follower_delta is not None and follower_delta < 0:
        recommendations.append("Review the posts from the down-follower week before changing the next variant.")
    if subscriber_delta is not None and subscriber_delta <= 0 and funnel["habit_signup_success"] > 0:
        recommendations.append("Check SendGrid signup attribution; signup successes did not move subscriber count.")
    if not recommendations:
        recommendations.append("Keep the current campaign running until the next weekly review has published-post evidence.")
    return recommendations


def build_weekly_review(snapshots: list[dict[str, Any]], *, week_end: date, days: int = DEFAULT_DAYS) -> dict[str, Any]:
    week_start = week_end - timedelta(days=days - 1)
    snapshots = sorted(snapshots, key=lambda item: item.get("date") or item.get("captured_at") or "")
    report: dict[str, Any] = {
        "status": "ok" if len(snapshots) >= 2 else "insufficient_data",
        "week_start": week_start.isoformat(),
        "week_end": week_end.isoformat(),
        "snapshot_count": len(snapshots),
        "first_snapshot_date": snapshots[0].get("date") if snapshots else None,
        "last_snapshot_date": snapshots[-1].get("date") if snapshots else None,
        "metrics": {
            "instagram_followers": _delta(snapshots, "instagram_followers"),
            "email_subscribers": _delta(snapshots, "email_subscribers"),
            "next_habit_registrations": _delta(snapshots, "next_habit_registrations"),
            "landing_page": {
                "visitors": _sum(snapshots, "landing_day_visitors"),
                "pageviews": _sum(snapshots, "landing_day_pageviews"),
                "habit_register_clicks": _sum(snapshots, "habit_register_clicks_day"),
                "habit_signup_success": _sum(snapshots, "habit_signup_success_day"),
            },
        },
        "campaigns": {
            "recent_variants": _unique_summary_values(snapshots, "recent_campaign_variants"),
            "upcoming_variants": _unique_summary_values(snapshots, "upcoming_campaign_variants"),
        },
    }
    report["recommendations"] = _recommendations(report)
    return report


def save_weekly_review(report: dict[str, Any], *, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"{report['week_end']}.json"
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description="Build TWY weekly social growth review")
    parser.add_argument("--dry-run", action="store_true", help="Print JSON without writing it")
    parser.add_argument("--week-end", help="Week-end date, YYYY-MM-DD. Default is today in UTC.")
    parser.add_argument("--days", type=int, default=DEFAULT_DAYS)
    args = parser.parse_args()

    load_env()
    week_end = parse_date(args.week_end) if args.week_end else datetime.now(timezone.utc).date()
    data_dir = default_data_root()
    snapshots = load_daily_snapshots(data_dir / "social_growth", week_end=week_end, days=args.days)
    report = build_weekly_review(snapshots, week_end=week_end, days=args.days)
    if args.dry_run:
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0
    destination = save_weekly_review(report, output_dir=data_dir / "social_growth" / "weekly")
    print(f"Saved weekly social growth review to {destination}")
    if report["status"] == "insufficient_data":
        print("Weekly social growth review has insufficient data.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
