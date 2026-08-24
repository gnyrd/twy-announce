from __future__ import annotations

import json
from datetime import date

import pytest
import social_growth_weekly as weekly


def write_snapshot(path, snapshot_date, summary, *, analytics=None, campaign_posts=None, websites=None):
    path.mkdir(parents=True, exist_ok=True)
    (path / f"{snapshot_date}.json").write_text(
        json.dumps(
            {
                "date": snapshot_date,
                "captured_at": f"{snapshot_date}T13:20:00Z",
                "summary": summary,
                "campaigns": {"posts": campaign_posts or []},
                "zernio": {"analytics": {"posts": analytics or []}},
                "websites": websites or {},
            }
        )
        + "\n"
    )


def website_property(site_id, *, day_visitors=2, status="ok", error=None, funnel_by_vector=None):
    if status != "ok":
        return {
            "status": status,
            "site_id": site_id,
            "error": error,
        }

    day = {
        "visitors": day_visitors,
        "visits": day_visitors + 1,
        "pageviews": day_visitors + 2,
        "events": day_visitors + 3,
    }
    last_7_days = {
        "visitors": day_visitors * 7,
        "visits": (day_visitors + 1) * 7,
        "pageviews": (day_visitors + 2) * 7,
        "events": (day_visitors + 3) * 7,
        "sources": [{"dimensions": ["instagram"], "metrics": [day_visitors * 3, day_visitors * 3]}],
        "entry_pages": [{"dimensions": ["/ig"], "metrics": [day_visitors * 2, day_visitors * 2]}],
        "utm_sources": [{"dimensions": ["instagram", "social"], "metrics": [day_visitors, day_visitors]}],
        "tracked_events": [{"dimensions": ["Habit Register Click"], "metrics": [day_visitors]}],
    }
    if funnel_by_vector is not None:
        last_7_days["funnel_events"] = {"Habit Register Click": day_visitors}
        last_7_days["funnel_by_vector"] = funnel_by_vector
    return {
        "status": "ok",
        "site_id": site_id,
        "metrics": {
            "day": day,
            "last_7_days": last_7_days,
            "last_30_days": {"visitors": day_visitors * 30},
        },
    }


def write_snapshot_with_websites(path, snapshot_date, *, studio_status="ok", funnel_by_vector=None):
    write_snapshot(
        path,
        snapshot_date,
        {},
        websites={
            "status": "partial" if studio_status != "ok" else "ok",
            "properties": {
                "main": website_property("tiffanywoodyoga.com"),
                "studio": website_property(
                    "studio.tiffanywoodyoga.com",
                    status=studio_status,
                    error="studio unavailable" if studio_status != "ok" else None,
                ),
                "habit": website_property(
                    "habit.tiffanywoodyoga.com",
                    funnel_by_vector=funnel_by_vector,
                ),
            },
        },
    )


def test_weekly_report_keeps_websites_separate(tmp_path):
    snapshot_dir = tmp_path / "social_growth"
    write_snapshot_with_websites(snapshot_dir, "2026-07-28")
    write_snapshot_with_websites(snapshot_dir, "2026-07-29")

    snapshots = weekly.load_daily_snapshots(snapshot_dir, week_end=date(2026, 7, 29))
    report = weekly.build_weekly_review(snapshots, week_end=date(2026, 7, 29))

    websites = report["website_performance"]
    assert websites["main"]["site_id"] == "tiffanywoodyoga.com"
    assert websites["studio"]["site_id"] == "studio.tiffanywoodyoga.com"
    assert websites["habit"]["site_id"] == "habit.tiffanywoodyoga.com"
    assert "combined_visitors" not in report
    assert "combined_visitors" not in websites


