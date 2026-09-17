"""The one view of a platform's posts across the old ledger and the inventory."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import social_ledgers


def test_merge_adds_facebook_publisher_placements_and_drops_withdrawn_ledger_rows(tmp_path):
    inventory = tmp_path / "inventory.json"
    inventory.write_text(json.dumps({"version": 1, "items": {
        "clip:2026-09-14_strength/02_w": {"kind": "clip", "class_name": "2026-09-14_strength", "clip_name": "02_w", "class_type": "strength",
            "placements": [
                {"publisher": "fb_reel", "zernio_post_id": "new1", "scheduled_for": "2026-09-21T08:00:00-06:00", "status": "scheduled",
                 "post_type": "class", "slot": "2026-09-22", "source": {"title": "2026-09-22"}},
                {"publisher": "fb_reel", "zernio_post_id": "oldgone", "scheduled_for": "2026-09-18T07:00:00-06:00", "status": "withdrawn",
                 "post_type": "fb_reel", "source": {}},
                {"publisher": "yt_short", "zernio_post_id": "yt1", "scheduled_for": "2026-09-21T08:00:00-06:00", "status": "scheduled",
                 "post_type": "class", "source": {}},
            ]},
        "quote:2026-06-25_expansion/04_w": {"kind": "quote", "class_name": "2026-06-25_expansion", "clip_name": "04_w", "class_type": "expansion",
            "placements": [
                {"publisher": "fb_photo", "zernio_post_id": "card1", "scheduled_for": "2026-09-17T12:30:00-06:00", "status": "scheduled",
                 "post_type": "quote", "quote_text": "Words.", "campaign": None, "source": {"title": "TWY FB Quote Card"}},
            ]},
    }}))
    ledger = [
        {"post_type": "fb_link", "zernio_post_id": "link1", "scheduled_for": "2026-09-23T07:00:00-06:00", "slug": "a-post"},
        {"post_type": "fb_reel", "zernio_post_id": "oldgone", "scheduled_for": "2026-09-18T07:00:00-06:00", "cancelled": True},
        {"post_type": "fb_reel", "zernio_post_id": "new1", "scheduled_for": "2026-09-21T08:00:00-06:00"},   # already known: not doubled
    ]
    merged = social_ledgers.merge_history(ledger, "facebook", inventory)
    assert [r["zernio_post_id"] for r in merged] == ["link1", "new1", "card1"]
    assert merged[2]["quote_text"] == "Words." and merged[2]["publisher"] == "fb_photo" and merged[2]["kind"] == "quote"
    assert social_ledgers.merge_history(ledger, "instagram", inventory) == ledger
