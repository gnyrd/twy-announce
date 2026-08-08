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
from twy_paths import load_env, twy_root

from social_post_performance import materialize_post_performance


log = logging.getLogger("social_post_performance")

DEFAULT_BASE_URL = "https://zernio.com/api/v1"
# 202 means the provider accepted the question and has no answer yet. It is a
# normal reply, not an error, and every Instagram story returns it.
PENDING_STATUSES = {202, 402, 424}


def history_path() -> Path:
    return twy_root() / "clips" / "state" / "ig_history.json"


def read_history(path: Path) -> list[dict]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return []
    return [row for row in payload if isinstance(row, dict)]


def analytics_fetcher():
    api_key = os.getenv("ZERNIO_API_KEY", "").strip()
    if not api_key:
        raise SystemExit("ZERNIO_API_KEY is not configured")
    account_id = os.getenv("ZERNIO_INSTAGRAM_ACCOUNT_ID", "").strip()
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
    parser.add_argument("--backfill", action="store_true", help="walk the whole publish history")
    parser.add_argument("--since-days", type=int, default=10, help="incremental window in days")
    parser.add_argument("--dry-run", action="store_true", help="collect but write nothing")
    args = parser.parse_args()

    now = datetime.now(timezone.utc)
    history = read_history(history_path())
    if not history:
        log.error("no publish history at %s", history_path())
        return 1

    rows = select_rows(history, backfill=args.backfill, since_days=args.since_days, now=now)
    log.info("%s posts selected of %s in history", len(rows), len(history))
    collected, tally = collect(rows, analytics_fetcher())

    if args.dry_run:
        log.info("dry run: %s", json.dumps(tally))
        return 0

    written = materialize_post_performance(collected, now)
    log.info(
        "measured=%(measured)s pending=%(pending)s errors=%(errors)s" % tally
        + " months=%s" % len(written)
    )
    for path in written:
        log.info("wrote %s", path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
