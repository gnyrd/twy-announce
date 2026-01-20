#!/usr/bin/env python3
"""Refresh a trimmed snapshot of Marvelous (Namastream) events.

Runs independently of the reminder script so we can sync the source-of-truth
calendar on a schedule (e.g. cron at 9am/6pm America/Denver) and keep a
compact local copy under data/marvelous_events.json.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests
from dateutil import parser as date_parser

MARVELOUS_EVENTS_URL = "https://api.namastream.com/api/studios/tiffany-wood-yoga/events"
CACHE_PATH = Path(os.environ.get("MARVELOUS_EVENTS_PATH", "./data/marvelous_events.json"))

# How far ahead to keep in the local cache.
LOOKAHEAD_DAYS = int(os.environ.get("MARVELOUS_LOOKAHEAD_DAYS", "60"))


def fetch_raw_events() -> list[dict[str, Any]]:
    """Fetch events, mimicking browser headers a bit to avoid 403s."""
    headers = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Referer": "https://studio.tiffanywoodyoga.com/calendar",
        "Origin": "https://studio.tiffanywoodyoga.com",
    }
    # Allow user override for debugging if needed
    extra = os.environ.get("MARVELOUS_EXTRA_HEADERS_JSON")
    if extra:
        import json as _json
        try:
            headers.update(_json.loads(extra))
        except Exception:
            pass

    resp = requests.get(MARVELOUS_EVENTS_URL, headers=headers, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    if isinstance(data, list):
        return data
    return []


def trim_and_filter_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop noisy fields and keep only a rolling window of events."""
    now_utc = datetime.now(timezone.utc)
    cutoff = now_utc + timedelta(days=LOOKAHEAD_DAYS)

    out: list[dict[str, Any]] = []
    for ev in events:
        start_raw = ev.get("event_start_datetime")
        if not start_raw:
            continue
        try:
            start_dt = date_parser.parse(start_raw)
        except Exception:
            continue
        if not start_dt.tzinfo:
            start_dt = start_dt.replace(tzinfo=timezone.utc)

        # Only keep reasonably upcoming events
        if start_dt < now_utc - timedelta(days=1):
            continue
        if start_dt > cutoff:
            continue

        out.append(
            {
                "id": ev.get("id"),
                "event_name": ev.get("event_name"),
                "event_start_datetime": ev.get("event_start_datetime"),
                "event_end_datetime": ev.get("event_end_datetime"),
                "event_type": ev.get("event_type"),
                "is_cancelled": ev.get("is_cancelled", False),
                "is_www_event": ev.get("is_www_event", False),
            }
        )

    # Sort by start time ascending for easier inspection
    out.sort(key=lambda e: e.get("event_start_datetime") or "")
    return out


def save_events(events: list[dict[str, Any]]) -> None:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = CACHE_PATH.with_suffix(CACHE_PATH.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(events, f, indent=2, sort_keys=True)
    tmp.replace(CACHE_PATH)


def main() -> int:
    try:
        raw = fetch_raw_events()
    except Exception as e:
        print(f"❌ Failed to fetch Marvelous events: {e}")
        return 1

    trimmed = trim_and_filter_events(raw)
    save_events(trimmed)

    print(f"Synced {len(trimmed)} Marvelous events into {CACHE_PATH} (lookahead={LOOKAHEAD_DAYS}d)")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
