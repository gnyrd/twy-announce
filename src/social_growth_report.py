#!/usr/bin/env python3
"""Collect daily social growth evidence for TWY."""

from __future__ import annotations

import argparse
from collections import Counter
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sqlite3
import sys
import time
from typing import Any

import requests
from twy_paths import data_root as default_data_root
from twy_paths import load_env
from twy_paths import twy_root as default_twy_root


DEFAULT_ZERNIO_BASE_URL = "https://zernio.com/api/v1"
DEFAULT_ZERNIO_LOOKBACK_HOURS = 72
DEFAULT_ZERNIO_LOOKAHEAD_HOURS = 72
DEFAULT_ZERNIO_TOKEN_WARNING_HOURS = 48
DEFAULT_FOLLOWER_DROP_ALERT_THRESHOLD = 10
DEFAULT_CAMPAIGN_LOOKBACK_DAYS = 7
DEFAULT_CAMPAIGN_LOOKAHEAD_DAYS = 14
DEFAULT_SYSTEM_WARNINGS_CHANNEL = "C0ASG1EU0HL"
DEFAULT_PLAUSIBLE_BASE_URL = "https://analytics.tiffanywoodyoga.com"
PLAUSIBLE_FUNNEL_EVENTS = (
    "Habit Register Click",
    "Habit Newsletter Open",
    "Habit Signup Submit",
    "Habit Signup Success",
    "Habit Signup Error",
    "Habit Membership Click",
)
PLAUSIBLE_FUNNEL_DIMENSIONS = (
    "event:name",
    "event:props:source",
    "event:props:content",
    "event:props:page_state",
    "event:props:path",
)
ZERNIO_ANALYTICS_METRICS = (
    "impressions",
    "reach",
    "likes",
    "comments",
    "shares",
    "saves",
    "clicks",
    "views",
    "follows",
    "igReelsAvgWatchTime",
    "igReelsVideoViewTotalTime",
    "engagementRate",
)
CAMPAIGN_METADATA_KEYS = ("campaign", "ctaVariant", "habitTargetDate")


