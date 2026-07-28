from __future__ import annotations

import json
from datetime import date

import social_growth_weekly as weekly


def write_snapshot(path, snapshot_date, summary):
    path.mkdir(parents=True, exist_ok=True)
    (path / f"{snapshot_date}.json").write_text(
        json.dumps(
            {
                "date": snapshot_date,
                "captured_at": f"{snapshot_date}T13:20:00Z",
                "summary": summary,
            }
        )
        + "\n"
    )


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
        "Keep the current campaign running until the next weekly review has published-post evidence."
    ]


def test_save_weekly_review_writes_week_end_file(tmp_path):
    report = {
        "week_end": "2026-07-28",
        "status": "ok",
    }

    path = weekly.save_weekly_review(report, output_dir=tmp_path / "weekly")

    assert path == tmp_path / "weekly" / "2026-07-28.json"
    assert json.loads(path.read_text())["status"] == "ok"