def test_weekly_report_preserves_habit_vector_funnel(tmp_path):
    snapshot_dir = tmp_path / "social_growth"
    vector = [
        {
            "event": "Habit Register Click",
            "source": "instagram",
            "content": "cue",
            "page_state": "class_defined",
            "path": "/ig",
            "events": 3,
        }
    ]
    write_snapshot_with_websites(snapshot_dir, "2026-07-28", funnel_by_vector=vector)
    write_snapshot_with_websites(snapshot_dir, "2026-07-29", funnel_by_vector=vector)

    snapshots = weekly.load_daily_snapshots(snapshot_dir, week_end=date(2026, 7, 29))
    report = weekly.build_weekly_review(snapshots, week_end=date(2026, 7, 29))

    assert report["website_performance"]["habit"]["funnel_by_vector"] == vector


def test_weekly_report_marks_failed_property_stale_and_uses_last_good_data(tmp_path):
    snapshot_dir = tmp_path / "social_growth"
    write_snapshot_with_websites(snapshot_dir, "2026-07-21")
    write_snapshot_with_websites(snapshot_dir, "2026-07-29", studio_status="error")

    snapshots = weekly.load_daily_snapshots(snapshot_dir, week_end=date(2026, 7, 29))
    report = weekly.build_weekly_review(snapshots, week_end=date(2026, 7, 29))

    studio = report["website_performance"]["studio"]
    assert report["snapshot_count"] == 1
    assert studio["status"] == "stale"
    assert studio["stale"] is True
    assert studio["failure_message"] == "studio unavailable"
    assert studio["last_good_at"] == "2026-07-21T13:20:00Z"
    assert studio["latest_7_days"]["visitors"] == 14
    assert studio["daily_trend"] == []


def test_weekly_review_requires_two_snapshots(tmp_path):
    snapshot_dir = tmp_path / "social_growth"
    write_snapshot(
        snapshot_dir,
        "2026-07-27",
        {
            "instagram_followers": 2303,
            "email_subscribers": 921,
            "next_habit_registrations": 0,
            "landing_day_visitors": 0,
            "landing_day_pageviews": 0,
            "habit_register_clicks_day": 0,
            "habit_signup_success_day": 0,
        },
    )

    snapshots = weekly.load_daily_snapshots(snapshot_dir, week_end=date(2026, 7, 27))
    report = weekly.build_weekly_review(snapshots, week_end=date(2026, 7, 27))

    assert report["status"] == "insufficient_data"
    assert report["snapshot_count"] == 1
    assert report["metrics"]["instagram_followers"] == {"start": 2303, "end": 2303, "delta": 0}
    assert report["recommendations"] == ["Collect at least two daily snapshots before changing the campaign."]


def test_weekly_review_computes_deltas_funnel_totals_and_campaigns(tmp_path):
    snapshot_dir = tmp_path / "social_growth"
    write_snapshot(
        snapshot_dir,
        "2026-07-21",
        {"instagram_followers": 2300},
    )
    write_snapshot(
        snapshot_dir,
        "2026-07-27",
        {
            "instagram_followers": 2303,
            "email_subscribers": 921,
            "next_habit_registrations": 0,
            "landing_day_visitors": 2,
            "landing_day_pageviews": 3,
            "habit_register_clicks_day": 1,
            "habit_signup_success_day": 0,
            "recent_campaign_variants": ["habit_entry_regular_reels:steady_first_step"],
            "upcoming_campaign_variants": ["habit_entry_regular_reels:find_out"],
        },
    )
    write_snapshot(
        snapshot_dir,
        "2026-07-28",
        {
            "instagram_followers": 2308,
            "email_subscribers": 924,
            "next_habit_registrations": 2,
            "landing_day_visitors": 4,
            "landing_day_pageviews": 6,
            "habit_register_clicks_day": 2,
            "habit_signup_success_day": 1,
            "recent_campaign_variants": ["habit_entry_regular_reels:steady_first_step"],
            "upcoming_campaign_variants": ["habit_entry_regular_reels:find_out"],
        },
    )

    snapshots = weekly.load_daily_snapshots(snapshot_dir, week_end=date(2026, 7, 28))
    report = weekly.build_weekly_review(snapshots, week_end=date(2026, 7, 28))

    assert report["status"] == "ok"
    assert report["week_start"] == "2026-07-22"
    assert report["snapshot_count"] == 2
    assert report["metrics"]["instagram_followers"] == {"start": 2303, "end": 2308, "delta": 5}
    assert report["metrics"]["email_subscribers"] == {"start": 921, "end": 924, "delta": 3}
    assert report["metrics"]["next_habit_registrations"] == {"start": 0, "end": 2, "delta": 2}
    assert report["metrics"]["landing_page"] == {
        "visitors": 6,
        "pageviews": 9,
        "habit_register_clicks": 3,
        "habit_signup_success": 1,
    }
    assert report["campaigns"] == {
        "recent_variants": ["habit_entry_regular_reels:steady_first_step"],
        "upcoming_variants": ["habit_entry_regular_reels:find_out"],
    }
    assert report["recommendations"] == [
        "Let the scheduled campaign publish before changing copy. No published variant evidence exists yet.",
        "Landing traffic is too small to judge conversion. Keep measuring before changing the page.",
    ]


