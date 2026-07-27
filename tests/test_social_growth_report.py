from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

import social_growth_report as social_growth


def write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload) + "\n")


def create_marvy_db(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE events (
            id INTEGER PRIMARY KEY,
            event_name TEXT,
            event_start_datetime TEXT,
            event_end_datetime TEXT,
            registration_required INTEGER,
            registration_limit INTEGER,
            number_of_registrations INTEGER,
            is_cancelled INTEGER
        )
        """
    )
    conn.execute(
        """
        INSERT INTO events (
            id,
            event_name,
            event_start_datetime,
            event_end_datetime,
            registration_required,
            registration_limit,
            number_of_registrations,
            is_cancelled
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            1012621,
            "Habit: Open to Grace",
            "2026-08-08T15:00:00Z",
            "2026-08-08T16:00:00Z",
            1,
            0,
            4,
            0,
        ),
    )
    conn.commit()
    conn.close()


def test_collect_snapshot_combines_available_growth_sources(tmp_path):
    twy_root = tmp_path
    data_root = tmp_path / "data"
    captured_at = datetime(2026, 7, 27, 20, 15, tzinfo=timezone.utc)

    write_json(
        twy_root / "announce/data/instagram/history/2026-07-26.json",
        {"date": "2026-07-26", "follower_count": 2305},
    )
    write_json(
        twy_root / "announce/data/instagram/history/2026-07-27.json",
        {"date": "2026-07-27", "follower_count": 2303},
    )
    write_json(
        twy_root / "announce/data/email/history/2026-07-27.json",
        {
            "captured_at": "2026-07-27T11:55:02Z",
            "list_name": "Email: Subscribed",
            "subscriber_count": 921,
        },
    )
    create_marvy_db(data_root / "marvy.db")
    write_json(twy_root / "clips/state/ig_queue.json", [{"clip": 1}, {"clip": 2}])
    write_json(twy_root / "clips/state/ig_quote_queue.json", [{"quote": 1}])
    write_json(twy_root / "clips/state/ig_history.json", [{"post": 1}, {"post": 2}, {"post": 3}])
    write_json(
        twy_root / "clips/state/ig_scheduler_state.json",
        {
            "clip_pool_warning_active": False,
            "clip_pool_warning_posted_to_slack": True,
        },
    )

    snapshot = social_growth.collect_snapshot(
        captured_at=captured_at,
        twy_root=twy_root,
        data_root=data_root,
        zernio_fetch_post=None,
        zernio_account_health=None,
    )

    assert snapshot["date"] == "2026-07-27"
    assert snapshot["instagram"]["followers"] == {
        "count": 2303,
        "snapshot_date": "2026-07-27",
    }
    assert snapshot["email"]["subscribers"] == {
        "count": 921,
        "list_name": "Email: Subscribed",
        "snapshot_date": "2026-07-27",
    }
    assert snapshot["habit"]["next_event"] == {
        "id": 1012621,
        "name": "Habit: Open to Grace",
        "start": "2026-08-08T15:00:00Z",
        "registrations": 4,
    }
    assert snapshot["queues"] == {
        "ig_clip_queue": 2,
        "ig_quote_queue": 1,
        "ig_history": 3,
        "clip_pool_warning_active": False,
        "clip_pool_warning_posted_to_slack": True,
    }
    assert snapshot["landing_page"]["plausible"]["status"] == "not_configured"
    assert snapshot["external_benchmarks"]["socialblade"]["status"] == "not_configured"


def test_collect_zernio_recent_status_uses_nested_platform_status(tmp_path):
    twy_root = tmp_path
    history_path = twy_root / "clips/state/ig_history.json"
    write_json(
        history_path,
        [
            {
                "zernio_post_id": "old",
                "post_type": "reel",
                "posted_for_class": "2026-07-20",
                "class_name": "2026-07-13_strength",
                "clip_name": "01.mp4",
                "scheduled_for": "2026-07-20T08:00:00-06:00",
            },
            {
                "zernio_post_id": "failed1",
                "post_type": "reel",
                "posted_for_class": "2026-07-28",
                "class_name": "2026-07-15_breath",
                "clip_name": "02.mp4",
                "scheduled_for": "2026-07-27T08:00:00-06:00",
            },
            {
                "zernio_post_id": "scheduled1",
                "post_type": "story",
                "posted_for_class": "2026-07-28",
                "class_name": "2026-07-15_breath",
                "clip_name": "03.mp4",
                "scheduled_for": "2026-07-28T06:00:00-06:00",
            },
        ],
    )

    def fake_fetch(post_id):
        assert post_id != "old"
        payloads = {
            "failed1": {
                "post": {
                    "title": "TWY IG Reel for 2026-07-28",
                    "status": "failed",
                    "content": "caption",
                    "platforms": [
                        {
                            "status": "failed",
                            "publishAttempts": 1,
                            "platformSpecificData": {"contentType": "reels"},
                        }
                    ],
                }
            },
            "scheduled1": {
                "post": {
                    "title": "TWY IG Story for 2026-07-28",
                    "status": "scheduled",
                    "content": "story",
                    "platforms": [
                        {
                            "status": "pending",
                            "publishAttempts": 0,
                            "platformSpecificData": {"contentType": "story"},
                        }
                    ],
                }
            },
        }
        return payloads[post_id]

    status = social_growth.collect_zernio_recent_status(
        history_path=history_path,
        captured_at=datetime(2026, 7, 27, 20, 0, tzinfo=timezone.utc),
        fetch_post=fake_fetch,
        lookback_hours=72,
        lookahead_hours=72,
    )

    assert status["queried_count"] == 2
    assert status["by_post_status"] == {"failed": 1, "scheduled": 1}
    assert status["by_platform_status"] == {"failed": 1, "pending": 1}
    assert status["by_content_type"] == {"reels": 1, "story": 1}
    assert status["failed_count"] == 1
    assert status["pending_count"] == 1
    assert status["failed"][0]["zernio_post_id"] == "failed1"
    assert status["pending"][0]["zernio_post_id"] == "scheduled1"
