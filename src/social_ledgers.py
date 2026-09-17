"""One view of a platform's posts across the old ledger and the inventory.

Since 2026-09-17 the publishers (clips src/publishers) write only the social
inventory, so a platform's posts are its old ledger rows (the schedulers that
still run, and history) plus the inventory placements of the publishers that
serve it. Both readers in announce, the per-post performance collector and
the daily social growth report, go through here so they agree.
"""

from __future__ import annotations

import json
from pathlib import Path

from twy_paths import clips_inventory_path

# platform -> the publishers that write only the inventory for it
INVENTORY_PUBLISHERS = {
    "youtube": ("yt_short",),
    "facebook": ("fb_reel", "fb_quote_reel", "fb_photo"),
}
NOT_LIVE = {"withdrawn", "cancelled"}


def inventory_rows(publishers: tuple[str, ...], path: Path | None = None) -> tuple[list[dict], set[str]]:
    """Placements by the named publishers, shaped like ledger rows, and the
    ids of the ones that never reached the platform (withdrawn or cancelled).
    A draft has no slot."""
    path = path or clips_inventory_path()
    try:
        inv = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return [], set()
    rows, not_live = [], set()
    for item in (inv.get("items") or {}).values():
        for p in item.get("placements") or []:
            if p.get("publisher") not in publishers:
                continue
            if p.get("status") in NOT_LIVE:
                not_live.add(str(p.get("zernio_post_id")))
                continue
            if p.get("is_draft"):
                continue
            source = p.get("source") or {}
            rows.append({
                "zernio_post_id": p.get("zernio_post_id"),
                "scheduled_for": p.get("scheduled_for"),
                "class_name": item.get("class_name"),
                "clip_name": item.get("clip_name"),
                "class_type": item.get("class_type"),
                "post_type": p.get("post_type"),
                "kind": item.get("kind"),
                "title": source.get("title"),
                "quote_text": p.get("quote_text"),
                "plan_date": p.get("slot") if p.get("post_type") in ("class", "habit_week") else None,
                "campaign": p.get("campaign"),
                "publisher": p.get("publisher"),
            })
    return rows, not_live


def merge_history(history: list[dict], platform: str, inventory_path: Path | None = None) -> list[dict]:
    """The ledger rows plus, for a platform a publisher now serves, its
    inventory placements, one row per post id. A ledger row the inventory
    marks withdrawn or cancelled is dropped: the post is gone at Zernio."""
    publishers = INVENTORY_PUBLISHERS.get(platform)
    if not publishers:
        return history
    rows, not_live = inventory_rows(publishers, inventory_path)
    history = [row for row in history if str(row.get("zernio_post_id")) not in not_live]
    seen = {str(row.get("zernio_post_id")) for row in history if row.get("zernio_post_id")}
    for row in rows:
        if str(row.get("zernio_post_id")) not in seen:
            history.append(row)
            seen.add(str(row.get("zernio_post_id")))
    return history