def test_save_weekly_review_writes_week_end_file(tmp_path):
    report = weekly.build_weekly_review([], week_end=date(2026, 7, 28))

    path, markdown_path = weekly.save_weekly_review(report, output_dir=tmp_path / "weekly")

    assert path == tmp_path / "weekly" / "2026-07-28.json"
    assert markdown_path == tmp_path / "weekly" / "2026-07-28.md"
    assert json.loads(path.read_text())["status"] == "insufficient_data"


def test_weekly_review_deduplicates_latest_post_metrics_and_ranks_posts(tmp_path):
    snapshot_dir = tmp_path / "social_growth"
    early = {
        "zernio_post_id": "post-1",
        "scheduled_for": "2026-07-27T06:00:00-06:00",
        "posted_for_class": "2026-07-28",
        "class_name": "2026-07-16_expansion",
        "clip_name": "05_teaching_score8_17s",
        "platform_post_url": "https://instagram.example/post-1",
        "metrics": {
            "reach": 100,
            "views": 110,
            "likes": 2,
            "comments": 0,
            "shares": 0,
            "saves": 0,
            "clicks": 0,
            "follows": 0,
            "engagementRate": 1.8,
            "igReelsAvgWatchTime": 3000,
        },
    }
    mature = {
        **early,
        "metrics": {
            **early["metrics"],
            "reach": 209,
            "views": 242,
            "likes": 8,
            "engagementRate": 3.31,
            "igReelsAvgWatchTime": 5844,
        },
    }
    second = {
        "zernio_post_id": "post-2",
        "scheduled_for": "2026-07-26T17:30:00-06:00",
        "posted_for_class": "2026-07-27",
        "class_name": "2026-07-08_breath",
        "clip_name": "03_meditation_score8_17s",
        "platform_post_url": "https://instagram.example/post-2",
        "metrics": {
            "reach": 195,
            "views": 219,
            "likes": 5,
            "comments": 0,
            "shares": 1,
            "saves": 2,
            "clicks": 0,
            "follows": 1,
            "engagementRate": 2.28,
            "igReelsAvgWatchTime": 3450,
        },
    }
    write_snapshot(
        snapshot_dir,
        "2026-07-27",
        {"instagram_followers": 2303},
        analytics=[early, second],
    )
    write_snapshot(
        snapshot_dir,
        "2026-07-28",
        {"instagram_followers": 2304},
        analytics=[mature, second],
    )

    snapshots = weekly.load_daily_snapshots(snapshot_dir, week_end=date(2026, 7, 28))
    report = weekly.build_weekly_review(snapshots, week_end=date(2026, 7, 28))

    performance = report["post_performance"]
    assert performance["posts_analyzed"] == 2
    assert performance["totals"]["reach"] == 404
    assert performance["totals"]["views"] == 461
    assert performance["totals"]["growth_actions"] == 4
    assert performance["averages"]["engagement_rate"] == 2.79
    assert performance["averages"]["watch_time_seconds"] == 4.65
    assert performance["top_by_reach"]["zernio_post_id"] == "post-1"
    assert performance["top_by_growth_actions"]["zernio_post_id"] == "post-2"
    assert performance["posts"][0]["metrics"]["reach"] == 209


