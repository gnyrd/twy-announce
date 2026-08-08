"""Durable per-post social performance."""
from datetime import datetime, timedelta, timezone
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pytest

from social_post_performance import (
    materialize_post_performance,
    normalized_metrics,
)


NOW = datetime(2026, 8, 8, 12, 0, tzinfo=timezone.utc)


def path_for(tmp_path):
    def build(year, month):
        return tmp_path / f"{year:04d}-{month:02d}" / ".performance.json"

    return build


def post(post_id, scheduled_for, analytics=None, **extra):
    return {
        "zernio_post_id": post_id,
        "scheduled_for": scheduled_for,
        "post_type": extra.pop("post_type", "reel"),
        "clip_name": extra.pop("clip_name", "01_wisdom_score9_11s"),
        "class_name": extra.pop("class_name", "2026-07-14_flow"),
        "analytics": analytics,
        **extra,
    }


def read(tmp_path, period):
    return json.loads((tmp_path / period / ".performance.json").read_text())


def test_every_provider_metric_is_kept_not_a_chosen_subset():
    metrics = normalized_metrics({
        "reach": 403,
        "impressions": 543,
        "engagementRate": 7.55,
        "igReelsAvgWatchTime": 12637,
        "videoDurationSeconds": 52,
        "lastUpdated": "2026-08-07 16:32:21",
        "somethingZernioAddsLater": 9,
    })

    # No allowlist: a metric nobody has written code for still lands.
    assert metrics["somethingZernioAddsLater"] == 9
    assert metrics["igReelsAvgWatchTime"] == 12637
    assert metrics["videoDurationSeconds"] == 52
    assert metrics["lastUpdated"] == "2026-08-07 16:32:21"


def test_growth_actions_are_derived_from_the_actions_that_exist():
    metrics = normalized_metrics(
        {"follows": 1, "saves": 4, "shares": 1, "clicks": 0, "reach": 10}
    )
    assert metrics["growth_actions"] == 6


def test_counts_never_move_backwards_across_reads(tmp_path):
    build = path_for(tmp_path)
    materialize_post_performance(
        [post("a", "2026-08-01T08:00:00-06:00", {"reach": 400, "engagementRate": 7.5})],
        NOW,
        path_for=build,
    )
    # A later read reporting less must not erase what was already recorded.
    materialize_post_performance(
        [post("a", "2026-08-01T08:00:00-06:00", {"reach": 12, "engagementRate": 1.1})],
        NOW,
        path_for=build,
    )

    current = read(tmp_path, "2026-08")["posts"]["a"]["current"]
    assert current["reach"] == 400
    # A rate is not a counter; the newest reading wins.
    assert current["engagementRate"] == 1.1


def test_a_post_that_has_not_gone_out_is_never_counted_as_measured(tmp_path):
    build = path_for(tmp_path)
    future = (NOW + timedelta(days=3)).isoformat()
    materialize_post_performance(
        [post("future", future, {"reach": 0, "impressions": 0, "views": 0})],
        NOW,
        path_for=build,
    )

    entry = read(tmp_path, future[:7])["posts"]["future"]
    assert entry["published"] is False
    assert entry["has_metrics"] is False


def test_a_premature_reading_corrects_itself_once_the_post_publishes(tmp_path):
    build = path_for(tmp_path)
    scheduled = "2026-08-20T08:00:00-06:00"
    materialize_post_performance(
        [post("s", scheduled, {"reach": 0})], NOW, path_for=build
    )
    assert read(tmp_path, "2026-08")["posts"]["s"]["has_metrics"] is False

    later = datetime(2026, 8, 21, 12, 0, tzinfo=timezone.utc)
    materialize_post_performance(
        [post("s", scheduled, {"reach": 88})], later, path_for=build
    )

    entry = read(tmp_path, "2026-08")["posts"]["s"]
    assert entry["published"] is True
    assert entry["has_metrics"] is True
    assert entry["current"]["reach"] == 88


def test_an_unmeasured_post_is_recorded_as_unmeasured_not_dropped(tmp_path):
    build = path_for(tmp_path)
    materialize_post_performance(
        [post("story", "2026-08-01T08:00:00-06:00", None, post_type="story")],
        NOW,
        path_for=build,
    )

    entry = read(tmp_path, "2026-08")["posts"]["story"]
    assert entry["post_type"] == "story"
    assert entry["has_metrics"] is False
    assert entry["current"] is None


def test_posts_are_filed_by_the_month_they_were_scheduled_for(tmp_path):
    build = path_for(tmp_path)
    materialize_post_performance(
        [
            post("may", "2026-05-06T08:00:00-06:00", {"reach": 1}),
            post("jun", "2026-06-06T08:00:00-06:00", {"reach": 2}),
        ],
        NOW,
        path_for=build,
    )

    assert "may" in read(tmp_path, "2026-05")["posts"]
    assert "jun" in read(tmp_path, "2026-06")["posts"]


def test_milestones_are_captured_once_and_then_frozen(tmp_path):
    build = path_for(tmp_path)
    scheduled = "2026-08-01T08:00:00-06:00"
    materialize_post_performance(
        [post("m", scheduled, {"reach": 100})], NOW, path_for=build
    )
    # Scheduled 2026-08-01 14:00Z, so at NOW it is 24 hours old but not 7 days.
    first = read(tmp_path, "2026-08")["posts"]["m"]["milestones"]
    assert set(first) == {"24_hour"}
    assert first["24_hour"]["metrics"]["reach"] == 100

    materialize_post_performance(
        [post("m", scheduled, {"reach": 999})], NOW + timedelta(days=2), path_for=build
    )
    entry = read(tmp_path, "2026-08")["posts"]["m"]
    # The first milestone is frozen at what it saw; the 7-day one has now
    # aged in and captures the newer figure.
    assert entry["milestones"]["24_hour"]["metrics"]["reach"] == 100
    assert entry["milestones"]["7_day"]["metrics"]["reach"] == 999
    assert entry["current"]["reach"] == 999


def test_a_row_without_an_id_or_a_schedule_is_skipped(tmp_path):
    build = path_for(tmp_path)
    written = materialize_post_performance(
        [
            {"zernio_post_id": "", "scheduled_for": "2026-08-01T08:00:00-06:00"},
            {"zernio_post_id": "x", "scheduled_for": None},
            {"zernio_post_id": "y", "scheduled_for": "not a date"},
        ],
        NOW,
        path_for=build,
    )
    assert written == []


def test_an_unreadable_document_is_refused_rather_than_overwritten(tmp_path):
    build = path_for(tmp_path)
    target = tmp_path / "2026-08" / ".performance.json"
    target.parent.mkdir(parents=True)
    target.write_text(json.dumps({"version": 2, "posts": {}}))

    with pytest.raises(ValueError):
        materialize_post_performance(
            [post("a", "2026-08-01T08:00:00-06:00", {"reach": 1})], NOW, path_for=build
        )
