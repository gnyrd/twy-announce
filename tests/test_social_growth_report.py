from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

import social_growth_report as social_growth

CAPTURED_AT = datetime(2026, 7, 27, 20, 15, tzinfo=timezone.utc)



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
    monkeypatch.delenv("PLAUSIBLE_SITE_IDS", raising=False)
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
    write_json(
        twy_root / "announce/data/facebook/history/2026-07-26.json",
        {"date": "2026-07-26", "follower_count": 2320},
    )
    write_json(
        twy_root / "announce/data/facebook/history/2026-07-27.json",
        {"date": "2026-07-27", "follower_count": 2319},
    )
    write_json(
        twy_root / "announce/data/youtube/history/2026-07-26.json",
        {"date": "2026-07-26", "subscriber_count": 1078},
    )
    write_json(
        twy_root / "announce/data/youtube/history/2026-07-27.json",
        {"date": "2026-07-27", "subscriber_count": 1080},
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
        "previous_count": 2305,
        "previous_snapshot_date": "2026-07-26",
        "delta_since_previous": -2,
    }
    assert snapshot["email"]["subscribers"] == {
        "count": 921,
        "list_name": "Email: Subscribed",
        "snapshot_date": "2026-07-27",
    }
    assert snapshot["facebook"]["followers"] == {
        "count": 2319,
        "snapshot_date": "2026-07-27",
        "previous_count": 2320,
        "previous_snapshot_date": "2026-07-26",
        "delta_since_previous": -1,
    }
    assert snapshot["youtube"]["subscribers"] == {
        "count": 1080,
        "snapshot_date": "2026-07-27",
        "previous_count": 1078,
        "previous_snapshot_date": "2026-07-26",
        "delta_since_previous": 2,
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
    assert snapshot["websites"] == {
        "status": "not_configured",
        "captured_at": "2026-07-27T20:15:00Z",
        "properties": {
            "habit": {
                "status": "not_configured",
                "required": ["PLAUSIBLE_API_KEY", "PLAUSIBLE_SITE_ID"],
            }
        },
    }
    assert snapshot["landing_page"]["plausible"]["status"] == "not_configured"
    assert snapshot["external_benchmarks"]["socialblade"]["status"] == "not_configured"
    assert snapshot["summary"] == {
        "email_subscribers": 921,
        "habit_register_clicks_day": None,
        "habit_signup_success_day": None,
        "ig_clip_queue": 2,
        "ig_quote_queue": 1,
        "instagram_follower_delta": -2,
        "instagram_followers": 2303,
        "facebook_followers": 2319,
        "facebook_follower_delta": -1,
        "youtube_subscribers": 1080,
        "youtube_subscriber_delta": 2,
        "landing_day_pageviews": None,
        "landing_day_visitors": None,
        "landing_page_status": "not_configured",
        "next_habit_registrations": 4,
        "recent_campaign_variants": [],
        "upcoming_campaign_variants": [],
        "zernio_analytics_status": None,
        "zernio_api_errors": None,
        "zernio_content_issues": None,
        "zernio_failed_posts": None,
        "zernio_status": "not_configured",
    }


def test_campaign_snapshot_groups_recent_and_upcoming_variants(tmp_path):
    history_path = tmp_path / "clips/state/ig_history.json"
    write_json(
        history_path,
        [
            {
                "post_type": "reel",
                "posted_for_class": "2026-07-20",
                "scheduled_for": "2026-07-19T17:30:00-06:00",
                "zernio_post_id": "old",
                "campaign": {
                    "campaign": "habit_entry_regular_reels",
                    "ctaVariant": "older",
                    "habitTargetDate": "2026-08-08",
                },
            },
            {
                "post_type": "reel",
                "posted_for_class": "2026-08-04",
                "scheduled_for": "2026-08-03T08:00:00-06:00",
                "zernio_post_id": "recent1",
                "campaign": {
                    "campaign": "habit_entry_regular_reels",
                    "ctaVariant": "steady_first_step",
                    "habitTargetDate": "2026-08-08",
                },
            },
            {
                "post_type": "reel",
                "posted_for_class": "2026-08-06",
                "scheduled_for": "2026-08-05T08:00:00-06:00",
                "zernio_post_id": "recent2",
                "campaign": {
                    "campaign": "habit_entry_regular_reels",
                    "ctaVariant": "steady_first_step",
                    "habitTargetDate": "2026-08-08",
                },
            },
            {
                "post_type": "reel",
                "posted_for_class": "2026-08-10",
                "scheduled_for": "2026-08-09T17:30:00-06:00",
                "zernio_post_id": "upcoming1",
                "campaign": {
                    "campaign": "habit_entry_regular_reels",
                    "ctaVariant": "find_out",
                    "habitTargetDate": "2026-09-12",
                },
            },
            {
                "post_type": "story",
                "posted_for_class": "2026-08-06",
                "scheduled_for": "2026-08-06T06:00:00-06:00",
                "zernio_post_id": "story1",
            },
        ],
    )

    snapshot = social_growth.campaign_snapshot(
        history_path=history_path,
        captured_at=datetime(2026, 8, 6, 18, 0, tzinfo=timezone.utc),
    )

    assert snapshot["status"] == "ok"
    assert snapshot["post_count"] == 3
    assert [variant["key"] for variant in snapshot["recent_variants"]] == [
        "habit_entry_regular_reels:steady_first_step"
    ]
    recent = snapshot["recent_variants"][0]
    assert recent["post_count"] == 2
    assert recent["habitTargetDate"] == "2026-08-08"
    assert recent["posted_for_classes"] == ["2026-08-04", "2026-08-06"]
    assert recent["zernio_post_ids"] == ["recent1", "recent2"]
    assert [variant["key"] for variant in snapshot["upcoming_variants"]] == [
        "habit_entry_regular_reels:find_out"
    ]
    assert snapshot["upcoming_variants"][0]["post_count"] == 1
    assert snapshot["posts"][0]["zernio_post_id"] == "recent1"


def test_summarize_snapshot_includes_campaign_variants():
    summary = social_growth.summarize_snapshot(
        {
            "campaigns": {
                "recent_variants": [
                    {"key": "habit_entry_regular_reels:steady_first_step"},
                ],
                "upcoming_variants": [
                    {"key": "habit_entry_regular_reels:find_out"},
                ],
            }
        }
    )

    assert summary["recent_campaign_variants"] == ["habit_entry_regular_reels:steady_first_step"]
    assert summary["upcoming_campaign_variants"] == ["habit_entry_regular_reels:find_out"]


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
        "utm_sources": [{"metrics": [5, 5, 8, 8], "dimensions": []}],
        "tracked_events": [{"metrics": [5, 5, 8, 8], "dimensions": []}],
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
    assert [call["date_range"] for call in calls] == (
        ["day"] * 4 + ["7d"] * 7 + ["30d"] * 7
    )
    assert all(call["site_id"] == "habit.tiffanywoodyoga.com" for call in calls)
    # The visit:channel query feeds search and AI counts for the stats page.
    assert [c for c in calls if c.get("dimensions") == ["visit:channel"]]
    assert status["metrics"]["last_7_days"]["search_visitors"] == 0
    assert status["metrics"]["last_7_days"]["ai_visitors"] == 0

    event_calls = [
        call
        for call in calls
        if call.get("dimensions") == list(social_growth.PLAUSIBLE_FUNNEL_DIMENSIONS)
    ]
    assert len(event_calls) == 3
    assert all(call["metrics"] == ["events"] for call in event_calls)
    assert all(call["filters"][0][:2] == ["is", "event:name"] for call in event_calls)
    breakdown_calls = [
        call
        for call in calls
        if call.get("dimensions") in (
            ["visit:source"],
            ["visit:entry_page"],
            ["visit:utm_source", "visit:utm_medium"],
            ["event:name"],
        )
    ]
    assert all(call["order_by"] for call in breakdown_calls)
    assert all(call["pagination"] == {"limit": 20} for call in breakdown_calls)
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


def test_plausible_sites_collect_each_property_independently():
    queried_site_ids = []

    def fake_query_by_site(site_id):
        def query(body):
            queried_site_ids.append(body["site_id"])
            assert body["site_id"] == site_id
            return {"results": [{"metrics": [5, 5, 8, 8], "dimensions": []}]}

        return query

    result = social_growth.collect_plausible_sites(
        captured_at=CAPTURED_AT,
        site_ids=list(social_growth.PLAUSIBLE_PROPERTY_ROLES),
        post_query_for_site=fake_query_by_site,
    )

    assert set(result["properties"]) == {"main", "studio", "habit"}
    assert result["properties"]["main"]["site_id"] == "tiffanywoodyoga.com"
    assert result["properties"]["studio"]["site_id"] == "studio.tiffanywoodyoga.com"
    assert result["properties"]["habit"]["site_id"] == "habit.tiffanywoodyoga.com"
    assert set(queried_site_ids) == set(social_growth.PLAUSIBLE_PROPERTY_ROLES)


def test_one_plausible_property_failure_is_partial_not_total():
    def query_with_studio_failure(site_id):
        def query(body):
            if site_id == "studio.tiffanywoodyoga.com":
                raise RuntimeError("studio unavailable")
            return {"results": [{"metrics": [5, 5, 8, 8], "dimensions": []}]}

        return query

    result = social_growth.collect_plausible_sites(
        captured_at=CAPTURED_AT,
        site_ids=list(social_growth.PLAUSIBLE_PROPERTY_ROLES),
        post_query_for_site=query_with_studio_failure,
    )

    assert result["status"] == "partial"
    assert result["properties"]["main"]["status"] == "ok"
    assert result["properties"]["studio"]["status"] == "error"
    assert result["properties"]["habit"]["status"] == "ok"


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


def test_zernio_post_row_checks_full_reel_caption_before_storing_excerpt():
    long_caption = (
        "Open the body without forcing the shape. " * 12
        + "\n\n#anusara #anusarayoga #tiffanywoodyoga"
    )
    row = social_growth.zernio_post_row(
        {
            "scheduled_for": "2026-08-05T08:00:00-06:00",
            "post_type": "reel",
            "posted_for_class": "2026-08-06",
            "class_name": "2026-07-22_breath",
            "clip_name": "02_teaching_score8_15s",
            "zernio_post_id": "post1",
        },
        {
            "post": {
                "title": "TWY IG Reel for 2026-08-06",
                "status": "scheduled",
                "content": long_caption,
                "platforms": [
                    {
                        "status": "pending",
                        "publishAttempts": 0,
                        "platformSpecificData": {"contentType": "reels"},
                    }
                ],
            }
        },
    )

    assert row["content_length"] == len(long_caption)
    assert row["content_truncated"] is True
    assert len(row["content"]) == 280
    assert row["content_issues"] == []


def test_zernio_post_row_reports_missing_reel_hashtags_and_generic_fallback():
    row = social_growth.zernio_post_row(
        {
            "scheduled_for": "2026-07-28T08:00:00-06:00",
            "post_type": "reel",
            "posted_for_class": "2026-07-29",
            "class_name": "2026-07-13_strength",
            "clip_name": "07_wisdom_score8_24s",
            "zernio_post_id": "post1",
        },
        {
            "post": {
                "title": "TWY IG Reel for 2026-07-29",
                "status": "scheduled",
                "content": (
                    "A short practice cue from Tiff's teaching library.\n\n"
                    "Upcoming live class: Breath as Medicine, Wednesday, July 29.\n\n"
                    "#anusara #tiffanywoodyoga"
                ),
                "platforms": [
                    {
                        "status": "pending",
                        "publishAttempts": 0,
                        "platformSpecificData": {"contentType": "reels"},
                    }
                ],
            }
        },
    )

    assert row["content_issues"] == [
        {
            "code": "missing_required_hashtags",
            "missing": ["#anusarayoga"],
        },
        {"code": "generic_fallback_opener"},
    ]


def test_warning_events_include_zernio_content_issues():
    snapshot = {
        "captured_at": "2026-07-27T20:00:00Z",
        "instagram": {"zernio_account": {}},
        "zernio": {
            "content_issues": [
                {
                    "zernio_post_id": "post1",
                    "title": "TWY IG Reel for 2026-07-29",
                    "scheduled_for": "2026-07-28T08:00:00-06:00",
                    "content_issues": [
                        {
                            "code": "missing_required_hashtags",
                            "missing": ["#anusarayoga"],
                        },
                        {"code": "generic_fallback_opener"},
                    ],
                }
            ],
        },
    }

    events = social_growth.warning_events(snapshot, token_warning_hours=48)

    assert [event["key"] for event in events] == [
        "zernio_content_issue:post1:2026-07-28T08:00:00-06:00"
    ]
    assert "TWY IG Reel for 2026-07-29" in events[0]["text"]
    assert "missing required hashtags #anusarayoga" in events[0]["text"]
    assert "generic fallback opener" in events[0]["text"]


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


def test_collect_snapshot_uses_legacy_habit_when_site_ids_are_absent(tmp_path, monkeypatch):
    captured_at = datetime(2026, 7, 27, 20, 15, tzinfo=timezone.utc)
    legacy_habit = {
        "status": "ok",
        "site_id": "habit.tiffanywoodyoga.com",
        "captured_at": "2026-07-27T20:15:00Z",
        "metrics": {"day": {"visitors": 5, "funnel_events": {"Habit Register Click": 2}}},
    }
    calls = []

    monkeypatch.setenv("PLAUSIBLE_API_KEY", "test-key")
    monkeypatch.setenv("PLAUSIBLE_SITE_ID", "habit.tiffanywoodyoga.com")
    monkeypatch.delenv("PLAUSIBLE_SITE_IDS", raising=False)

    def fake_collect_legacy(*, captured_at, post_query=None):
        calls.append((captured_at, post_query))
        return legacy_habit

    monkeypatch.setattr(social_growth, "collect_plausible_status", fake_collect_legacy)

    snapshot = social_growth.collect_snapshot(
        captured_at=captured_at,
        twy_root=tmp_path,
        data_root=tmp_path / "data",
        zernio_fetch_post=None,
        zernio_account_health=None,
    )

    assert calls == [(captured_at, None)]
    assert snapshot["websites"] == {
        "status": "ok",
        "captured_at": "2026-07-27T20:15:00Z",
        "properties": {"habit": legacy_habit},
    }
    assert snapshot["landing_page"]["plausible"] == legacy_habit


def test_warning_events_include_each_errored_website_property():
    snapshot = {
        "captured_at": "2026-07-27T20:00:00Z",
        "instagram": {"zernio_account": {}},
        "zernio": {},
        "websites": {
            "properties": {
                "main": {
                    "status": "error",
                    "site_id": "tiffanywoodyoga.com",
                    "error": "main timeout",
                },
                "studio": {
                    "status": "error",
                    "site_id": "studio.tiffanywoodyoga.com",
                    "error": "studio timeout",
                },
                "habit": {
                    "status": "ok",
                    "site_id": "habit.tiffanywoodyoga.com",
                },
            }
        },
        "landing_page": {
            "plausible": {
                "status": "error",
                "site_id": "habit.tiffanywoodyoga.com",
                "error": "legacy alias must not duplicate website warnings",
            }
        },
    }

    events = social_growth.warning_events(snapshot, token_warning_hours=48)

    assert [event["key"] for event in events] == [
        "plausible_error:main:tiffanywoodyoga.com",
        "plausible_error:studio:studio.tiffanywoodyoga.com",
    ]
    assert "Main property" in events[0]["text"]
    assert "Studio property" in events[1]["text"]


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


def test_warning_events_include_significant_follower_drop():
    snapshot = {
        "captured_at": "2026-07-27T20:00:00Z",
        "instagram": {
            "followers": {
                "count": 2290,
                "snapshot_date": "2026-07-27",
                "previous_count": 2303,
                "previous_snapshot_date": "2026-07-26",
                "delta_since_previous": -13,
            },
            "zernio_account": {},
        },
        "zernio": {},
    }

    events = social_growth.warning_events(
        snapshot,
        token_warning_hours=48,
        follower_drop_threshold=10,
    )

    assert [event["key"] for event in events] == [
        "instagram_follower_drop:2026-07-27:2026-07-26:-13"
    ]
    assert "Instagram followers dropped by 13" in events[0]["text"]
    assert "2303 to 2290" in events[0]["text"]
    assert "threshold 10" in events[0]["text"]


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


def _zernio_history(tmp_path, post_id, scheduled_for):
    history = tmp_path / "ig_history.json"
    history.write_text(
        json.dumps([{"zernio_post_id": post_id, "scheduled_for": scheduled_for}])
    )
    return history


def _failed_payload(title):
    return {
        "post": {
            "title": title,
            "status": "failed",
            "platforms": [{"platform": "instagram", "status": "failed"}],
        }
    }


def test_a_retried_failure_is_reported_recovered_not_failed(tmp_path):
    captured_at = datetime(2026, 8, 12, 13, 20, tzinfo=timezone.utc)
    post_id = "6a6d0cf46579419296ddcd5a"
    history = _zernio_history(tmp_path, post_id, "2026-08-11T14:00:00+00:00")
    ledger = tmp_path / "ig_publish_retry.json"
    ledger.write_text(
        json.dumps(
            {
                post_id: {
                    "attempts": 1,
                    "new_post_ids": ["6a7b390f5460ed9f2954248c"],
                    "resolved": "published",
                    "resolved_utc": "2026-08-11T15:30:12+00:00",
                }
            }
        )
    )

    snapshot = social_growth.collect_zernio_recent_status(
        history_path=history,
        captured_at=captured_at,
        fetch_post=lambda pid: _failed_payload("TWY IG Reel for 2026-08-12"),
        retry_ledger_path=ledger,
    )

    assert snapshot["failed_count"] == 0
    assert snapshot["recovered_count"] == 1
    assert snapshot["recovered"][0]["recovered_post_ids"] == ["6a7b390f5460ed9f2954248c"]


def test_an_unrecovered_failure_is_still_reported_failed(tmp_path):
    captured_at = datetime(2026, 8, 12, 13, 20, tzinfo=timezone.utc)
    post_id = "deadbeefdeadbeefdeadbeef"
    history = _zernio_history(tmp_path, post_id, "2026-08-11T14:00:00+00:00")
    ledger = tmp_path / "ig_publish_retry.json"
    ledger.write_text(json.dumps({}))

    snapshot = social_growth.collect_zernio_recent_status(
        history_path=history,
        captured_at=captured_at,
        fetch_post=lambda pid: _failed_payload("TWY IG Reel"),
        retry_ledger_path=ledger,
    )

    assert snapshot["failed_count"] == 1
    assert snapshot["recovered_count"] == 0


def test_a_stale_ledger_entry_does_not_count_as_recovered(tmp_path):
    captured_at = datetime(2026, 8, 12, 13, 20, tzinfo=timezone.utc)
    post_id = "6a67fdf5131b530ebafbc867"
    history = _zernio_history(tmp_path, post_id, "2026-08-11T14:00:00+00:00")
    ledger = tmp_path / "ig_publish_retry.json"
    ledger.write_text(json.dumps({post_id: {"resolved": "stale"}}))

    snapshot = social_growth.collect_zernio_recent_status(
        history_path=history,
        captured_at=captured_at,
        fetch_post=lambda pid: _failed_payload("TWY IG Reel"),
        retry_ledger_path=ledger,
    )

    assert snapshot["failed_count"] == 1
    assert snapshot["recovered_count"] == 0


def test_a_missing_ledger_leaves_every_failure_reported_as_failed(tmp_path):
    captured_at = datetime(2026, 8, 12, 13, 20, tzinfo=timezone.utc)
    history = _zernio_history(tmp_path, "abc123", "2026-08-11T14:00:00+00:00")

    snapshot = social_growth.collect_zernio_recent_status(
        history_path=history,
        captured_at=captured_at,
        fetch_post=lambda pid: _failed_payload("TWY IG Reel"),
        retry_ledger_path=tmp_path / "does_not_exist.json",
    )

    assert snapshot["failed_count"] == 1
    assert snapshot["recovered_count"] == 0


def test_the_recovered_alert_says_recovered_and_names_the_replacement():
    events = social_growth.warning_events(
        {
            "zernio": {
                "recovered": [
                    {
                        "zernio_post_id": "6a6d0cf46579419296ddcd5a",
                        "title": "TWY IG Reel for 2026-08-12",
                        "scheduled_for": "2026-08-11T08:00:00-06:00",
                        "recovered_post_ids": ["6a7b390f5460ed9f2954248c"],
                    }
                ]
            }
        }
    )

    assert len(events) == 1
    text = events[0]["text"]
    assert events[0]["key"] == "zernio_recovered:6a6d0cf46579419296ddcd5a"
    assert "recovered on its own" in text
    assert "6a7b390f5460ed9f2954248c" in text
    assert "No action needed." in text
    assert ":warning:" not in text


def test_collect_plausible_property_search_and_ai_visitors():
    def fake_post_query(body):
        if body.get("dimensions") == ["visit:channel"]:
            return {"results": [
                {"dimensions": ["Organic Search"], "metrics": [12]},
                {"dimensions": ["Direct"], "metrics": [30]},
            ]}
        if body.get("dimensions") == ["visit:source"]:
            return {"results": [
                {"dimensions": ["Google"], "metrics": [12, 13]},
                {"dimensions": ["chatgpt.com"], "metrics": [4, 4]},
                {"dimensions": ["Perplexity"], "metrics": [2, 2]},
            ]}
        return {"results": [{"metrics": [50, 55, 90, 95], "dimensions": []}], "query": {}}

    prop = social_growth.collect_plausible_property(
        site_id="tiffanywoodyoga.com",
        role="main",
        captured_at=datetime(2026, 9, 4, 20, 0, tzinfo=timezone.utc),
        post_query=fake_post_query,
    )
    for window in ("last_7_days", "last_30_days"):
        assert prop["metrics"][window]["search_visitors"] == 12
        assert prop["metrics"][window]["ai_visitors"] == 6
    assert "search_visitors" not in prop["metrics"]["day"]
