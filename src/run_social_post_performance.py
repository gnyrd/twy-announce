"""Collect per-post social analytics into the durable monthly store.

Default run covers recent posts. ``--backfill`` walks the whole publish
history, which is possible because Zernio answers /analytics for any post id
regardless of age; the 72-hour window in social_growth_report is this
collector's own choice, not a provider limit.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import requests
from twy_paths import (
    clips_inventory_path,
    facebook_post_performance_path,
    load_env,
    social_post_performance_path,
    twy_root,
    youtube_post_performance_path,
)

from social_post_performance import materialize_post_performance


log = logging.getLogger("social_post_performance")

DEFAULT_BASE_URL = "https://zernio.com/api/v1"
# 202 means the provider accepted the question and has no answer yet. It is a
# normal reply, not an error, and every Instagram story returns it.
PENDING_STATUSES = {202, 402, 424}

# Each platform has its own publish ledger, its own Zernio account id and its
# own monthly store. The durable store shape is identical, which is why one
# collector serves all three by swapping these things. YouTube differs in one
# place only: its numbers come from the Data API, see youtube_fetcher.
HISTORY_FILES = {
    "instagram": "ig_history.json",
    "facebook": "fb_history.json",
    "youtube": "yt_shorts_history.json",
}
ACCOUNT_ENV = {
    "instagram": "ZERNIO_INSTAGRAM_ACCOUNT_ID",
    "facebook": "ZERNIO_FACEBOOK_ACCOUNT_ID",
    "youtube": "ZERNIO_YOUTUBE_ACCOUNT_ID",
}
PERFORMANCE_PATHS = {
    "instagram": social_post_performance_path,
    "facebook": facebook_post_performance_path,
    "youtube": youtube_post_performance_path,
}
YOUTUBE_VIDEOS_URL = "https://www.googleapis.com/youtube/v3/videos"
# YouTube Analytics API traffic source types, folded into four buckets the
# report can read. Everything else is "other".
TRAFFIC_BUCKETS = {
    "SHORTS": "trafficShortsFeedPct",
    "YT_SEARCH": "trafficSearchPct",
    "YT_CHANNEL": "trafficChannelPct",
    "SUBSCRIBER": "trafficChannelPct",
    "NOTIFICATION": "trafficChannelPct",
    "EXT_URL": "trafficExternalPct",
}


def history_path(platform: str = "instagram") -> Path:
    return twy_root() / "clips" / "state" / HISTORY_FILES[platform]


def read_history(path: Path) -> list[dict]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return []
    return [row for row in payload if isinstance(row, dict)]


# The publishers that write only the social inventory live in social_ledgers;
# these names stay so the collector's callers and tests read the same way.
from social_ledgers import INVENTORY_PUBLISHERS, NOT_LIVE, merge_history  # noqa: E402,F401
from social_ledgers import inventory_rows as _inventory_rows  # noqa: E402


def inventory_rows(publishers: tuple[str, ...], path: Path | None = None) -> tuple[list[dict], set[str]]:
    return _inventory_rows(publishers, path or clips_inventory_path())


def platform_history(platform: str) -> list[dict]:
    """The ledger rows plus the inventory placements of the publishers that
    serve the platform, one row per post id, minus rows the inventory marks
    withdrawn or cancelled."""
    return merge_history(read_history(history_path(platform)), platform, clips_inventory_path())


def analytics_fetcher(account_env: str = "ZERNIO_INSTAGRAM_ACCOUNT_ID"):
    api_key = os.getenv("ZERNIO_API_KEY", "").strip()
    if not api_key:
        raise SystemExit("ZERNIO_API_KEY is not configured")
    account_id = os.getenv(account_env, "").strip()
    base_url = os.getenv("ZERNIO_BASE_URL", DEFAULT_BASE_URL).rstrip("/")
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    def fetch(post_id: str) -> tuple[int, dict]:
        params = {"postId": post_id}
        if account_id:
            params["accountId"] = account_id
        response = requests.get(
            f"{base_url}/analytics", headers=headers, params=params, timeout=30
        )
        try:
            payload = response.json()
        except ValueError:
            payload = {}
        return response.status_code, payload

    return fetch


def analytics_from_payload(payload: dict) -> dict | None:
    metrics = payload.get("analytics")
    if isinstance(metrics, dict):
        return metrics
    platforms = payload.get("platformAnalytics")
    if isinstance(platforms, list) and platforms:
        candidate = platforms[0].get("analytics")
        if isinstance(candidate, dict):
            return candidate
    return None


def youtube_video_id(payload: dict) -> str | None:
    """The YouTube video id Zernio recorded for a post, once it has published."""
    for entry in payload.get("platformAnalytics") or []:
        if isinstance(entry, dict) and entry.get("platform") == "youtube" and entry.get("platformPostId"):
            return str(entry["platformPostId"])
    return None


def youtube_analytics_client():
    """The YouTube Analytics API for the channel, or None when no token is
    configured. The token (YOUTUBE_ANALYTICS_OAUTH_TOKEN_FILE, minted for the
    Tiffany Wood Yoga brand account with the yt-analytics.readonly scope)
    refreshes itself and is written back like the Search Console one."""
    token_file = os.getenv("YOUTUBE_ANALYTICS_OAUTH_TOKEN_FILE", "").strip()
    if not token_file:
        return None
    if not Path(token_file).exists():
        raise SystemExit(f"YOUTUBE_ANALYTICS_OAUTH_TOKEN_FILE does not exist: {token_file}")
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    creds = Credentials.from_authorized_user_file(token_file)
    if not creds.valid:
        creds.refresh(Request())
        Path(token_file).write_text(creds.to_json())
    return build("youtubeAnalytics", "v2", credentials=creds, cache_discovery=False)


def analytics_query(client, video_id: str, start: str, end: str, **params) -> list:
    """Rows of one reports.query for one video, [] when YouTube has none yet."""
    response = client.reports().query(
        ids="channel==MINE", startDate=start, endDate=end, filters=f"video=={video_id}", **params
    ).execute()
    return response.get("rows") or []


def youtube_analytics_metrics(client, video_id: str, published_at: str | None) -> dict:
    """What Studio's Reach and Engagement tabs show, flattened for the store.

    Two queries: the totals (engaged views, watch time, average view duration
    and percentage, likes, comments, shares, subscribers gained and lost) and
    the traffic split by source, folded into Shorts feed / search / channel
    (channel pages, subscriptions, notifications) / external / other, as a
    percentage of views. "Stayed to watch" is engaged views over views.
    YouTube's analytics lag a day or two behind the counters, so a key is
    written only when YouTube returned it: the store keeps counts at their
    maximum and rates at their latest, and an empty answer must not zero a
    rate that was real yesterday.
    """
    start = (published_at or "")[:10] or (datetime.now(timezone.utc) - timedelta(days=30)).date().isoformat()
    end = datetime.now(timezone.utc).date().isoformat()
    out: dict = {}
    totals = analytics_query(
        client, video_id, start, end,
        metrics="views,engagedViews,estimatedMinutesWatched,averageViewDuration,averageViewPercentage,likes,comments,shares,subscribersGained,subscribersLost",
    )
    if totals and totals[0]:
        row = totals[0]
        keys = ("views", "engagedViews", "watchMinutes", "avgViewDurationSec", "avgViewPct", "likes", "comments", "shares", "subscribersGained", "subscribersLost")
        got = {key: value for key, value in zip(keys, row) if value is not None}
        # views, likes and comments stay the Data API's live counters; the
        # analytics figures lag two days and would drag them backwards.
        for key in ("engagedViews", "watchMinutes", "avgViewDurationSec", "shares", "subscribersGained", "subscribersLost"):
            if key in got:
                out[key] = int(got[key])
        if "avgViewPct" in got:
            out["avgViewPct"] = round(float(got["avgViewPct"]), 1)
        views = int(got.get("views") or 0)
        if views and "engagedViews" in got:
            out["stayedPct"] = round(int(got["engagedViews"]) / views * 100, 1)
    sources = analytics_query(client, video_id, start, end, dimensions="insightTrafficSourceType", metrics="views", sort="-views")
    total = sum(int(r[1] or 0) for r in sources)
    if total:
        buckets: dict = {}
        for source, views in sources:
            key = TRAFFIC_BUCKETS.get(str(source), "trafficOtherPct")
            buckets[key] = buckets.get(key, 0) + int(views or 0)
        for key, views in buckets.items():
            out[key] = round(views / total * 100, 1)
    return out


def youtube_fetcher():
    """Measure a Short through the YouTube Data API, with Zernio only naming
    the video. Zernio's /analytics answered 202 "being synced" for the first
    Short seven hours after it published (2026-09-15), while the Data API
    answers for any public video at once with the channel's own key. The
    payload comes back in the Zernio shape so collect() needs no branch."""
    zernio = analytics_fetcher(ACCOUNT_ENV["youtube"])
    api_key = os.getenv("YOUTUBE_API_KEY", "").strip()
    if not api_key:
        raise SystemExit("YOUTUBE_API_KEY is not configured")
    analytics_client = youtube_analytics_client()

    def fetch(post_id: str) -> tuple[int, dict]:
        status, payload = zernio(post_id)
        if status >= 400 and status not in PENDING_STATUSES:
            return status, payload
        video_id = youtube_video_id(payload)
        if not video_id:
            return 202, payload   # not published yet, or Zernio has not named the video
        response = requests.get(
            YOUTUBE_VIDEOS_URL,
            params={"part": "statistics", "id": video_id, "key": api_key},
            timeout=30,
        )
        try:
            data = response.json()
        except ValueError:
            data = {}
        if response.status_code >= 400:
            return response.status_code, data
        items = data.get("items") or []
        if not items:
            return 404, {"error": "video " + video_id + " is not on YouTube"}
        stats = items[0].get("statistics") or {}
        analytics = {
            key: int(stats[field])
            for key, field in (("views", "viewCount"), ("likes", "likeCount"), ("comments", "commentCount"))
            if str(stats.get(field, "")).isdigit()
        }
        # YouTube reports no reach, so the rate the other two platforms carry
        # as engagementRate is likes plus comments per hundred views here.
        # Stated on the stats page and in the weekly review wherever it shows.
        if analytics.get("views"):
            analytics["engagementRate"] = round(
                (analytics.get("likes", 0) + analytics.get("comments", 0)) / analytics["views"] * 100, 2
            )
        if analytics_client is not None:
            try:
                analytics.update(youtube_analytics_metrics(analytics_client, video_id, payload.get("publishedAt")))
            except Exception as exc:  # the counters above still stand; the analytics come next tick
                log.warning("youtube analytics failed for %s: %s", video_id, exc)
        analytics["lastUpdated"] = datetime.now(timezone.utc).isoformat()
        return 200, {
            "analytics": analytics,
            "platformPostId": video_id,
            "platformPostUrl": "https://www.youtube.com/shorts/" + video_id,
            "syncStatus": "youtube-data-api",
        }

    return fetch


def fetcher_for(platform: str):
    return youtube_fetcher() if platform == "youtube" else analytics_fetcher(ACCOUNT_ENV[platform])


def with_class_type(rows: list[dict]) -> list[dict]:
    """The Shorts ledger carries class_name only; the store keys by class_type
    like the other two platforms, so derive it (2026-07-23_expansion -> expansion)."""
    out = []
    for row in rows:
        if not row.get("class_type") and "_" in str(row.get("class_name") or ""):
            row = {**row, "class_type": str(row["class_name"]).split("_", 1)[1]}
        out.append(row)
    return out


def select_rows(history: list[dict], *, backfill: bool, since_days: int, now: datetime) -> list[dict]:
    if backfill:
        return [row for row in history if row.get("zernio_post_id") and row.get("scheduled_for")]
    cutoff = now - timedelta(days=since_days)
    selected = []
    for row in history:
        if not row.get("zernio_post_id") or not row.get("scheduled_for"):
            continue
        try:
            scheduled_at = datetime.fromisoformat(str(row["scheduled_for"]).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            continue
        if scheduled_at >= cutoff:
            selected.append(row)
    return selected


def collect(rows: list[dict], fetch) -> tuple[list[dict], dict]:
    collected: list[dict] = []
    tally = {"measured": 0, "pending": 0, "errors": 0}
    for row in rows:
        post_id = str(row["zernio_post_id"])
        try:
            status, payload = fetch(post_id)
        except Exception as exc:  # provider or network failure
            log.warning("analytics fetch failed for %s: %s", post_id, exc)
            tally["errors"] += 1
            continue
        if status >= 400 and status not in PENDING_STATUSES:
            log.warning("analytics HTTP %s for %s", status, post_id)
            tally["errors"] += 1
            continue
        pending = status in PENDING_STATUSES
        metrics = None if pending else analytics_from_payload(payload)
        if metrics:
            tally["measured"] += 1
        else:
            tally["pending"] += 1
        collected.append(
            {
                **row,
                "analytics": metrics,
                "platform_post_url": payload.get("platformPostUrl"),
                "sync_status": payload.get("syncStatus"),
                "provider_status": "pending" if pending else "ok",
            }
        )
    return collected, tally


def main() -> int:
    logging.basicConfig(format="%(asctime)s %(levelname)s %(message)s", level=logging.INFO)
    load_env()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--platform",
        choices=("instagram", "facebook", "youtube"),
        default="instagram",
        help="which publish history and Zernio account to collect",
    )
    parser.add_argument("--backfill", action="store_true", help="walk the whole publish history")
    parser.add_argument("--since-days", type=int, default=10, help="incremental window in days")
    parser.add_argument("--dry-run", action="store_true", help="collect but write nothing")
    args = parser.parse_args()

    now = datetime.now(timezone.utc)
    platform = args.platform
    history = platform_history(platform)
    if not history:
        log.error("no publish history at %s", history_path(platform))
        return 1

    rows = select_rows(history, backfill=args.backfill, since_days=args.since_days, now=now)
    if platform == "youtube":
        rows = with_class_type(rows)
    log.info("%s %s posts selected of %s in history", len(rows), platform, len(history))
    collected, tally = collect(rows, fetcher_for(platform))

    if args.dry_run:
        log.info("dry run (%s): %s", platform, json.dumps(tally))
        return 0

    written = materialize_post_performance(collected, now, path_for=PERFORMANCE_PATHS[platform])
    log.info(
        "measured=%(measured)s pending=%(pending)s errors=%(errors)s" % tally
        + " months=%s" % len(written)
    )
    for path in written:
        log.info("wrote %s", path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
