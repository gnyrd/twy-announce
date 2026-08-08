"""Durable per-post social performance, one document per month.

Zernio answers ``GET /analytics?postId=`` for any post it has ever published,
but the collector only ever asked inside a 72-hour window, so the numbers were
read once and thrown away. This module keeps them, in the same shape and with
the same guarantees as newsletter_performance: one file per month, counts that
only ever move forward, and milestone snapshots taken once the post has aged.

Breadth is deliberate. Every metric the provider returns is stored, including
ones nothing reads yet, because a metric that was never captured cannot be
recovered later. Classification is by value type rather than by an allowlist,
so a new provider metric lands here without a code change:

  int   -> a count. Durable: the stored value never decreases.
  float -> a rate. Latest wins, because rates move both ways.
  str   -> a label such as lastUpdated. Latest wins.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from twy_paths import social_post_performance_path
from twy_platform import locked_json


GROWTH_ACTIONS = ("follows", "saves", "shares", "clicks")


def normalized_metrics(analytics: dict | None) -> dict:
    """Every metric the provider returned, unfiltered, with growth actions added."""
    values: dict = {}
    for key, value in (analytics or {}).items():
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float, str)):
            values[key] = value
    if any(isinstance(values.get(key), int) for key in GROWTH_ACTIONS):
        values["growth_actions"] = sum(
            int(values.get(key) or 0)
            for key in GROWTH_ACTIONS
            if isinstance(values.get(key), int)
        )
    return values


def _durable_metrics(existing: object, current: dict) -> dict:
    """Merge a fresh read over a stored one without ever losing a count."""
    if not isinstance(existing, dict):
        return dict(current)
    merged = dict(existing)
    for key, value in current.items():
        stored = merged.get(key)
        if isinstance(value, int) and isinstance(stored, int):
            merged[key] = max(stored, value)
        else:
            merged[key] = value
    return merged


def _empty_document(period: str, now: datetime) -> dict:
    return {
        "version": 1,
        "period": period,
        "updated_at": now.isoformat(),
        "posts": {},
    }


def _validate_document(document: object, path: Path) -> dict:
    if not isinstance(document, dict):
        raise ValueError(f"invalid social post performance: {path}")
    if document.get("version") != 1:
        raise ValueError(f"unsupported social post performance: {path}")
    if not isinstance(document.get("posts"), dict):
        raise ValueError(f"invalid social post performance posts: {path}")
    return document


def _period(scheduled_at: datetime) -> tuple[int, int]:
    return scheduled_at.year, scheduled_at.month


def materialize_post_performance(
    posts,
    now: datetime,
    *,
    path_for=social_post_performance_path,
) -> list[Path]:
    """Write each post's newest reading into its month's document.

    ``posts`` is an iterable of dicts carrying at least ``zernio_post_id``,
    ``scheduled_for`` and ``analytics``. A post whose analytics are absent is
    still recorded, so an unmeasured post is visible as unmeasured rather than
    missing.
    """
    grouped: dict[tuple[int, int], list[dict]] = {}
    for post in posts:
        scheduled_for = post.get("scheduled_for")
        if not post.get("zernio_post_id") or not scheduled_for:
            continue
        try:
            scheduled_at = datetime.fromisoformat(str(scheduled_for).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            continue
        grouped.setdefault(_period(scheduled_at), []).append({**post, "_scheduled_at": scheduled_at})

    written: list[Path] = []
    for (year, month), rows in sorted(grouped.items()):
        period = f"{year:04d}-{month:02d}"
        path = Path(path_for(year, month))
        with locked_json(path, default=_empty_document(period, now)) as loaded:
            document = _validate_document(loaded, path)
            stored = document["posts"]
            for row in rows:
                post_id = str(row["zernio_post_id"])
                scheduled_at = row["_scheduled_at"]
                existing = stored.get(post_id) or {}
                current = normalized_metrics(row.get("analytics"))
                # A post that has not gone out yet still answers with a full
                # analytics object of zeros. That is not a measurement, and
                # counting it as one would put phantom zeros into every average.
                published = scheduled_at <= now
                measured = bool(current) and published
                # Stickiness only carries a measurement of a post that was
                # actually published, so a reading taken before a post went out
                # corrects itself on the next run instead of persisting.
                has_metrics = measured or bool(
                    existing.get("has_metrics") and existing.get("published")
                )
                milestones = existing.get("milestones")
                if not isinstance(milestones, dict):
                    milestones = {}
                merged = (
                    _durable_metrics(existing.get("current"), current)
                    if measured
                    else existing.get("current")
                )
                entry = {
                    "zernio_post_id": post_id,
                    "scheduled_for": scheduled_at.isoformat(),
                    "post_type": row.get("post_type") or existing.get("post_type"),
                    "clip_name": row.get("clip_name") or existing.get("clip_name"),
                    "class_name": row.get("class_name") or existing.get("class_name"),
                    "platform_post_url": (
                        row.get("platform_post_url")
                        or (current or {}).get("platformPostUrl")
                        or existing.get("platform_post_url")
                    ),
                    "sync_status": row.get("sync_status") or existing.get("sync_status"),
                    "provider_status": row.get("provider_status") or existing.get("provider_status"),
                    "published": published or bool(existing.get("published")),
                    "has_metrics": has_metrics,
                    "current": merged,
                    "milestones": milestones,
                }
                for label, threshold in (("24_hour", timedelta(hours=24)), ("7_day", timedelta(days=7))):
                    if (
                        label not in milestones
                        and merged
                        and now - scheduled_at >= threshold
                    ):
                        milestones[label] = {
                            "captured_at": now.isoformat(),
                            "metrics": dict(merged),
                        }
                stored[post_id] = entry
            document.update({"version": 1, "period": period, "updated_at": now.isoformat()})
        written.append(path)
    return written
