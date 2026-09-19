"""The contribution switch in the growth reports: a platform the switch has
off has no section in the daily snapshot and no metric, section or arm in
the weekly review; Instagram is untouched either way."""
from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timezone

import social_growth_report as daily
import social_growth_weekly as weekly


def write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload) + "\n")


def create_marvy_db(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE events (id INTEGER PRIMARY KEY, event_name TEXT, event_start_datetime TEXT, "
        "event_end_datetime TEXT, registration_required INTEGER, registration_limit INTEGER, "
        "number_of_registrations INTEGER, is_cancelled INTEGER)"
    )
    conn.commit()
    conn.close()


def write_snapshot(path, snapshot_date, summary):
    path.mkdir(parents=True, exist_ok=True)
    (path / f"{snapshot_date}.json").write_text(json.dumps({
        "date": snapshot_date, "captured_at": f"{snapshot_date}T13:20:00Z", "summary": summary,
        "campaigns": {"posts": []}, "zernio": {"analytics": {"posts": []}}, "websites": {},
    }) + "\n")


def _root(tmp_path, monkeypatch):
    for name in ("PLAUSIBLE_API_KEY", "PLAUSIBLE_SITE_ID", "PLAUSIBLE_SITE_IDS"):
        monkeypatch.delenv(name, raising=False)
    twy_root, data_root = tmp_path, tmp_path / "data"
    for platform, key in (("instagram", "follower_count"), ("facebook", "follower_count"), ("youtube", "subscriber_count")):
        write_json(twy_root / f"announce/data/{platform}/history/2026-07-27.json", {"date": "2026-07-27", key: 100})
    write_json(twy_root / "announce/data/email/history/2026-07-27.json",
               {"captured_at": "2026-07-27T11:55:02Z", "list_name": "Email: Subscribed", "subscriber_count": 921})
    create_marvy_db(data_root / "marvy.db")
    write_json(twy_root / "clips/state/ig_history.json", [])
    return twy_root, data_root


def test_daily_snapshot_drops_the_platforms_the_switch_has_off(tmp_path, monkeypatch):
    twy_root, data_root = _root(tmp_path, monkeypatch)
    captured_at = datetime(2026, 7, 27, 20, 15, tzinfo=timezone.utc)
    kwargs = dict(captured_at=captured_at, twy_root=twy_root, data_root=data_root,
                  zernio_fetch_post=None, zernio_account_health=None)
    everything = daily.collect_snapshot(platforms=("instagram", "facebook", "youtube"), **kwargs)
    assert everything["facebook"]["followers"]["count"] == 100
    assert everything["youtube"]["subscribers"]["count"] == 100
    assert "zernio_facebook" in everything
    assert everything["summary"]["facebook_followers"] == 100

    instagram_only = daily.collect_snapshot(platforms=("instagram",), **kwargs)
    assert instagram_only["platforms"] == ["instagram"]
    assert "facebook" not in instagram_only and "youtube" not in instagram_only
    assert "zernio_facebook" not in instagram_only
    assert instagram_only["instagram"]["followers"]["count"] == 100
    assert instagram_only["summary"]["instagram_followers"] == 100
    assert instagram_only["summary"]["facebook_followers"] is None
    assert instagram_only["summary"]["youtube_subscribers"] is None


def _snapshots(tmp_path):
    snapshot_dir = tmp_path / "social_growth"
    for day, followers in (("2026-07-28", 10), ("2026-07-29", 12)):
        write_snapshot(snapshot_dir, day, {
            "instagram_followers": followers, "facebook_followers": followers + 100,
            "youtube_subscribers": followers + 1000, "email_subscribers": 900,
            "next_habit_registrations": 5,
        })
    return weekly.load_daily_snapshots(snapshot_dir, week_end=date(2026, 7, 29))


def test_weekly_review_covers_only_the_live_platforms(tmp_path):
    snapshots = _snapshots(tmp_path)
    absent = tmp_path / "absent.json"
    full = weekly.build_weekly_review(snapshots, week_end=date(2026, 7, 29), youtube_store=tmp_path / "yt",
                                      comment_to_dm_state=absent, platforms=("instagram", "facebook", "youtube"))
    assert full["metrics"]["facebook_followers"]["delta"] == 2
    assert full["metrics"]["youtube_subscribers"]["delta"] == 2
    markdown, slack = weekly.render_markdown(full), weekly.render_slack(full)
    assert "## YouTube Shorts" in markdown and "(Facebook)" in markdown and "both platforms" in markdown
    assert "*YouTube subscribers:*" in slack

    narrowed = weekly.build_weekly_review(snapshots, week_end=date(2026, 7, 29), youtube_store=tmp_path / "yt",
                                          comment_to_dm_state=absent, platforms=("instagram",))
    assert narrowed["platforms"] == ["instagram"]
    assert narrowed["metrics"]["instagram_followers"]["delta"] == 2
    assert "facebook_followers" not in narrowed["metrics"] and "youtube_subscribers" not in narrowed["metrics"]
    assert narrowed["youtube_shorts"]["published"] == 0 and narrowed["facebook_quote_formats"] == {}
    markdown, slack = weekly.render_markdown(narrowed), weekly.render_slack(narrowed)
    assert "YouTube" not in markdown and "Facebook" not in markdown
    assert "Quote card looks (Instagram)" in markdown
    assert "YouTube" not in slack and "FB quote" not in slack
    assert "*IG followers:*" in slack


def test_a_review_written_before_the_switch_renders_everything(tmp_path):
    snapshots = _snapshots(tmp_path)
    old = weekly.build_weekly_review(snapshots, week_end=date(2026, 7, 29), youtube_store=tmp_path / "yt",
                                     comment_to_dm_state=tmp_path / "absent.json", platforms=("instagram", "facebook", "youtube"))
    old.pop("platforms")
    assert "## YouTube Shorts" in weekly.render_markdown(old)


def test_comment_to_dm_rows_follow_the_platforms(tmp_path):
    state = tmp_path / "comment_automations.json"
    state.write_text(json.dumps({"automations": {
        "instagram": {"id": "ig1", "is_active": True, "stats": {"triggered": 3, "dmsSent": 3}},
        "facebook": {"id": "fb1", "is_active": True, "stats": {"triggered": 1, "dmsSent": 1}},
    }}))
    snapshots = _snapshots(tmp_path)
    both = weekly.build_weekly_review(snapshots, week_end=date(2026, 7, 29), youtube_store=tmp_path / "yt",
                                      comment_to_dm_state=state, platforms=("instagram", "facebook"))
    assert set(both["comment_to_dm"]["platforms"]) == {"instagram", "facebook"}
    one = weekly.build_weekly_review(snapshots, week_end=date(2026, 7, 29), youtube_store=tmp_path / "yt",
                                     comment_to_dm_state=state, platforms=("instagram",))
    assert set(one["comment_to_dm"]["platforms"]) == {"instagram"}
    assert "| Facebook |" not in weekly.render_markdown(one)
