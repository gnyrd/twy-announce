#!/usr/bin/env python3
"""Build a weekly TWY social growth review from daily snapshots."""

from __future__ import annotations

import argparse
import calendar
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import json
import os
import re
import sys
import uuid
from pathlib import Path
from typing import Any

import requests
from twy_paths import data_root as default_data_root
from twy_paths import load_env


DEFAULT_DAYS = 7
MIN_POST_AGE_HOURS = 24
MIN_WEBSITE_VISITORS_FOR_RECOMMENDATION = 10
SLACK_STATE_FILE = ".weekly_slack_state.json"
CLASSES_API = "http://localhost:5003"

WEBSITE_PROPERTIES = {
    "main": {
        "site_id": "tiffanywoodyoga.com",
        "role": "discovery",
    },
    "studio": {
        "site_id": "studio.tiffanywoodyoga.com",
        "role": "customer_service",
    },
    "habit": {
        "site_id": "habit.tiffanywoodyoga.com",
        "role": "campaign_conversion",
    },
}


def parse_date(value: str) -> date:
    return date.fromisoformat(value)


def read_json(path: Path) -> Any:
    return json.loads(path.read_text())


def load_daily_snapshots(snapshot_dir: Path, *, week_end: date, days: int = DEFAULT_DAYS) -> list[dict[str, Any]]:
    snapshots: list[dict[str, Any]] = []
    for path in sorted(snapshot_dir.glob("*.json")):
        if path.name.startswith("."):
            continue
        try:
            snapshot_date = parse_date(path.stem)
        except ValueError:
            continue
        if snapshot_date > week_end:
            continue
        try:
            snapshot = read_json(path)
        except Exception:
            continue
        snapshots.append(snapshot)
    return snapshots


def _snapshot_date(snapshot: dict[str, Any]) -> date | None:
    value = snapshot.get("date") or snapshot.get("captured_at")
    if not value:
        return None
    try:
        return parse_date(str(value)[:10])
    except ValueError:
        return None


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


def _metric(metrics: dict[str, Any], key: str) -> float:
    value = metrics.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    return float(value)