def test_weekly_review_compares_published_campaign_variant_with_baseline(tmp_path):
    snapshot_dir = tmp_path / "social_growth"
    baseline = {
        "zernio_post_id": "baseline-1",
        "scheduled_for": "2026-08-02T08:00:00-06:00",
        "posted_for_class": "2026-08-03",
        "metrics": {
            "reach": 100,
            "views": 120,
            "likes": 3,
            "comments": 0,
            "shares": 0,
            "saves": 0,
            "clicks": 0,
            "follows": 0,
            "engagementRate": 2.5,
            "igReelsAvgWatchTime": 4000,
        },
    }
    variant_posts = []
    campaign_posts = []
    for index, reach in enumerate((130, 150, 170), start=1):
        post_id = f"variant-{index}"
        variant_posts.append(
            {
                **baseline,
                "zernio_post_id": post_id,
                "scheduled_for": f"2026-08-0{index + 2}T08:00:00-06:00",
                "metrics": {
                    **baseline["metrics"],
                    "reach": reach,
                    "views": reach + 20,
                    "saves": 1,
                    "engagementRate": 3.5,
                    "igReelsAvgWatchTime": 6000,
                },
            }
        )
        campaign_posts.append(
            {
                "zernio_post_id": post_id,
                "campaign": "habit_entry_regular_reels",
                "ctaVariant": "steady_first_step",
                "habitTargetDate": "2026-08-08",
            }
        )
    write_snapshot(
        snapshot_dir,
        "2026-08-03",
        {"instagram_followers": 2304},
        analytics=[baseline],
    )
    write_snapshot(
        snapshot_dir,
        "2026-08-07",
        {"instagram_followers": 2306},
        analytics=[baseline, *variant_posts],
        campaign_posts=campaign_posts,
    )

    snapshots = weekly.load_daily_snapshots(snapshot_dir, week_end=date(2026, 8, 7))
    report = weekly.build_weekly_review(snapshots, week_end=date(2026, 8, 7))

    variant = report["campaign_performance"]["variants"][0]
    assert variant["key"] == "habit_entry_regular_reels:steady_first_step"
    assert variant["post_count"] == 3
    assert variant["averages"]["reach"] == 150
    assert variant["comparison_to_baseline"]["reach_delta"] == 50
    assert variant["comparison_to_baseline"]["watch_time_seconds_delta"] == 2
    assert variant["comparison_to_baseline"]["assessment"] == "better"


def test_markdown_and_slack_outputs_are_human_readable(tmp_path):
    snapshot_dir = tmp_path / "social_growth"
    analytics = [
        {
            "zernio_post_id": "post-1",
            "scheduled_for": "2026-07-27T06:00:00-06:00",
            "posted_for_class": "2026-07-28",
            "platform_post_url": "https://instagram.example/post-1",
            "metrics": {
                "reach": 209,
                "views": 242,
                "likes": 8,
                "comments": 0,
                "shares": 0,
                "saves": 0,
                "clicks": 0,
                "follows": 0,
                "engagementRate": 3.31,
                "igReelsAvgWatchTime": 5844,
            },
        }
    ]
    write_snapshot(
        snapshot_dir,
        "2026-07-27",
        {
            "instagram_followers": 2303,
            "email_subscribers": 921,
            "next_habit_registrations": 0,
        },
        analytics=analytics,
    )
    write_snapshot(
        snapshot_dir,
        "2026-07-28",
        {
            "instagram_followers": 2304,
            "email_subscribers": 921,
            "next_habit_registrations": 0,
        },
        analytics=analytics,
    )
    snapshots = weekly.load_daily_snapshots(snapshot_dir, week_end=date(2026, 7, 28))
    report = weekly.build_weekly_review(snapshots, week_end=date(2026, 7, 28))

    markdown = weekly.render_markdown(report)
    slack = weekly.render_slack(report)

    assert "# TWY Audience Growth Review" in markdown
    assert "Post Performance" in markdown
    assert "209" in markdown
    assert "*TWY audience growth review*" in slack
    assert "*IG followers:* 2,303 -> 2,304 (+1)" in slack
    assert "Top Reel:" in slack

    json_path, markdown_path = weekly.save_weekly_review(
        report,
        output_dir=tmp_path / "weekly",
    )
    assert json_path.name == "2026-07-28.json"
    assert markdown_path.name == "2026-07-28.md"