def iso_z(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def read_json(path: Path) -> Any:
    return json.loads(path.read_text())


def latest_json_snapshot(history_dir: Path) -> dict[str, Any] | None:
    for path in sorted(history_dir.glob("*.json"), reverse=True):
        try:
            return {"date": path.stem, "data": read_json(path)}
        except Exception:
            continue
    return None


def latest_instagram_followers(twy_root: Path) -> dict[str, Any] | None:
    history_dir = twy_root / "announce/data/instagram/history"
    snapshots: list[dict[str, Any]] = []
    for path in sorted(history_dir.glob("*.json"), reverse=True):
        try:
            data = read_json(path)
        except Exception:
            continue
        count = data.get("follower_count")
        if count is None:
            continue
        snapshots.append({"count": int(count), "snapshot_date": path.stem})
        if len(snapshots) == 2:
            break
    if not snapshots:
        return None
    latest = snapshots[0]
    if len(snapshots) > 1:
        previous = snapshots[1]
        latest["previous_count"] = previous["count"]
        latest["previous_snapshot_date"] = previous["snapshot_date"]
        latest["delta_since_previous"] = latest["count"] - previous["count"]
    return latest


def latest_email_subscribers(twy_root: Path) -> dict[str, Any] | None:
    snapshot = latest_json_snapshot(twy_root / "announce/data/email/history")
    if not snapshot:
        return None
    data = snapshot["data"]
    count = data.get("subscriber_count")
    if count is None:
        return None
    return {
        "count": int(count),
        "list_name": data.get("list_name"),
        "snapshot_date": snapshot["date"],
    }


def next_habit_event(data_root: Path, captured_at: datetime) -> dict[str, Any] | None:
    db_path = data_root / "marvy.db"
    if not db_path.exists():
        return None
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            """
            SELECT id, event_name, event_start_datetime, number_of_registrations
            FROM events
            WHERE (event_name LIKE 'Habit:%' OR event_name = 'The Yoga Habit')
              AND is_cancelled = 0
              AND event_start_datetime >= :now
            ORDER BY event_start_datetime
            LIMIT 1
            """,
            {"now": iso_z(captured_at)},
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return None
    return {
        "id": row["id"],
        "name": row["event_name"],
        "start": row["event_start_datetime"],
        "registrations": row["number_of_registrations"],
    }


def json_length(path: Path) -> int | None:
    if not path.exists():
        return None
    try:
        payload = read_json(path)
    except Exception:
        return None
    if isinstance(payload, list):
        return len(payload)
    return None


def queue_snapshot(twy_root: Path) -> dict[str, Any]:
    state_dir = twy_root / "clips/state"
    scheduler_state_path = state_dir / "ig_scheduler_state.json"
    scheduler_state = {}
    if scheduler_state_path.exists():
        try:
            scheduler_state = read_json(scheduler_state_path)
        except Exception:
            scheduler_state = {}
    return {
        "ig_clip_queue": json_length(state_dir / "ig_queue.json"),
        "ig_quote_queue": json_length(state_dir / "ig_quote_queue.json"),
        "ig_history": json_length(state_dir / "ig_history.json"),
        "clip_pool_warning_active": scheduler_state.get("clip_pool_warning_active"),
        "clip_pool_warning_posted_to_slack": scheduler_state.get("clip_pool_warning_posted_to_slack"),
    }


def zernio_fetcher_from_env() -> Callable[[str], dict[str, Any]] | None:
    api_key = os.getenv("ZERNIO_API_KEY", "").strip()
    if not api_key:
        return None
    base_url = os.getenv("ZERNIO_BASE_URL", DEFAULT_ZERNIO_BASE_URL).rstrip("/")
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    def fetch(post_id: str) -> dict[str, Any]:
        url = f"{base_url}/posts/{post_id}"
        last_payload: dict[str, Any] = {}
        for attempt in range(3):
            response = requests.get(url, headers=headers, timeout=30)
            try:
                payload = response.json()
            except ValueError:
                payload = {"text": response.text[:200]}
            last_payload = payload
            if response.status_code == 429:
                time.sleep(8 + attempt * 4)
                continue
            if response.status_code >= 400:
                raise RuntimeError(f"Zernio GET /posts/{post_id} failed: {response.status_code} {payload}")
            return payload
        raise RuntimeError(f"Zernio GET /posts/{post_id} rate limited: {last_payload}")

    return fetch


def zernio_analytics_fetcher_from_env() -> Callable[[str], dict[str, Any]] | None:
    api_key = os.getenv("ZERNIO_API_KEY", "").strip()
    if not api_key:
        return None
    account_id = os.getenv("ZERNIO_INSTAGRAM_ACCOUNT_ID", "").strip()
    base_url = os.getenv("ZERNIO_BASE_URL", DEFAULT_ZERNIO_BASE_URL).rstrip("/")
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    def fetch(post_id: str) -> dict[str, Any]:
        params = {"postId": post_id}
        if account_id:
            params["accountId"] = account_id
        response = requests.get(
            f"{base_url}/analytics",
            headers=headers,
            params=params,
            timeout=30,
        )
        try:
            payload = response.json()
        except ValueError:
            payload = {"text": response.text[:200]}
        payload["_collector_http_status"] = response.status_code
        if response.status_code in {202, 402, 424}:
            return payload
        if response.status_code >= 400:
            raise RuntimeError(f"Zernio GET /analytics failed: {response.status_code} {payload}")
        return payload

    return fetch


def zernio_account_health_from_env() -> dict[str, Any] | None:
    api_key = os.getenv("ZERNIO_API_KEY", "").strip()
    account_id = os.getenv("ZERNIO_INSTAGRAM_ACCOUNT_ID", "").strip()
    if not api_key or not account_id:
        return None
    base_url = os.getenv("ZERNIO_BASE_URL", DEFAULT_ZERNIO_BASE_URL).rstrip("/")
    response = requests.get(
        f"{base_url}/accounts/{account_id}/health",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()
    permissions = payload.get("permissions") or {}
    token_status = payload.get("tokenStatus") or {}
    return {
        "account_id": payload.get("accountId"),
        "username": payload.get("username"),
        "display_name": payload.get("displayName"),
        "platform": payload.get("platform"),
        "status": payload.get("status"),
        "can_post": permissions.get("canPost"),
        "can_fetch_analytics": permissions.get("canFetchAnalytics"),
        "missing_required": permissions.get("missingRequired"),
        "token_status": {
            "valid": token_status.get("valid"),
            "needs_refresh": token_status.get("needsRefresh"),
            "expires_at": token_status.get("expiresAt"),
        },
    }


def zernio_post_row(entry: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    post = payload.get("post") or payload
    platforms = post.get("platforms") or []
    platform = platforms[0] if platforms and isinstance(platforms[0], dict) else {}
    platform_specific = platform.get("platformSpecificData") or {}
    return {
        "scheduled_for": entry.get("scheduled_for"),
        "post_type": entry.get("post_type"),
        "posted_for_class": entry.get("posted_for_class"),
        "class_name": entry.get("class_name"),
        "clip_name": entry.get("clip_name"),
        "zernio_post_id": entry.get("zernio_post_id"),
        "title": post.get("title"),
        "post_status": post.get("status"),
        "platform_status": platform.get("status"),
        "content_type": platform_specific.get("contentType"),
        "platform_post_url": platform.get("platformPostUrl"),
        "published_at": platform.get("publishedAt"),
        "publish_attempts": platform.get("publishAttempts"),
        "error": platform.get("error") or post.get("error") or post.get("lastError") or post.get("failureReason"),
        "content": (post.get("content") or "").replace("\n", " ")[:280],
    }


def zernio_analytics_metrics(payload: dict[str, Any]) -> dict[str, Any]:
    metrics = payload.get("analytics")
    if not isinstance(metrics, dict):
        platforms = payload.get("platforms") or []
        if platforms and isinstance(platforms[0], dict):
            metrics = platforms[0].get("analytics")
    if not isinstance(metrics, dict):
        return {}
    return {
        key: metrics.get(key)
        for key in ZERNIO_ANALYTICS_METRICS
        if key in metrics
    }


def zernio_analytics_row(row: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    platforms = payload.get("platforms") or []
    platform = platforms[0] if platforms and isinstance(platforms[0], dict) else {}
    return {
        "scheduled_for": row.get("scheduled_for"),
        "post_type": row.get("post_type"),
        "posted_for_class": row.get("posted_for_class"),
        "class_name": row.get("class_name"),
        "clip_name": row.get("clip_name"),
        "zernio_post_id": row.get("zernio_post_id"),
        "platform_post_id": platform.get("platformPostId") or payload.get("platformPostId"),
        "platform_post_url": platform.get("platformPostUrl") or payload.get("platformPostUrl"),
        "sync_status": platform.get("syncStatus") or payload.get("syncStatus"),
        "metrics": zernio_analytics_metrics(payload),
    }


def collect_zernio_post_analytics(
    *,
    rows: list[dict[str, Any]],
    fetch_analytics: Callable[[str], dict[str, Any]] | None,
) -> dict[str, Any]:
    if fetch_analytics is None:
        return {
            "status": "not_configured",
            "reason": "ZERNIO_API_KEY is not configured",
        }
    published = [
        row
        for row in rows
        if row.get("zernio_post_id")
        and (row.get("post_status") == "published" or row.get("platform_status") == "published")
    ]
    if not published:
        return {
            "status": "no_published_posts",
            "queried_count": 0,
            "posts": [],
        }
    analytics_rows: list[dict[str, Any]] = []
    api_errors: list[dict[str, Any]] = []
    pending: list[dict[str, Any]] = []
    platform_failed: list[dict[str, Any]] = []
    for row in published:
        post_id = str(row["zernio_post_id"])
        try:
            payload = fetch_analytics(post_id)
        except Exception as exc:
            api_errors.append(
                {
                    "zernio_post_id": post_id,
                    "scheduled_for": row.get("scheduled_for"),
                    "error": str(exc),
                }
            )
            continue
        http_status = payload.get("_collector_http_status")
        if http_status == 402:
            return {
                "status": "blocked",
                "queried_count": len(analytics_rows),
                "code": payload.get("code"),
                "error": payload.get("error"),
                "reason": payload.get("reason"),
                "mode": payload.get("mode"),
            }
        if http_status == 202:
            pending.append(
                {
                    "zernio_post_id": post_id,
                    "scheduled_for": row.get("scheduled_for"),
                    "message": payload.get("message"),
                }
            )
            continue
        if http_status == 424:
            platform_failed.append(
                {
                    "zernio_post_id": post_id,
                    "scheduled_for": row.get("scheduled_for"),
                    "error": payload.get("error"),
                }
            )
            continue
        analytics_rows.append(zernio_analytics_row(row, payload))
    return {
        "status": "ok",
        "queried_count": len(analytics_rows),
        "api_error_count": len(api_errors),
        "api_errors": api_errors,
        "pending_count": len(pending),
        "pending": pending,
        "platform_failed_count": len(platform_failed),
        "platform_failed": platform_failed,
        "posts": analytics_rows,
    }


def collect_zernio_recent_status(
    *,
    history_path: Path,
    captured_at: datetime,
    fetch_post: Callable[[str], dict[str, Any]] | None,
    fetch_analytics: Callable[[str], dict[str, Any]] | None = None,
    lookback_hours: int = DEFAULT_ZERNIO_LOOKBACK_HOURS,
    lookahead_hours: int = DEFAULT_ZERNIO_LOOKAHEAD_HOURS,
) -> dict[str, Any]:
    if fetch_post is None:
        return {
            "status": "not_configured",
            "reason": "ZERNIO_API_KEY is not configured",
        }
    if not history_path.exists():
        return {
            "status": "missing_history",
            "history_path": str(history_path),
        }
    history = read_json(history_path)
    window_start = captured_at - timedelta(hours=lookback_hours)
    window_end = captured_at + timedelta(hours=lookahead_hours)
    rows: list[dict[str, Any]] = []
    api_errors: list[dict[str, Any]] = []
    for entry in history:
        post_id = entry.get("zernio_post_id")
        scheduled_for = entry.get("scheduled_for")
        if not post_id or not scheduled_for:
            continue
        try:
            scheduled_at = parse_datetime(scheduled_for)
        except ValueError:
            continue
        if scheduled_at < window_start or scheduled_at > window_end:
            continue
        try:
            payload = fetch_post(post_id)
        except Exception as exc:
            api_errors.append(
                {
                    "zernio_post_id": post_id,
                    "scheduled_for": scheduled_for,
                    "error": str(exc),
                }
            )
            continue
        rows.append(zernio_post_row(entry, payload))
    failed = [
        row
        for row in rows
        if row.get("post_status") == "failed" or row.get("platform_status") == "failed"
    ]
    pending = [
        row
        for row in rows
        if row.get("post_status") == "scheduled" or row.get("platform_status") == "pending"
    ]
    return {
        "status": "ok",
        "window_start": iso_z(window_start),
        "window_end": iso_z(window_end),
        "queried_count": len(rows),
        "api_error_count": len(api_errors),
        "api_errors": api_errors,
        "by_post_status": dict(Counter(row.get("post_status") for row in rows)),
        "by_platform_status": dict(Counter(row.get("platform_status") for row in rows)),
        "by_content_type": dict(Counter(row.get("content_type") for row in rows)),
        "failed_count": len(failed),
        "failed": failed,
        "pending_count": len(pending),
        "pending": pending,
        "analytics": collect_zernio_post_analytics(rows=rows, fetch_analytics=fetch_analytics),
    }


def collect_plausible_status(
    *,
    captured_at: datetime,
    post_query: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    api_key = os.getenv("PLAUSIBLE_API_KEY", "").strip()
    site_id = os.getenv("PLAUSIBLE_SITE_ID", "").strip()
    if not api_key or not site_id:
        return {
            "status": "not_configured",
            "required": ["PLAUSIBLE_API_KEY", "PLAUSIBLE_SITE_ID"],
        }
    if post_query is None:
        base_url = os.getenv("PLAUSIBLE_BASE_URL", DEFAULT_PLAUSIBLE_BASE_URL).rstrip("/")

        def post_query(body: dict[str, Any]) -> dict[str, Any]:
            response = requests.post(
                f"{base_url}/api/v2/query",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json=body,
                timeout=30,
            )
            try:
                payload = response.json()
            except ValueError:
                payload = {"text": response.text[:200]}
            if response.status_code >= 400:
                raise RuntimeError(f"Plausible query failed: {response.status_code} {payload}")
            return payload

    queries = {
        "day": "day",
        "last_7_days": "7d",
        "last_30_days": "30d",
    }
    results: dict[str, Any] = {}
    try:
        for label, date_range in queries.items():
            payload = post_query(
                {
                    "site_id": site_id,
                    "metrics": ["visitors", "visits", "pageviews", "events"],
                    "date_range": date_range,
                }
            )
            row = (payload.get("results") or [{}])[0]
            metrics = row.get("metrics") or []
            results[label] = {
                "visitors": metrics[0] if len(metrics) > 0 else None,
                "visits": metrics[1] if len(metrics) > 1 else None,
                "pageviews": metrics[2] if len(metrics) > 2 else None,
                "events": metrics[3] if len(metrics) > 3 else None,
                "query_date_range": (payload.get("query") or {}).get("date_range"),
            }
            event_payload = post_query(
                {
                    "site_id": site_id,
                    "metrics": ["events"],
                    "date_range": date_range,
                    "dimensions": list(PLAUSIBLE_FUNNEL_DIMENSIONS),
                    "filters": [["is", "event:name", list(PLAUSIBLE_FUNNEL_EVENTS)]],
                    "pagination": {"limit": 100},
                }
            )
            event_counts = {event_name: 0 for event_name in PLAUSIBLE_FUNNEL_EVENTS}
            vector_rows: list[dict[str, Any]] = []
            for event_row in event_payload.get("results") or []:
                dimensions = event_row.get("dimensions") or []
                event_name = dimensions[0] if dimensions else ""
                if event_name not in event_counts:
                    continue
                event_metrics = event_row.get("metrics") or []
                event_count = event_metrics[0] if event_metrics else 0
                event_counts[event_name] += event_count
                vector_rows.append(
                    {
                        "event": event_name,
                        "source": dimensions[1] if len(dimensions) > 1 else "",
                        "content": dimensions[2] if len(dimensions) > 2 else "",
                        "page_state": dimensions[3] if len(dimensions) > 3 else "",
                        "path": dimensions[4] if len(dimensions) > 4 else "",
                        "events": event_count,
                    }
                )
            results[label]["funnel_events"] = event_counts
            results[label]["funnel_by_vector"] = vector_rows
        return {
            "status": "ok",
            "site_id": site_id,
            "captured_at": iso_z(captured_at),
            "metrics": results,
        }
    except Exception as exc:
        return {
            "status": "error",
            "site_id": site_id,
            "error": str(exc),
        }


def collect_socialblade_status() -> dict[str, Any]:
    export_path = os.getenv("SOCIALBLADE_EXPORT_PATH", "").strip()
    if not export_path:
        return {
            "status": "not_configured",
            "required": ["SOCIALBLADE_EXPORT_PATH or API integration"],
        }
    path = Path(export_path)
    if not path.exists():
        return {
            "status": "missing_export",
            "path": str(path),
        }
    try:
        payload = read_json(path)
    except Exception as exc:
        return {
            "status": "invalid_export",
            "path": str(path),
            "error": str(exc),
        }
    return {
        "status": "loaded_export",
        "path": str(path),
        "data": payload,
    }


def campaign_metadata_from_history_entry(entry: dict[str, Any]) -> dict[str, str] | None:
    campaign = entry.get("campaign")
    if not isinstance(campaign, dict):
        return None
    campaign_name = str(campaign.get("campaign") or "").strip()
    variant = str(campaign.get("ctaVariant") or "").strip()
    if not campaign_name or not variant:
        return None
    row = {
        "campaign": campaign_name,
        "ctaVariant": variant,
        "habitTargetDate": str(campaign.get("habitTargetDate") or "").strip(),
    }
    return row


def _append_unique(values: list[str], value: Any) -> None:
    text = str(value or "").strip()
    if text and text not in values:
        values.append(text)


def group_campaign_variants(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for row in rows:
        key = f"{row['campaign']}:{row['ctaVariant']}"
        group = grouped.setdefault(
            key,
            {
                "key": key,
                "campaign": row["campaign"],
                "ctaVariant": row["ctaVariant"],
                "habitTargetDate": row.get("habitTargetDate"),
                "post_count": 0,
                "first_scheduled_for": row["scheduled_for"],
                "last_scheduled_for": row["scheduled_for"],
                "posted_for_classes": [],
                "post_types": [],
                "zernio_post_ids": [],
            },
        )
        group["post_count"] += 1
        group["last_scheduled_for"] = row["scheduled_for"]
        _append_unique(group["posted_for_classes"], row.get("posted_for_class"))
        _append_unique(group["post_types"], row.get("post_type"))
        _append_unique(group["zernio_post_ids"], row.get("zernio_post_id"))
    return sorted(grouped.values(), key=lambda item: (item["first_scheduled_for"], item["key"]))


def campaign_snapshot(
    *,
    history_path: Path,
    captured_at: datetime,
    lookback_days: int = DEFAULT_CAMPAIGN_LOOKBACK_DAYS,
    lookahead_days: int = DEFAULT_CAMPAIGN_LOOKAHEAD_DAYS,
) -> dict[str, Any]:
    if not history_path.exists():
        return {
            "status": "missing_history",
            "history_path": str(history_path),
        }
    window_start = captured_at - timedelta(days=lookback_days)
    window_end = captured_at + timedelta(days=lookahead_days)
    rows: list[dict[str, Any]] = []
    for entry in read_json(history_path):
        campaign = campaign_metadata_from_history_entry(entry)
        scheduled_for = entry.get("scheduled_for")
        if not campaign or not scheduled_for:
            continue
        try:
            scheduled_at = parse_datetime(str(scheduled_for))
        except ValueError:
            continue
        if scheduled_at < window_start or scheduled_at > window_end:
            continue
        rows.append(
            {
                **campaign,
                "scheduled_for": str(scheduled_for),
                "post_type": entry.get("post_type"),
                "posted_for_class": entry.get("posted_for_class"),
                "class_name": entry.get("class_name"),
                "clip_name": entry.get("clip_name"),
                "zernio_post_id": entry.get("zernio_post_id"),
            }
        )
    rows = sorted(rows, key=lambda row: row["scheduled_for"])
    recent = [
        row
        for row in rows
        if parse_datetime(row["scheduled_for"]) <= captured_at
    ]
    upcoming = [
        row
        for row in rows
        if parse_datetime(row["scheduled_for"]) > captured_at
    ]
    return {
        "status": "ok",
        "window_start": iso_z(window_start),
        "window_end": iso_z(window_end),
        "post_count": len(rows),
        "recent_variants": group_campaign_variants(recent),
        "upcoming_variants": group_campaign_variants(upcoming),
        "posts": rows,
    }


def summarize_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    followers = (snapshot.get("instagram") or {}).get("followers") or {}
    subscribers = (snapshot.get("email") or {}).get("subscribers") or {}
    habit_event = (snapshot.get("habit") or {}).get("next_event") or {}
    queues = snapshot.get("queues") or {}
    zernio = snapshot.get("zernio") or {}
    zernio_analytics = zernio.get("analytics") or {}
    plausible = ((snapshot.get("landing_page") or {}).get("plausible") or {})
    day = ((plausible.get("metrics") or {}).get("day") or {})
    funnel_events = day.get("funnel_events") or {}
    campaigns = snapshot.get("campaigns") or {}
    return {
        "instagram_followers": followers.get("count"),
        "instagram_follower_delta": followers.get("delta_since_previous"),
        "email_subscribers": subscribers.get("count"),
        "next_habit_registrations": habit_event.get("registrations"),
        "ig_clip_queue": queues.get("ig_clip_queue"),
        "ig_quote_queue": queues.get("ig_quote_queue"),
        "zernio_status": zernio.get("status"),
        "zernio_failed_posts": zernio.get("failed_count"),
        "zernio_api_errors": zernio.get("api_error_count"),
        "zernio_analytics_status": zernio_analytics.get("status"),
        "landing_page_status": plausible.get("status"),
        "landing_day_visitors": day.get("visitors"),
        "landing_day_pageviews": day.get("pageviews"),
        "habit_register_clicks_day": funnel_events.get("Habit Register Click"),
        "habit_signup_success_day": funnel_events.get("Habit Signup Success"),
        "recent_campaign_variants": [
            variant.get("key")
            for variant in campaigns.get("recent_variants") or []
            if variant.get("key")
        ],
        "upcoming_campaign_variants": [
            variant.get("key")
            for variant in campaigns.get("upcoming_variants") or []
            if variant.get("key")
        ],
    }


def collect_snapshot(
    *,
    captured_at: datetime,
    twy_root: Path,
    data_root: Path,
    zernio_fetch_post: Callable[[str], dict[str, Any]] | None,
    zernio_fetch_analytics: Callable[[str], dict[str, Any]] | None = None,
    zernio_account_health: Callable[[], dict[str, Any] | None] | None,
    zernio_lookback_hours: int = DEFAULT_ZERNIO_LOOKBACK_HOURS,
    zernio_lookahead_hours: int = DEFAULT_ZERNIO_LOOKAHEAD_HOURS,
) -> dict[str, Any]:
    try:
        account_health = zernio_account_health() if zernio_account_health else None
    except Exception as exc:
        account_health = {"status": "error", "error": str(exc)}
    snapshot = {
        "date": captured_at.date().isoformat(),
        "captured_at": iso_z(captured_at),
        "instagram": {
            "followers": latest_instagram_followers(twy_root),
            "zernio_account": account_health,
        },
        "email": {
            "subscribers": latest_email_subscribers(twy_root),
        },
        "habit": {
            "next_event": next_habit_event(data_root, captured_at),
        },
        "queues": queue_snapshot(twy_root),
        "campaigns": campaign_snapshot(
            history_path=twy_root / "clips/state/ig_history.json",
            captured_at=captured_at,
        ),
        "zernio": collect_zernio_recent_status(
            history_path=twy_root / "clips/state/ig_history.json",
            captured_at=captured_at,
            fetch_post=zernio_fetch_post,
            fetch_analytics=zernio_fetch_analytics,
            lookback_hours=zernio_lookback_hours,
            lookahead_hours=zernio_lookahead_hours,
        ),
        "landing_page": {
            "plausible": collect_plausible_status(captured_at=captured_at),
        },
        "external_benchmarks": {
            "socialblade": collect_socialblade_status(),
        },
    }
    snapshot["summary"] = summarize_snapshot(snapshot)
    return snapshot


def save_snapshot(snapshot: dict[str, Any], *, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"{snapshot['date']}.json"
    path.write_text(json.dumps(snapshot, indent=2, sort_keys=True) + "\n")
    return path


def system_warnings_channel() -> str:
    return (
        os.getenv("SOCIAL_GROWTH_WARNINGS_CHANNEL", "").strip()
        or os.getenv("SENDGRID_SYSTEM_WARNINGS_CHANNEL", "").strip()
        or os.getenv("IG_SYSTEM_WARNINGS_SLACK_CHANNEL", "").strip()
        or DEFAULT_SYSTEM_WARNINGS_CHANNEL
    )


def slack_post_warning(channel: str, text: str) -> None:
    token = os.getenv("SLACK_BOT_TOKEN", "").strip()
    if not token:
        print(f"[slack] {channel}: {text}")
        return
    response = requests.post(
        "https://slack.com/api/chat.postMessage",
        headers={"Authorization": f"Bearer {token}"},
        json={"channel": channel, "text": text},
        timeout=10,
    )
    response.raise_for_status()
    payload = response.json()
    if not payload.get("ok"):
        raise RuntimeError(f"Slack API error: {payload.get('error')}")


def zernio_token_warning_event(
    snapshot: dict[str, Any],
    *,
    token_warning_hours: int,
) -> dict[str, str] | None:
    account = ((snapshot.get("instagram") or {}).get("zernio_account") or {})
    token_status = account.get("token_status") or {}
    expires_at = token_status.get("expires_at")
    needs_refresh = token_status.get("needs_refresh")
    valid = token_status.get("valid")
    if not expires_at:
        return None
    captured_at = parse_datetime(str(snapshot["captured_at"]))
    expiry = parse_datetime(str(expires_at))
    hours_left = (expiry - captured_at).total_seconds() / 3600
    if valid is False:
        reason = "is invalid"
    elif needs_refresh:
        reason = "needs refresh"
    elif hours_left <= token_warning_hours:
        reason = f"expires within {token_warning_hours} hours"
    else:
        return None
    username = account.get("username") or "unknown account"
    return {
        "key": f"zernio_token:{expires_at}",
        "text": (
            ":warning: TWY social growth: Zernio Instagram token "
            f"for {username} {reason}. Expires at {expires_at}."
        ),
    }


def warning_events(
    snapshot: dict[str, Any],
    *,
    token_warning_hours: int = DEFAULT_ZERNIO_TOKEN_WARNING_HOURS,
    follower_drop_threshold: int = DEFAULT_FOLLOWER_DROP_ALERT_THRESHOLD,
) -> list[dict[str, str]]:
    events: list[dict[str, str]] = []
    zernio = snapshot.get("zernio") or {}
    for row in zernio.get("failed") or []:
        post_id = row.get("zernio_post_id") or "unknown"
        title = row.get("title") or "untitled post"
        scheduled_for = row.get("scheduled_for") or "unknown time"
        status = row.get("platform_status") or row.get("post_status") or "failed"
        events.append(
            {
                "key": f"zernio_failed:{post_id}",
                "text": (
                    ":warning: TWY social growth: Zernio publish failed "
                    f"for {title} scheduled {scheduled_for}. "
                    f"Post id {post_id}. Status {status}."
                ),
            }
        )
    for row in zernio.get("api_errors") or []:
        post_id = row.get("zernio_post_id") or "unknown"
        scheduled_for = row.get("scheduled_for") or "unknown time"
        error = row.get("error") or "unknown error"
        events.append(
            {
                "key": f"zernio_api_error:{post_id}:{scheduled_for}",
                "text": (
                    ":warning: TWY social growth: Zernio status check "
                    f"failed for post {post_id} scheduled {scheduled_for}: "
                    f"{error}"
                ),
            }
        )
    analytics = zernio.get("analytics") or {}
    for row in analytics.get("api_errors") or []:
        post_id = row.get("zernio_post_id") or "unknown"
        scheduled_for = row.get("scheduled_for") or "unknown time"
        error = row.get("error") or "unknown error"
        events.append(
            {
                "key": f"zernio_analytics_api_error:{post_id}:{scheduled_for}",
                "text": (
                    ":warning: TWY social growth: Zernio analytics check "
                    f"failed for post {post_id} scheduled {scheduled_for}: "
                    f"{error}"
                ),
            }
        )
    plausible = ((snapshot.get("landing_page") or {}).get("plausible") or {})
    if plausible.get("status") == "error":
        site_id = plausible.get("site_id") or "unknown site"
        error = plausible.get("error") or "unknown error"
        events.append(
            {
                "key": f"plausible_error:{site_id}:{error}",
                "text": (
                    ":warning: TWY social growth: Plausible funnel collection "
                    f"failed for {site_id}: {error}"
                ),
            }
        )
    followers = ((snapshot.get("instagram") or {}).get("followers") or {})
    delta = followers.get("delta_since_previous")
    if (
        follower_drop_threshold > 0
        and isinstance(delta, int)
        and delta <= -follower_drop_threshold
    ):
        count = followers.get("count")
        previous_count = followers.get("previous_count")
        snapshot_date = followers.get("snapshot_date") or "unknown date"
        previous_date = followers.get("previous_snapshot_date") or "unknown prior date"
        events.append(
            {
                "key": f"instagram_follower_drop:{snapshot_date}:{previous_date}:{delta}",
                "text": (
                    ":warning: TWY social growth: Instagram followers dropped "
                    f"by {abs(delta)} from {previous_count} to {count} "
                    f"({previous_date} -> {snapshot_date}, threshold {follower_drop_threshold})."
                ),
            }
        )
    token_event = zernio_token_warning_event(
        snapshot,
        token_warning_hours=token_warning_hours,
    )
    if token_event:
        events.append(token_event)
    return events


def load_alert_state(state_path: Path) -> dict[str, Any]:
    if not state_path.exists():
        return {"sent": {}}
    try:
        state = read_json(state_path)
    except Exception:
        return {"sent": {}}
    if not isinstance(state, dict):
        return {"sent": {}}
    sent = state.get("sent")
    if not isinstance(sent, dict):
        state["sent"] = {}
    return state


def save_alert_state(state: dict[str, Any], state_path: Path) -> None:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")


def post_new_warning_events(
    events: list[dict[str, str]],
    *,
    state_path: Path,
    channel: str,
    post_warning: Callable[[str, str], None],
    sent_at: datetime,
) -> dict[str, int]:
    state = load_alert_state(state_path)
    sent = state["sent"]
    result = {"posted": 0, "skipped": 0, "failed": 0}
    for event in events:
        key = event["key"]
        if key in sent:
            result["skipped"] += 1
            continue
        try:
            post_warning(channel, event["text"])
        except Exception as exc:
            print(f"Warning: could not post Slack alert {key}: {exc}")
            result["failed"] += 1
            continue
        sent[key] = {
            "sent_at": iso_z(sent_at),
            "text": event["text"],
        }
        result["posted"] += 1
    if result["posted"]:
        save_alert_state(state, state_path)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Collect TWY social growth evidence")
    parser.add_argument("--dry-run", action="store_true", help="Print JSON without writing it")
    parser.add_argument("--lookback-hours", type=int, default=DEFAULT_ZERNIO_LOOKBACK_HOURS)
    parser.add_argument("--lookahead-hours", type=int, default=DEFAULT_ZERNIO_LOOKAHEAD_HOURS)
    parser.add_argument(
        "--token-warning-hours",
        type=int,
        default=DEFAULT_ZERNIO_TOKEN_WARNING_HOURS,
    )
    parser.add_argument(
        "--follower-drop-threshold",
        type=int,
        default=DEFAULT_FOLLOWER_DROP_ALERT_THRESHOLD,
    )
    args = parser.parse_args()

    load_env()
    now = datetime.now(timezone.utc)
    root = default_twy_root()
    data_dir = default_data_root()
    snapshot = collect_snapshot(
        captured_at=now,
        twy_root=root,
        data_root=data_dir,
        zernio_fetch_post=zernio_fetcher_from_env(),
        zernio_fetch_analytics=zernio_analytics_fetcher_from_env(),
        zernio_account_health=zernio_account_health_from_env,
        zernio_lookback_hours=args.lookback_hours,
        zernio_lookahead_hours=args.lookahead_hours,
    )
    if args.dry_run:
        print(json.dumps(snapshot, indent=2, sort_keys=True))
        events = warning_events(
            snapshot,
            token_warning_hours=args.token_warning_hours,
            follower_drop_threshold=args.follower_drop_threshold,
        )
        for event in events:
            print(f"[DRY RUN alert] {event['text']}")
        return 0
    destination = save_snapshot(snapshot, output_dir=data_dir / "social_growth")
    print(f"Saved social growth snapshot to {destination}")
    zernio = snapshot.get("zernio") or {}
    if zernio.get("failed_count"):
        print(f"Zernio failed posts in window: {zernio['failed_count']}")
    if zernio.get("api_error_count"):
        print(f"Zernio API errors in window: {zernio['api_error_count']}")
    events = warning_events(
        snapshot,
        token_warning_hours=args.token_warning_hours,
        follower_drop_threshold=args.follower_drop_threshold,
    )
    alert_result = post_new_warning_events(
        events,
        state_path=data_dir / "social_growth" / ".alert_state.json",
        channel=system_warnings_channel(),
        post_warning=slack_post_warning,
        sent_at=now,
    )
    if events:
        print(
            "Slack alerts: "
            f"{alert_result['posted']} posted, "
            f"{alert_result['skipped']} skipped, "
            f"{alert_result['failed']} failed"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
