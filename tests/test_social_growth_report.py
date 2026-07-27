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


def test_collect_snapshot_combines_available_growth_sources(tmp_path, monkeypatch):
    monkeypatch.delenv("PLAUSIBLE_API_KEY", raising=False)
    monkeypatch.delenv("PLAUSIBLE_SITE_ID", raising=False)
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


def test_collect_plausible_status_queries_configured_site(monkeypatch):
    monkeypatch.setenv("PLAUSIBLE_API_KEY", "test-key")
    monkeypatch.setenv("PLAUSIBLE_SITE_ID", "habit.tiffanywoodyoga.com")
    calls = []

    def fake_post_query(body):
        calls.append(body)
        if body.get("dimensions") == list(social_growth.PLAUSIBLE_FUNNEL_DIMENSIONS):
            return {
                "results": [
                    {
                        "metrics": [2],
                        "dimensions": ["Habit Register Click", "instagram-bio", "bio", "pre_class", "/ig"],
                    },
                    {
                        "metrics": [3],
                        "dimensions": ["Habit Register Click", "instagram", "reel", "pre_class", "/ig"],
                    },
                    {
                        "metrics": [1],
                        "dimensions": ["Habit Signup Success", "instagram-bio", "bio", "pre_class", "/ig"],
                    },
                ],
                "query": {"date_range": ["2026-07-20T00:00:00-06:00", "2026-07-26T23:59:59-06:00"]},
            }
        return {
            "results": [{"metrics": [5, 5, 8, 8], "dimensions": []}],
            "query": {"date_range": ["2026-07-20T00:00:00-06:00", "2026-07-26T23:59:59-06:00"]},
        }

    status = social_growth.collect_plausible_status(
        captured_at=datetime(2026, 7, 27, 20, 15, tzinfo=timezone.utc),
        post_query=fake_post_query,
    )

    assert status["status"] == "ok"
    assert status["site_id"] == "habit.tiffanywoodyoga.com"
    assert status["metrics"]["day"] == {
        "visitors": 5,
        "visits": 5,
        "pageviews": 8,
        "events": 8,
        "query_date_range": ["2026-07-20T00:00:00-06:00", "2026-07-26T23:59:59-06:00"],
        "funnel_events": {
            "Habit Register Click": 5,
            "Habit Newsletter Open": 0,
            "Habit Signup Submit": 0,
            "Habit Signup Success": 1,
            "Habit Signup Error": 0,
            "Habit Membership Click": 0,
        },
        "funnel_by_vector": [
            {
                "event": "Habit Register Click",
                "source": "instagram-bio",
                "content": "bio",
                "page_state": "pre_class",
                "path": "/ig",
                "events": 2,
            },
            {
                "event": "Habit Register Click",
                "source": "instagram",
                "content": "reel",
                "page_state": "pre_class",
                "path": "/ig",
                "events": 3,
            },
            {
                "event": "Habit Signup Success",
                "source": "instagram-bio",
                "content": "bio",
                "page_state": "pre_class",
                "path": "/ig",
                "events": 1,
            },
        ],
    }
    assert [call["date_range"] for call in calls] == ["day", "day", "7d", "7d", "30d", "30d"]
    assert all(call["site_id"] == "habit.tiffanywoodyoga.com" for call in calls)
    event_calls = [
        call
        for call in calls
        if call.get("dimensions") == list(social_growth.PLAUSIBLE_FUNNEL_DIMENSIONS)
    ]
    assert len(event_calls) == 3
    assert all(call["metrics"] == ["events"] for call in event_calls)
    assert all(call["filters"][0][:2] == ["is", "event:name"] for call in event_calls)
    assert all(call["pagination"] == {"limit": 100} for call in event_calls)


def test_collect_plausible_status_reports_api_errors(monkeypatch):
    monkeypatch.setenv("PLAUSIBLE_API_KEY", "test-key")
    monkeypatch.setenv("PLAUSIBLE_SITE_ID", "habit.tiffanywoodyoga.com")

    def fake_post_query(body):
        raise RuntimeError("Plausible query failed: 401 invalid")

    status = social_growth.collect_plausible_status(
        captured_at=datetime(2026, 7, 27, 20, 15, tzinfo=timezone.utc),
        post_query=fake_post_query,
    )

    assert status == {
        "status": "error",
        "site_id": "habit.tiffanywoodyoga.com",
        "error": "Plausible query failed: 401 invalid",
    }


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
    assert status["analytics"]["status"] == "not_configured"


def test_collect_zernio_post_analytics_reports_blocked_addon():
    rows = [
        {
            "zernio_post_id": "post1",
            "scheduled_for": "2026-07-27T08:00:00-06:00",
            "post_status": "published",
            "platform_status": "published",
        }
    ]

    def fake_fetch(post_id):
        assert post_id == "post1"
        return {
            "_collector_http_status": 402,
            "error": "Analytics add-on required",
            "code": "analytics_addon_required",
            "reason": "no_analytics",
            "mode": "subscription",
        }

    status = social_growth.collect_zernio_post_analytics(
        rows=rows,
        fetch_analytics=fake_fetch,
    )

    assert status == {
        "status": "blocked",
        "queried_count": 0,
        "code": "analytics_addon_required",
        "error": "Analytics add-on required",
        "reason": "no_analytics",
        "mode": "subscription",
    }