def test_weekly_review_excludes_posts_younger_than_24_hours(tmp_path):
    snapshot_dir = tmp_path / "social_growth"
    fresh = {
        "zernio_post_id": "fresh-post",
        "scheduled_for": "2026-07-28T14:00:00Z",
        "posted_for_class": "2026-07-29",
        "metrics": {
            "reach": 33,
            "views": 43,
            "engagementRate": 2.33,
            "igReelsAvgWatchTime": 8156,
        },
    }
    mature = {
        **fresh,
        "zernio_post_id": "mature-post",
        "scheduled_for": "2026-07-27T13:00:00Z",
        "posted_for_class": "2026-07-28",
    }
    write_snapshot(
        snapshot_dir,
        "2026-07-27",
        {"instagram_followers": 2303},
    )
    write_snapshot(
        snapshot_dir,
        "2026-07-29",
        {"instagram_followers": 2304},
        analytics=[fresh, mature],
    )

    snapshots = weekly.load_daily_snapshots(snapshot_dir, week_end=date(2026, 7, 29))
    report = weekly.build_weekly_review(snapshots, week_end=date(2026, 7, 29))

    assert report["post_performance"]["posts_analyzed"] == 1
    assert report["post_performance"]["posts"][0]["zernio_post_id"] == "mature-post"


def test_slack_dm_targets_jp_and_is_idempotent(tmp_path, monkeypatch):
    report = weekly.build_weekly_review([], week_end=date(2026, 7, 28))
    calls = []

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"ok": True}

    def fake_slack_ok(url, **kwargs):
        calls.append({"url": url, **kwargs})
        return Response()

    monkeypatch.setattr(weekly.requests, "post", fake_slack_ok)
    monkeypatch.setenv("SLACK_CHANNEL", "Cconfigured")
    state_path = tmp_path / "weekly" / weekly.SLACK_STATE_FILE
    state_path.parent.mkdir(parents=True)

    assert weekly.post_slack_dm_once(
        report,
        state_path=state_path,
        token="bot-token",
        user_id="UJP",
    )
    assert not weekly.post_slack_dm_once(
        report,
        state_path=state_path,
        token="bot-token",
        user_id="UJP",
    )
    assert calls[0]["url"] == "https://slack.com/api/chat.postMessage"
    assert calls[0]["json"]["channel"] == "UJP"
    assert calls[0]["headers"]["Authorization"] == "Bearer bot-token"
    assert "https://stats.tiffanywoodyoga.com/" in calls[0]["json"]["text"]
    assert "Cconfigured" not in json.dumps(calls[0])
    assert calls[0]["json"]["client_msg_id"] == weekly.deterministic_client_msg_id(report["week_end"])


def test_slack_dm_http_error_does_not_write_state(tmp_path):
    report = weekly.build_weekly_review([], week_end=date(2026, 7, 28))
    state_path = tmp_path / weekly.SLACK_STATE_FILE

    def fake_post(url, **kwargs):
        raise weekly.requests.HTTPError("Slack unavailable")

    with pytest.raises(weekly.requests.HTTPError, match="Slack unavailable"):
        weekly.post_slack_dm_once(
            report,
            state_path=state_path,
            token="bot-token",
            user_id="UJP",
            post=fake_post,
        )

    assert not state_path.exists()


def test_slack_dm_rejection_does_not_write_state(tmp_path):
    report = weekly.build_weekly_review([], week_end=date(2026, 7, 28))
    state_path = tmp_path / weekly.SLACK_STATE_FILE

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"ok": False, "error": "channel_not_found"}

    with pytest.raises(RuntimeError, match="channel_not_found"):
        weekly.post_slack_dm_once(
            report,
            state_path=state_path,
            token="bot-token",
            user_id="UJP",
            post=lambda url, **kwargs: Response(),
        )

    assert not state_path.exists()


