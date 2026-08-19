#!/usr/bin/env python3
"""Capture Instagram story metrics before they expire.

Every other performance collector here can be re-run against history: Zernio
answers /analytics for any post id regardless of age, so a missed run is a
late answer. This one cannot. A story leaves Meta's /stories edge roughly 24
hours after posting, there is no archive endpoint, and the media id is the
only handle on it, so a story nobody read is gone permanently and silently.

That single fact shapes everything below:

- It UPSERTS by story id and never rewrites a row from scratch. A story is
  read several times across its life and its numbers only grow, so the newest
  reading wins per metric while first_seen and the id survive untouched.
- A story with no metrics is still written. The id is unrecoverable and worth
  keeping on its own; the numbers can be filled in by the next run, which is
  only possible because the row is already there.
- It writes the file only when something changed, so a quiet run leaves the
  mtime alone and the store's timestamp still means "last time a story moved".

The reading itself is twy_platform.meta.stories, which persists nothing. This
module owns the file and the schedule.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, "/root/twy/paths")

from twy_paths import load_env, story_insights_path  # noqa: E402
from twy_platform import meta  # noqa: E402

log = logging.getLogger("story_insights")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _period(story: dict, fallback: datetime) -> tuple[int, int]:
    """The month a story belongs to, from its own timestamp not from today.

    A story posted at 23:50 on the last of the month is read again after
    midnight; without this it would land in two different monthly files under
    the same id.
    """
    stamp = str(story.get("timestamp") or "")
    try:
        when = datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%S%z")
    except (ValueError, TypeError):
        when = fallback
    return when.year, when.month


def read_store(path: Path) -> dict:
    try:
        document = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {"version": 1, "stories": {}}
    if not isinstance(document.get("stories"), dict):
        document["stories"] = {}
    return document


def merge(existing: dict, story: dict, *, seen_at: str) -> tuple[dict, bool]:
    """Fold one reading into the stored row. Returns (row, changed)."""
    row = dict(existing) if existing else {}
    changed = not existing
    if not row:
        row["first_seen"] = seen_at
    for field in ("id", "media_type", "timestamp", "permalink"):
        value = story.get(field)
        if value is not None and row.get(field) != value:
            row[field] = value
            changed = True
    metrics = dict(row.get("metrics") or {})
    for name, value in (story.get("metrics") or {}).items():
        if metrics.get(name) != value:
            metrics[name] = value
            changed = True
    row["metrics"] = metrics
    if changed:
        row["last_seen"] = seen_at
    return row, changed


def capture(stories: list, *, now: datetime, write=True) -> dict:
    seen_at = now.isoformat()
    tally = {"seen": len(stories), "new": 0, "updated": 0, "unchanged": 0}
    by_period: dict[tuple[int, int], dict] = {}
    for story in stories:
        key = _period(story, now)
        document = by_period.get(key)
        if document is None:
            document = read_store(story_insights_path(*key))
            by_period[key] = document
        story_id = str(story.get("id") or "")
        if not story_id:
            continue
        before = document["stories"].get(story_id)
        row, changed = merge(before, story, seen_at=seen_at)
        document["stories"][story_id] = row
        if before is None:
            tally["new"] += 1
        elif changed:
            tally["updated"] += 1
        else:
            tally["unchanged"] += 1
    if write:
        for key, document in by_period.items():
            if not document["stories"]:
                continue
            document["updated_at"] = seen_at
            path = story_insights_path(*key)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n")
    tally["periods"] = [f"{y:04d}-{m:02d}" for y, m in sorted(by_period)]
    return tally


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true",
                        help="read and report, write nothing")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    load_env()
    token = meta.page_access_token()
    if not token:
        log.error("META_PAGE_ACCESS_TOKEN is not set; nothing can be captured")
        return 1
    stories = meta.stories(token)
    tally = capture(stories, now=datetime.now(timezone.utc), write=not args.dry_run)
    log.info("seen=%(seen)s new=%(new)s updated=%(updated)s unchanged=%(unchanged)s"
             % tally)
    print(json.dumps(tally, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