def test_collect_zernio_post_analytics_extracts_metrics():
    rows = [
        {
            "scheduled_for": "2026-07-27T08:00:00-06:00",
            "post_type": "reel",
            "posted_for_class": "2026-07-28",
            "class_name": "2026-07-15_breath",
            "clip_name": "06_teaching_score8_9s",
            "zernio_post_id": "post1",
            "post_status": "published",
            "platform_status": "published",
        }
    ]

    def fake_fetch(post_id):
        assert post_id == "post1"
        return {
            "_collector_http_status": 200,
            "analytics": {
                "impressions": 100,
                "reach": 80,
                "likes": 7,
                "comments": 1,
                "shares": 2,
                "saves": 3,
                "views": 90,
                "engagementRate": 0.13,
            },
            "platforms": [
                {
                    "platformPostId": "ig1",
                    "platformPostUrl": "https://www.instagram.com/reel/example/",
                    "syncStatus": "synced",
                }
            ],
        }

    status = social_growth.collect_zernio_post_analytics(
        rows=rows,
        fetch_analytics=fake_fetch,
    )

    assert status["status"] == "ok"
    assert status["queried_count"] == 1
    assert status["posts"][0] == {
        "scheduled_for": "2026-07-27T08:00:00-06:00",
        "post_type": "reel",
        "posted_for_class": "2026-07-28",
        "class_name": "2026-07-15_breath",
        "clip_name": "06_teaching_score8_9s",
        "zernio_post_id": "post1",
        "platform_post_id": "ig1",
        "platform_post_url": "https://www.instagram.com/reel/example/",
        "sync_status": "synced",
        "metrics": {
            "impressions": 100,
            "reach": 80,
            "likes": 7,
            "comments": 1,
            "shares": 2,
            "saves": 3,
            "views": 90,
            "engagementRate": 0.13,
        },
    }


def test_warning_events_include_zernio_failures_api_errors_and_token_expiry():
    snapshot = {
        "captured_at": "2026-07-27T20:00:00Z",
        "instagram": {
            "zernio_account": {
                "username": "tiffanywoodyoga",
                "token_status": {
                    "valid": True,
                    "needs_refresh": False,
                    "expires_at": "2026-07-28T19:00:00Z",
                },
            }
        },
        "zernio": {
            "failed": [
                {
                    "zernio_post_id": "failed1",
                    "title": "TWY IG Reel for 2026-07-28",
                    "scheduled_for": "2026-07-27T08:00:00-06:00",
                    "platform_status": "failed",
                }
            ],
            "api_errors": [
                {
                    "zernio_post_id": "api1",
                    "scheduled_for": "2026-07-28T08:00:00-06:00",
                    "error": "rate limited",
                }
            ],
            "analytics": {
                "api_errors": [
                    {
                        "zernio_post_id": "analytics1",
                        "scheduled_for": "2026-07-28T08:00:00-06:00",
                        "error": "analytics timeout",
                    }
                ]
            },
        },
    }

    events = social_growth.warning_events(snapshot, token_warning_hours=48)

    assert [event["key"] for event in events] == [
        "zernio_failed:failed1",
        "zernio_api_error:api1:2026-07-28T08:00:00-06:00",
        "zernio_analytics_api_error:analytics1:2026-07-28T08:00:00-06:00",
        "zernio_token:2026-07-28T19:00:00Z",
    ]
    assert "TWY IG Reel for 2026-07-28" in events[0]["text"]
    assert "rate limited" in events[1]["text"]
    assert "analytics timeout" in events[2]["text"]
    assert "expires within 48 hours" in events[3]["text"]


def test_warning_events_include_plausible_collection_errors():
    snapshot = {
        "captured_at": "2026-07-27T20:00:00Z",
        "instagram": {"zernio_account": {}},
        "zernio": {},
        "landing_page": {
            "plausible": {
                "status": "error",
                "site_id": "habit.tiffanywoodyoga.com",
                "error": "Plausible query failed: 401 invalid",
            }
        },
    }

    events = social_growth.warning_events(snapshot, token_warning_hours=48)

    assert [event["key"] for event in events] == [
        "plausible_error:habit.tiffanywoodyoga.com:Plausible query failed: 401 invalid"
    ]
    assert "Plausible funnel collection failed" in events[0]["text"]
    assert "habit.tiffanywoodyoga.com" in events[0]["text"]
    assert "401 invalid" in events[0]["text"]


def test_post_new_warning_events_records_sent_keys_and_skips_repeats(tmp_path):
    state_path = tmp_path / ".alert_state.json"
    sent = []

    def fake_post(channel, text):
        sent.append((channel, text))

    events = [
        {"key": "one", "text": "First warning"},
        {"key": "two", "text": "Second warning"},
    ]

    first = social_growth.post_new_warning_events(
        events,
        state_path=state_path,
        channel="C0ASG1EU0HL",
        post_warning=fake_post,
        sent_at=datetime(2026, 7, 27, 20, 0, tzinfo=timezone.utc),
    )
    second = social_growth.post_new_warning_events(
        events,
        state_path=state_path,
        channel="C0ASG1EU0HL",
        post_warning=fake_post,
        sent_at=datetime(2026, 7, 27, 20, 5, tzinfo=timezone.utc),
    )

    assert first == {"posted": 2, "skipped": 0, "failed": 0}
    assert second == {"posted": 0, "skipped": 2, "failed": 0}
    assert sent == [
        ("C0ASG1EU0HL", "First warning"),
        ("C0ASG1EU0HL", "Second warning"),
    ]
    state = json.loads(state_path.read_text())
    assert sorted(state["sent"]) == ["one", "two"]