def _post_date(row: dict[str, Any]) -> date | None:
    value = str(row.get("scheduled_for") or "").strip()
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def _parse_datetime(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def _campaign_metadata(snapshots: list[dict[str, Any]]) -> dict[str, dict[str, str]]:
    metadata: dict[str, dict[str, str]] = {}
    for snapshot in snapshots:
        for row in (snapshot.get("campaigns") or {}).get("posts") or []:
            post_id = str(row.get("zernio_post_id") or "").strip()
            campaign = str(row.get("campaign") or "").strip()
            variant = str(row.get("ctaVariant") or "").strip()
            if not post_id or not campaign or not variant:
                continue
            metadata[post_id] = {
                "campaign": campaign,
                "ctaVariant": variant,
                "habitTargetDate": str(row.get("habitTargetDate") or "").strip(),
            }
    return metadata


def _post_rows(
    snapshots: list[dict[str, Any]],
    *,
    week_start: date,
    week_end: date,
) -> list[dict[str, Any]]:
    campaigns = _campaign_metadata(snapshots)
    captured_times = [
        parsed
        for parsed in (_parse_datetime(snapshot.get("captured_at")) for snapshot in snapshots)
        if parsed is not None
    ]
    analysis_at = max(captured_times) if captured_times else datetime.now(timezone.utc)
    latest: dict[str, dict[str, Any]] = {}
    for snapshot in sorted(snapshots, key=lambda item: item.get("captured_at") or item.get("date") or ""):
        for row in ((snapshot.get("zernio") or {}).get("analytics") or {}).get("posts") or []:
            post_id = str(row.get("zernio_post_id") or "").strip()
            scheduled_date = _post_date(row)
            if not post_id or scheduled_date is None:
                continue
            if scheduled_date < week_start or scheduled_date > week_end:
                continue
            scheduled_at = _parse_datetime(row.get("scheduled_for"))
            if scheduled_at is None:
                continue
            age_hours = (analysis_at - scheduled_at).total_seconds() / 3600
            if age_hours < MIN_POST_AGE_HOURS:
                continue
            metrics = row.get("metrics")
            if not isinstance(metrics, dict):
                continue
            latest[post_id] = {
                **row,
                **campaigns.get(post_id, {}),
            }
    return sorted(
        latest.values(),
        key=lambda row: (
            _metric(row.get("metrics") or {}, "reach"),
            str(row.get("scheduled_for") or ""),
        ),
        reverse=True,
    )


def _growth_actions(metrics: dict[str, Any]) -> int:
    return int(
        sum(
            _metric(metrics, key)
            for key in ("follows", "saves", "shares", "clicks")
        )
    )


def _aggregate_posts(posts: list[dict[str, Any]]) -> dict[str, Any]:
    totals = {
        key: int(sum(_metric(row.get("metrics") or {}, key) for row in posts))
        for key in (
            "reach",
            "views",
            "impressions",
            "likes",
            "comments",
            "follows",
            "saves",
            "shares",
            "clicks",
        )
    }
    totals["growth_actions"] = sum(_growth_actions(row.get("metrics") or {}) for row in posts)
    count = len(posts)
    averages = {
        "reach": round(totals["reach"] / count, 2) if count else 0,
        "views": round(totals["views"] / count, 2) if count else 0,
        "engagement_rate": round(
            sum(_metric(row.get("metrics") or {}, "engagementRate") for row in posts) / count,
            2,
        )
        if count
        else 0,
        "watch_time_seconds": round(
            sum(_metric(row.get("metrics") or {}, "igReelsAvgWatchTime") for row in posts)
            / count
            / 1000,
            2,
        )
        if count
        else 0,
        "growth_actions": round(totals["growth_actions"] / count, 2) if count else 0,
    }
    return {
        "post_count": count,
        "totals": totals,
        "averages": averages,
    }


def _top_post(posts: list[dict[str, Any]], *, key: str) -> dict[str, Any] | None:
    if not posts:
        return None
    if key == "growth_actions":
        return max(posts, key=lambda row: _growth_actions(row.get("metrics") or {}))
    return max(posts, key=lambda row: _metric(row.get("metrics") or {}, key))


def _comparison(variant: dict[str, Any], baseline: dict[str, Any]) -> dict[str, Any]:
    variant_avg = variant["averages"]
    baseline_avg = baseline["averages"]
    deltas = {
        "reach_delta": round(variant_avg["reach"] - baseline_avg["reach"], 2),
        "engagement_rate_delta": round(
            variant_avg["engagement_rate"] - baseline_avg["engagement_rate"],
            2,
        ),
        "watch_time_seconds_delta": round(
            variant_avg["watch_time_seconds"] - baseline_avg["watch_time_seconds"],
            2,
        ),
        "growth_actions_delta": round(
            variant_avg["growth_actions"] - baseline_avg["growth_actions"],
            2,
        ),
    }
    positive = sum(value > 0 for value in deltas.values())
    negative = sum(value < 0 for value in deltas.values())
    if positive >= 3:
        assessment = "better"
    elif negative >= 3:
        assessment = "weaker"
    else:
        assessment = "mixed"
    return {
        **deltas,
        "assessment": assessment,
        "baseline_post_count": baseline["post_count"],
    }


def _campaign_performance(posts: list[dict[str, Any]]) -> dict[str, Any]:
    baseline_posts = [row for row in posts if not row.get("campaign")]
    baseline = _aggregate_posts(baseline_posts)
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in posts:
        campaign = str(row.get("campaign") or "").strip()
        variant = str(row.get("ctaVariant") or "").strip()
        if campaign and variant:
            grouped.setdefault(f"{campaign}:{variant}", []).append(row)

    variants: list[dict[str, Any]] = []
    for key, rows in sorted(grouped.items()):
        aggregate = _aggregate_posts(rows)
        item = {
            "key": key,
            "campaign": rows[0]["campaign"],
            "ctaVariant": rows[0]["ctaVariant"],
            "habitTargetDate": rows[0].get("habitTargetDate"),
            **aggregate,
        }
        item["comparison_to_baseline"] = (
            _comparison(item, baseline)
            if baseline["post_count"]
            else {
                "assessment": "no_baseline",
                "baseline_post_count": 0,
            }
        )
        variants.append(item)
    return {
        "baseline": baseline,
        "variants": variants,
    }


def _post_performance(posts: list[dict[str, Any]]) -> dict[str, Any]:
    aggregate = _aggregate_posts(posts)
    return {
        "posts_analyzed": aggregate["post_count"],
        "totals": aggregate["totals"],
        "averages": aggregate["averages"],
        "top_by_reach": _top_post(posts, key="reach"),
        "top_by_engagement": _top_post(posts, key="engagementRate"),
        "top_by_watch_time": _top_post(posts, key="igReelsAvgWatchTime"),
        "top_by_growth_actions": _top_post(posts, key="growth_actions"),
        "posts": posts,
    }


def _website_property(snapshot: dict[str, Any], role: str) -> dict[str, Any] | None:
    properties = ((snapshot.get("websites") or {}).get("properties") or {})
    property_snapshot = properties.get(role)
    if isinstance(property_snapshot, dict):
        return property_snapshot
    if role != "habit":
        return None
    legacy_property = ((snapshot.get("landing_page") or {}).get("plausible") or {})
    return legacy_property if isinstance(legacy_property, dict) else None


def _website_captured_at(snapshot: dict[str, Any], property_snapshot: dict[str, Any]) -> str | None:
    captured_at = property_snapshot.get("captured_at") or snapshot.get("captured_at")
    return str(captured_at) if captured_at else None


def _daily_website_trend(snapshots: list[dict[str, Any]], role: str) -> list[dict[str, Any]]:
    trend: list[dict[str, Any]] = []
    for snapshot in snapshots:
        property_snapshot = _website_property(snapshot, role)
        metrics = (property_snapshot or {}).get("metrics") or {}
        day = metrics.get("day") or {}
        if property_snapshot is None or property_snapshot.get("status") != "ok" or not isinstance(day, dict):
            continue
        trend.append(
            {
                "date": snapshot.get("date"),
                "visitors": day.get("visitors"),
                "visits": day.get("visits"),
                "pageviews": day.get("pageviews"),
                "events": day.get("events"),
            }
        )
    return trend


def _website_performance_for_role(
    history: list[dict[str, Any]],
    *,
    trend_snapshots: list[dict[str, Any]],
    role: str,
) -> dict[str, Any]:
    definition = WEBSITE_PROPERTIES[role]
    latest_property: dict[str, Any] | None = None
    last_good_property: dict[str, Any] | None = None
    last_good_snapshot: dict[str, Any] | None = None
    for snapshot in history:
        property_snapshot = _website_property(snapshot, role)
        if property_snapshot is None:
            continue
        latest_property = property_snapshot
        if property_snapshot.get("status") == "ok" and isinstance(property_snapshot.get("metrics"), dict):
            last_good_property = property_snapshot
            last_good_snapshot = snapshot

    status = "not_collected"
    stale = False
    failure_message = None
    if latest_property is not None:
        status = str(latest_property.get("status") or "unknown")
    if latest_property is not None and status == "error" and last_good_property is not None:
        status = "stale"
        stale = True
        failure_message = str(latest_property.get("error") or "") or None

    source_property = (
        last_good_property
        if status in {"ok", "stale"}
        else latest_property or {}
    )
    metrics = source_property.get("metrics") or {}
    latest_7_days = metrics.get("last_7_days") or {}
    latest_30_days = metrics.get("last_30_days") or {}
    result: dict[str, Any] = {
        **definition,
        "status": status,
        "stale": stale,
        "latest_7_days": latest_7_days,
        "latest_30_days": latest_30_days,
        "daily_trend": _daily_website_trend(trend_snapshots, role),
        "top_sources": latest_7_days.get("sources") or [],
        "top_entry_pages": latest_7_days.get("entry_pages") or [],
        "utm_sources": latest_7_days.get("utm_sources") or [],
        "tracked_events": latest_7_days.get("tracked_events") or [],
    }
    if stale:
        result["failure_message"] = failure_message
        result["last_good_at"] = _website_captured_at(last_good_snapshot or {}, last_good_property or {})
    elif status != "ok" and latest_property is not None:
        result["failure_message"] = str(latest_property.get("error") or "") or None
    if role == "habit":
        result["funnel_events"] = latest_7_days.get("funnel_events") or {}
        result["funnel_by_vector"] = latest_7_days.get("funnel_by_vector") or []
    return result


def _website_performance(
    history: list[dict[str, Any]],
    *,
    trend_snapshots: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        role: _website_performance_for_role(
            history,
            trend_snapshots=trend_snapshots,
            role=role,
        )
        for role in WEBSITE_PROPERTIES
    }


def _website_visitors(property_performance: dict[str, Any]) -> int | float | None:
    visitors = (property_performance.get("latest_7_days") or {}).get("visitors")
    if isinstance(visitors, bool) or not isinstance(visitors, (int, float)):
        return None
    return visitors


def _first_dimension(rows: list[dict[str, Any]]) -> str | None:
    if not rows:
        return None
    dimensions = rows[0].get("dimensions") or []
    if not dimensions:
        return None
    text = str(dimensions[0] or "").strip()
    return text or None


def _habit_class_date_for_month(year: int, month: int) -> date | None:
    """The month's Habit class date from the classes API, or None when no plan.

    Mirrors the landing page's own honesty rule: a month with no Habit plan has
    no Habit date, never a guessed second Saturday. Raises RequestException
    when the API is unreachable so the caller can tell unknown from absent.
    """
    last_day = calendar.monthrange(year, month)[1]
    response = requests.get(
        f"{CLASSES_API}/api/plans",
        params={
            "from": f"{year:04d}-{month:02d}-01",
            "to": f"{year:04d}-{month:02d}-{last_day:02d}",
        },
        timeout=10,
    )
    response.raise_for_status()
    for plan in response.json():
        if plan.get("class_type") == "Habit":
            return date.fromisoformat(plan["date"])
    return None


def _habit_pre_class_days(week_end: date, days: int = DEFAULT_DAYS) -> tuple[int, int] | None:
    """(pre_class_days, total_days) for the rolling window, or None when unknown.

    A day counts as pre_class when the class the landing page would SHOW that
    day had not yet happened, mirroring yoga-habit's fetch_habit_plan: this
    month's class until its day passes, then next month's class once that
    month's plan exists. Days between a finished class and the authoring of the
    next month's plan are post_class, and the page hides Register on them by
    design. The class day itself counts as pre_class (registration runs until
    the 9:00 MT start). None means the classes API was unreachable, so the
    caller must not claim either state.
    """
    dates: dict[tuple[int, int], date | None] = {}

    def month_date(year: int, month: int) -> date | None:
        key = (year, month)
        if key not in dates:
            dates[key] = _habit_class_date_for_month(year, month)
        return dates[key]

    pre = 0
    try:
        for offset in range(days):
            day = week_end - timedelta(days=days - 1 - offset)
            shown = month_date(day.year, day.month)
            if shown is not None and day <= shown:
                pre += 1
                continue
            next_month = (day.month % 12) + 1
            next_year = day.year + (1 if next_month == 1 else 0)
            if month_date(next_year, next_month) is not None:
                pre += 1
    except requests.RequestException as exc:
        print(f"[weekly] classes API unreachable for page-state split: {exc}", file=sys.stderr)
        return None
    return pre, days


def _website_recommendations(website_performance: dict[str, Any], week_end: date) -> list[str]:
    recommendations: list[str] = []
    main = website_performance["main"]
    main_visitors = _website_visitors(main)
    if main["status"] == "ok" and main_visitors is not None and main_visitors >= MIN_WEBSITE_VISITORS_FOR_RECOMMENDATION:
        source = _first_dimension(main["top_sources"])
        entry_page = _first_dimension(main["top_entry_pages"])
        if source and entry_page:
            recommendations.append(
                f"Main discovery recorded {main_visitors:g} rolling 7-day unique visitors. Review {source} traffic to {entry_page} before changing acquisition emphasis."
            )

    habit = website_performance["habit"]
    habit_visitors = _website_visitors(habit)
    register_clicks = (habit.get("funnel_events") or {}).get("Habit Register Click")
    if (
        habit["status"] == "ok"
        and habit_visitors is not None
        and habit_visitors >= MIN_WEBSITE_VISITORS_FOR_RECOMMENDATION
        and register_clicks == 0
    ):
        split = _habit_pre_class_days(week_end)
        if split is not None and split[0] == 0:
            recommendations.append(
                f"Habit campaign traffic reached {habit_visitors:g} rolling 7-day unique visitors with no register click, and the page was post-class the whole window (Register is hidden by design until the next class is schedulable). No CTA change indicated."
            )
        elif split is not None:
            recommendations.append(
                f"Habit campaign traffic reached {habit_visitors:g} rolling 7-day unique visitors without a register click across {split[0]} pre-class day(s). Review the /ig CTA path."
            )
        else:
            recommendations.append(
                f"Habit campaign traffic reached {habit_visitors:g} rolling 7-day unique visitors without a register click. Review the /ig CTA path (page state unknown, classes API unreachable)."
            )
    return recommendations


def _recommendations(report: dict[str, Any]) -> list[str]:
    if report["status"] == "insufficient_data":
        return ["Collect at least two daily snapshots before changing the campaign."]

    metrics = report["metrics"]
    funnel = metrics["landing_page"]
    follower_delta = metrics["instagram_followers"]["delta"]
    subscriber_delta = metrics["email_subscribers"]["delta"]
    habit_delta = metrics["next_habit_registrations"]["delta"]
    upcoming = report["campaigns"]["upcoming_variants"]
    performance = report["post_performance"]
    variants = report["campaign_performance"]["variants"]

    recommendations: list[str] = []
    if upcoming and not variants:
        recommendations.append(
            "Let the scheduled campaign publish before changing copy. No published variant evidence exists yet."
        )
    if 0 < funnel["visitors"] < 10:
        recommendations.append("Landing traffic is too small to judge conversion. Keep measuring before changing the page.")
    if funnel["visitors"] >= 10 and funnel["habit_register_clicks"] == 0:
        split = _habit_pre_class_days(parse_date(report["week_end"]))
        if split is not None and split[0] == 0:
            recommendations.append(
                "Landing visits produced no register clicks, and the page was post-class the whole window (Register is hidden by design until the next class is schedulable). No CTA change indicated."
            )
        elif split is not None:
            recommendations.append(
                f"Review the Instagram bio link and landing-page CTA path. Visits did not become register clicks across {split[0]} pre-class day(s)."
            )
        else:
            recommendations.append(
                "Review the Instagram bio link and landing-page CTA path. Visits did not become register clicks (page state unknown, classes API unreachable)."
            )
    if funnel["habit_register_clicks"] > 0 and habit_delta is not None and habit_delta <= 0:
        recommendations.append("Inspect the HeyMarvelous registration handoff. Register clicks did not increase Habit registrations.")
    if follower_delta is not None and follower_delta < 0:
        recommendations.append("Review the posts from the down-follower week before changing the next variant.")
    if subscriber_delta is not None and subscriber_delta <= 0 and funnel["habit_signup_success"] > 0:
        recommendations.append("Check SendGrid signup attribution. Signup successes did not move subscriber count.")
    if performance["posts_analyzed"] and performance["totals"]["growth_actions"] == 0:
        recommendations.append(
            "The reviewed Reels produced no follows, saves, shares, or clicks. Use this as the baseline the next variant must beat."
        )
    for variant in variants:
        comparison = variant["comparison_to_baseline"]
        if variant["post_count"] < 3:
            recommendations.append(
                f"Keep {variant['key']} running until at least three published posts have mature analytics."
            )
        elif comparison["assessment"] == "better":
            recommendations.append(
                f"Keep {variant['key']} for another week. It improved most measured post averages over baseline."
            )
        elif comparison["assessment"] == "weaker":
            recommendations.append(
                f"Replace or revise {variant['key']}. It underperformed the baseline on most measured post averages."
            )
        elif comparison["assessment"] == "mixed":
            recommendations.append(
                f"Review {variant['key']} by reach, watch time, and growth actions separately. Its result is mixed."
            )
    recommendations.extend(_website_recommendations(report["website_performance"], parse_date(report["week_end"])))
    if not recommendations:
        recommendations.append("Keep the current campaign running until the next weekly review has published-post evidence.")
    return recommendations


def _ig_shortcode(url: Any) -> str | None:
    match = re.search(r"instagram\.com/(?:reel|p|tv)/([^/?#]+)", str(url or ""))
    return match.group(1) if match else None


def fetch_meta_media(limit: int = 25) -> list[dict[str, Any]]:
    """Live Instagram media pull for the cross-check. Fails open to an empty list.

    The weekly report must never break on a Meta hiccup, so a missing token or
    any API error yields no cross-check rather than an exception.
    """
    token = os.getenv("META_PAGE_ACCESS_TOKEN")
    if not token:
        return []
    try:
        from meta_insights import MetaInsights

        return MetaInsights(token).ig_recent_media(limit=limit)
    except Exception:
        return []


def _meta_cross_check(posts: list[dict[str, Any]], meta_media: list[dict[str, Any]] | None) -> dict[str, Any]:
    """Match the week's Instagram posts to Meta's own insights by shortcode.

    Meta and Zernio coexist and validate each other. Meta is authoritative for
    Instagram reach, so a large delta flags a stale Zernio number rather than a
    real disagreement. Read only, mutates neither source.
    """
    if not meta_media:
        reason = "meta insights not fetched" if meta_media is None else "no meta media returned"
        return {"status": "unavailable", "reason": reason, "matched": []}
    by_code: dict[str, dict[str, Any]] = {}
    for media in meta_media:
        code = _ig_shortcode(media.get("permalink"))
        if code:
            by_code[code] = media
    matched: list[dict[str, Any]] = []
    unmatched = 0
    for post in posts:
        code = _ig_shortcode(post.get("platform_post_url"))
        media = by_code.get(code) if code else None
        if not media:
            unmatched += 1
            continue
        zm = post.get("metrics") or {}
        mi = media.get("insights") or {}
        zr = int(_metric(zm, "reach"))
        mr = int(mi.get("reach") or 0)
        matched.append(
            {
                "label": _post_label(post),
                "permalink": post.get("platform_post_url"),
                "shortcode": code,
                "zernio": {
                    "reach": zr,
                    "likes": int(_metric(zm, "likes")),
                    "comments": int(_metric(zm, "comments")),
                    "saves": int(_metric(zm, "saves")),
                    "shares": int(_metric(zm, "shares")),
                },
                "meta": {
                    "reach": mr,
                    "likes": int(mi.get("likes") or 0),
                    "comments": int(mi.get("comments") or 0),
                    "saved": int(mi.get("saved") or 0),
                    "shares": int(mi.get("shares") or 0),
                },
                "reach_delta": zr - mr,
                "reach_agrees": abs(zr - mr) <= max(5, round(0.10 * mr)),
            }
        )
    agree = sum(1 for row in matched if row["reach_agrees"])
    return {
        "status": "ok",
        "matched": matched,
        "matched_count": len(matched),
        "unmatched_posts": unmatched,
        "agree_count": agree,
        "disagree_count": len(matched) - agree,
    }


def build_weekly_review(snapshots: list[dict[str, Any]], *, week_end: date, days: int = DEFAULT_DAYS, meta_media: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    week_start = week_end - timedelta(days=days - 1)
    history = sorted(snapshots, key=lambda item: item.get("date") or item.get("captured_at") or "")
    snapshots = [
        snapshot
        for snapshot in history
        if (snapshot_date := _snapshot_date(snapshot)) is not None
        and week_start <= snapshot_date <= week_end
    ]
    posts = _post_rows(snapshots, week_start=week_start, week_end=week_end)
    report: dict[str, Any] = {
        "status": "ok" if len(snapshots) >= 2 else "insufficient_data",
        "week_start": week_start.isoformat(),
        "week_end": week_end.isoformat(),
        "snapshot_count": len(snapshots),
        "first_snapshot_date": snapshots[0].get("date") if snapshots else None,
        "last_snapshot_date": snapshots[-1].get("date") if snapshots else None,
        "metrics": {
            "instagram_followers": _delta(snapshots, "instagram_followers"),
            "facebook_followers": _delta(snapshots, "facebook_followers"),
            "email_subscribers": _delta(snapshots, "email_subscribers"),
            "youtube_subscribers": _delta(snapshots, "youtube_subscribers"),
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
        "post_performance": _post_performance(posts),
        "campaign_performance": _campaign_performance(posts),
        "website_performance": _website_performance(history, trend_snapshots=snapshots),
    }
    report["meta_cross_check"] = _meta_cross_check(posts, meta_media)
    report["recommendations"] = _recommendations(report)
    return report


def _format_number(value: Any) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float) and not value.is_integer():
        return f"{value:,.2f}"
    return f"{value:,.0f}"


def _format_delta(metric: dict[str, Any]) -> str:
    start = metric.get("start")
    end = metric.get("end")
    delta = metric.get("delta")
    if start is None or end is None or delta is None:
        return "n/a"
    return f"{_format_number(start)} -> {_format_number(end)} ({delta:+,.0f})"


def _post_label(post: dict[str, Any] | None) -> str:
    if not post:
        return "None"
    target = str(post.get("posted_for_class") or post.get("scheduled_for") or "Unknown")
    return target


def render_markdown(report: dict[str, Any]) -> str:
    metrics = report["metrics"]
    funnel = metrics["landing_page"]
    performance = report["post_performance"]
    lines = [
        "# TWY Audience Growth Review",
        "",
        f"Period: {report['week_start']} through {report['week_end']}",
        f"Daily snapshots: {report['snapshot_count']}",
        "",
        "## Audience",
        "",
        f"- Instagram followers: {_format_delta(metrics['instagram_followers'])}",
        f"- Email subscribers: {_format_delta(metrics['email_subscribers'])}",
        f"- Next Habits registrations: {_format_delta(metrics['next_habit_registrations'])}",
        "",
        "## Funnel",
        "",
        f"- Visitors: {_format_number(funnel['visitors'])}",
        f"- Pageviews: {_format_number(funnel['pageviews'])}",
        f"- Register clicks: {_format_number(funnel['habit_register_clicks'])}",
        f"- Signup completions: {_format_number(funnel['habit_signup_success'])}",
        "",
        "## Post Performance",
        "",
        f"Analyzed {performance['posts_analyzed']} published Reels with mature Zernio analytics.",
        "",
        "| Target | Reach | Views | Engagement | Avg watch | Follows | Saves | Shares | Clicks |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for post in performance["posts"]:
        post_metrics = post.get("metrics") or {}
        target = _post_label(post)
        url = str(post.get("platform_post_url") or "").strip()
        target_text = f"[{target}]({url})" if url else target
        lines.append(
            "| "
            + " | ".join(
                [
                    target_text,
                    _format_number(_metric(post_metrics, "reach")),
                    _format_number(_metric(post_metrics, "views")),
                    f"{_metric(post_metrics, 'engagementRate'):.2f}%",
                    f"{_metric(post_metrics, 'igReelsAvgWatchTime') / 1000:.2f}s",
                    _format_number(_metric(post_metrics, "follows")),
                    _format_number(_metric(post_metrics, "saves")),
                    _format_number(_metric(post_metrics, "shares")),
                    _format_number(_metric(post_metrics, "clicks")),
                ]
            )
            + " |"
        )
    if not performance["posts"]:
        lines.append("| No mature post analytics in this period |  |  |  |  |  |  |  |  |")

    lines.extend(["", "## Campaign Variants", ""])
    variants = report["campaign_performance"]["variants"]
    if variants:
        for variant in variants:
            comparison = variant["comparison_to_baseline"]
            lines.append(
                f"- `{variant['key']}`: {variant['post_count']} posts, "
                f"{_format_number(variant['averages']['reach'])} average reach, "
                f"{variant['averages']['watch_time_seconds']:.2f}s average watch, "
                f"assessment `{comparison['assessment']}`"
            )
    else:
        lines.append("- No campaign variant had published posts with mature analytics in this period.")

    cross = report.get("meta_cross_check") or {}
    lines.extend(["", "## Meta cross-check (Instagram)", ""])
    if cross.get("status") == "ok":
        lines.append(
            f"Compared {cross['matched_count']} Instagram posts against Meta's own insights. "
            f"{cross['agree_count']} agree on reach within tolerance, {cross['disagree_count']} differ, "
            f"{cross['unmatched_posts']} had no Meta match. Meta is authoritative for reach."
        )
        matched = cross.get("matched") or []
        if matched:
            lines.extend(
                [
                    "",
                    "| Target | Zernio reach | Meta reach | Delta | Likes Z/M |",
                    "| --- | ---: | ---: | ---: | ---: |",
                ]
            )
            for row in matched:
                flag = "" if row["reach_agrees"] else " !"
                lines.append(
                    "| "
                    + " | ".join(
                        [
                            row["label"],
                            _format_number(row["zernio"]["reach"]),
                            _format_number(row["meta"]["reach"]),
                            f"{row['reach_delta']:+d}{flag}",
                            f"{row['zernio']['likes']}/{row['meta']['likes']}",
                        ]
                    )
                    + " |"
                )
    else:
        lines.append(
            f"Unavailable this run ({cross.get('reason', 'no data')}). The Zernio numbers above stand alone."
        )

    lines.extend(["", "## Recommendations", ""])
    lines.extend(f"- {item}" for item in report["recommendations"])
    return "\n".join(lines) + "\n"


def render_slack(report: dict[str, Any]) -> str:
    metrics = report["metrics"]
    funnel = metrics["landing_page"]
    performance = report["post_performance"]
    top = performance["top_by_reach"]
    lines = [
        "*TWY audience growth review*",
        f"{report['week_start']} to {report['week_end']} | {report['snapshot_count']} daily snapshots",
        "",
        f"*IG followers:* {_format_delta(metrics['instagram_followers'])}",
        f"*Email subscribers:* {_format_delta(metrics['email_subscribers'])}",
        f"*Habits registrations:* {_format_delta(metrics['next_habit_registrations'])}",
        (
            f"*Funnel:* {_format_number(funnel['visitors'])} visitors | "
            f"{_format_number(funnel['habit_register_clicks'])} register clicks | "
            f"{_format_number(funnel['habit_signup_success'])} signups"
        ),
        (
            f"*Posts:* {performance['posts_analyzed']} analyzed | "
            f"{_format_number(performance['totals']['reach'])} total reach | "
            f"{_format_number(performance['totals']['growth_actions'])} follows+saves+shares+clicks"
        ),
    ]
    if top:
        top_metrics = top.get("metrics") or {}
        top_label = _post_label(top)
        top_url = str(top.get("platform_post_url") or "").strip()
        if top_url:
            top_label = f"<{top_url}|{top_label}>"
        lines.append(
            f"*Top Reel:* {top_label} | {_format_number(_metric(top_metrics, 'reach'))} reach | "
            f"{_metric(top_metrics, 'engagementRate'):.2f}% engagement | "
            f"{_metric(top_metrics, 'igReelsAvgWatchTime') / 1000:.2f}s average watch"
        )
    cross = report.get("meta_cross_check") or {}
    if cross.get("status") == "ok":
        lines.append(
            f"*Meta cross-check:* {cross['matched_count']} IG posts | "
            f"{cross['agree_count']} agree on reach | {cross['disagree_count']} differ vs Zernio"
        )
    lines.extend(["", "*Decisions:*"])
    lines.extend(f"- {item}" for item in report["recommendations"])
    return "\n".join(lines)


def save_weekly_review(report: dict[str, Any], *, output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / f"{report['week_end']}.json"
    markdown_path = output_dir / f"{report['week_end']}.md"
    json_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    markdown_path.write_text(render_markdown(report))
    return json_path, markdown_path


def deterministic_client_msg_id(week_end: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"twy-social-growth-weekly:{week_end}"))


def post_slack_dm_once(
    report: dict[str, Any],
    *,
    state_path: Path,
    token: str,
    user_id: str,
    post: Any | None = None,
) -> bool:
    if state_path.exists():
        try:
            state = read_json(state_path)
        except Exception:
            state = {}
        if state.get("last_week_end") == report["week_end"]:
            return False

    if post is None:
        post = requests.post
    response = post(
        "https://slack.com/api/chat.postMessage",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        json={
            "channel": user_id,
            "text": render_slack(report) + "\n\nhttps://stats.tiffanywoodyoga.com/",
            "client_msg_id": deterministic_client_msg_id(report["week_end"]),
        },
        timeout=15,
    )
    response.raise_for_status()
    payload = response.json()
    if not payload.get("ok"):
        raise RuntimeError(f"Slack API error: {payload.get('error')}")
    state_path.write_text(
        json.dumps(
            {
                "last_week_end": report["week_end"],
                "posted_at": datetime.now(timezone.utc).isoformat(),
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="Build TWY weekly social growth review")
    parser.add_argument("--dry-run", action="store_true", help="Print JSON without writing it")
    parser.add_argument("--week-end", help="Week-end date, YYYY-MM-DD. Default is today in UTC.")
    parser.add_argument("--days", type=int, default=DEFAULT_DAYS)
    parser.add_argument("--slack", action="store_true", help="Send the saved review to JP by Slack direct message")
    args = parser.parse_args()

    load_env()
    # Mountain, not UTC. The week this report covers is the week Tiff and
    # JP live in, and a UTC default rolls the boundary a day early every
    # evening after 18:00 MT.
    week_end = (
        parse_date(args.week_end)
        if args.week_end
        else datetime.now(ZoneInfo("America/Denver")).date()
    )
    data_dir = default_data_root()
    snapshots = load_daily_snapshots(data_dir / "social_growth", week_end=week_end, days=args.days)
    report = build_weekly_review(
        snapshots, week_end=week_end, days=args.days, meta_media=fetch_meta_media()
    )
    if args.dry_run:
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0
    output_dir = data_dir / "social_growth" / "weekly"
    json_path, markdown_path = save_weekly_review(report, output_dir=output_dir)
    print(f"Saved weekly social growth review to {json_path}")
    print(f"Saved readable weekly social growth review to {markdown_path}")
    if report["status"] == "insufficient_data":
        print("Weekly social growth review has insufficient data.")
    if args.slack:
        token = os.getenv("TWY_REPORTER_BOT_TOKEN") or os.getenv("SLACK_BOT_TOKEN")
        user_id = os.getenv("SLACK_USER_JP")
        if not token or not user_id:
            raise RuntimeError("Slack bot token and JP Slack user ID are required for weekly social growth review")
        posted = post_slack_dm_once(
            report,
            state_path=output_dir / SLACK_STATE_FILE,
            token=token,
            user_id=user_id,
        )
        print("Sent weekly social growth review to JP." if posted else "Slack review already posted.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