# --- page-state-aware habit recommendation (2026-08-23) ---------------------


def fake_month_dates(dates):
    """dict {(year, month): date | None} -> resolver, raising on missing keys."""
    def resolver(year, month):
        return dates[(year, month)]
    return resolver


def test_pre_class_days_gap_between_class_and_next_plan(monkeypatch):
    # August class happened on the 8th; September has no plan yet. Every day of
    # the Aug 9-15 window is post-class, matching the live page's behavior.
    monkeypatch.setattr(
        weekly, "_habit_class_date_for_month",
        fake_month_dates({(2026, 8): date(2026, 8, 8), (2026, 9): None}),
    )
    assert weekly._habit_pre_class_days(date(2026, 8, 15)) == (0, 7)


def test_pre_class_days_after_next_plan_exists(monkeypatch):
    # September's plan exists, so the page rolls forward and shows Register.
    monkeypatch.setattr(
        weekly, "_habit_class_date_for_month",
        fake_month_dates({(2026, 8): date(2026, 8, 8), (2026, 9): date(2026, 9, 12)}),
    )
    assert weekly._habit_pre_class_days(date(2026, 8, 23)) == (7, 7)


def test_pre_class_days_window_straddles_class_day(monkeypatch):
    # Aug 5-11 window: Aug 5-8 are pre-class (class day counts, registration
    # runs to the 9:00 start); Aug 9-11 post-class while September is unplanned.
    monkeypatch.setattr(
        weekly, "_habit_class_date_for_month",
        fake_month_dates({(2026, 8): date(2026, 8, 8), (2026, 9): None}),
    )
    assert weekly._habit_pre_class_days(date(2026, 8, 11)) == (4, 7)


def test_pre_class_days_unknown_when_classes_api_down(monkeypatch):
    import requests

    def boom(year, month):
        raise requests.RequestException("down")

    monkeypatch.setattr(weekly, "_habit_class_date_for_month", boom)
    assert weekly._habit_pre_class_days(date(2026, 8, 23)) is None


def habit_performance(visitors, register_clicks):
    return {
        "main": {"status": "error", "latest_7_days": {}, "top_sources": [], "top_entry_pages": []},
        "habit": {
            "status": "ok",
            "latest_7_days": {"visitors": visitors},
            "funnel_events": {"Habit Register Click": register_clicks},
        },
    }


def test_habit_recommendation_states_post_class_window(monkeypatch):
    monkeypatch.setattr(weekly, "_habit_pre_class_days", lambda week_end, days=7: (0, 7))
    lines = weekly._website_recommendations(habit_performance(11, 0), date(2026, 8, 17))
    assert len(lines) == 1
    assert "post-class the whole window" in lines[0]
    assert "No CTA change indicated" in lines[0]


def test_habit_recommendation_keeps_review_on_pre_class_days(monkeypatch):
    monkeypatch.setattr(weekly, "_habit_pre_class_days", lambda week_end, days=7: (4, 7))
    lines = weekly._website_recommendations(habit_performance(11, 0), date(2026, 8, 11))
    assert len(lines) == 1
    assert "Review the /ig CTA path" in lines[0]
    assert "4 pre-class day(s)" in lines[0]


def test_habit_recommendation_flags_unknown_state(monkeypatch):
    monkeypatch.setattr(weekly, "_habit_pre_class_days", lambda week_end, days=7: None)
    lines = weekly._website_recommendations(habit_performance(11, 0), date(2026, 8, 17))
    assert len(lines) == 1
    assert "page state unknown" in lines[0]


def test_habit_recommendation_silent_when_clicks_exist(monkeypatch):
    def explode(week_end, days=7):
        raise AssertionError("state lookup must not run when clicks exist")

    monkeypatch.setattr(weekly, "_habit_pre_class_days", explode)
    assert weekly._website_recommendations(habit_performance(11, 3), date(2026, 8, 17)) == []
