"""Platform routing for the per-post performance collector."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import run_social_post_performance as collector


def test_history_path_selects_the_ledger_per_platform():
    assert collector.history_path("instagram").name == "ig_history.json"
    assert collector.history_path("facebook").name == "fb_history.json"
    assert collector.history_path("youtube").name == "yt_shorts_history.json"
    assert collector.history_path().name == "ig_history.json"


def test_account_env_maps_each_platform():
    assert collector.ACCOUNT_ENV["facebook"] == "ZERNIO_FACEBOOK_ACCOUNT_ID"
    assert collector.ACCOUNT_ENV["instagram"] == "ZERNIO_INSTAGRAM_ACCOUNT_ID"
    assert collector.ACCOUNT_ENV["youtube"] == "ZERNIO_YOUTUBE_ACCOUNT_ID"


def test_each_platform_writes_its_own_store():
    import twy_paths

    assert collector.PERFORMANCE_PATHS["instagram"] is twy_paths.social_post_performance_path
    assert collector.PERFORMANCE_PATHS["facebook"] is twy_paths.facebook_post_performance_path
    assert collector.PERFORMANCE_PATHS["youtube"] is twy_paths.youtube_post_performance_path


def test_analytics_fetcher_reads_the_given_account_env(monkeypatch):
    captured = {}
    monkeypatch.setenv("ZERNIO_API_KEY", "k")
    monkeypatch.setenv("ZERNIO_FACEBOOK_ACCOUNT_ID", "fb-acct")

    class Resp:
        status_code = 200

        def json(self):
            return {"analytics": {}}

    def fake_get(url, headers=None, params=None, timeout=None):
        captured.update(params or {})
        return Resp()

    monkeypatch.setattr(collector.requests, "get", fake_get)
    collector.analytics_fetcher("ZERNIO_FACEBOOK_ACCOUNT_ID")("post123")
    assert captured["accountId"] == "fb-acct"
    assert captured["postId"] == "post123"


def _youtube_world(monkeypatch, zernio_status=202, video_id="1dX4KpUe2kE", data_status=200, stats=None):
    """Zernio names the video (or not); the Data API measures it (or fails)."""
    monkeypatch.setenv("ZERNIO_API_KEY", "k")
    monkeypatch.setenv("ZERNIO_YOUTUBE_ACCOUNT_ID", "yt-acct")
    monkeypatch.setenv("YOUTUBE_API_KEY", "yt-key")
    calls = []

    class Resp:
        def __init__(self, status, body):
            self.status_code, self._body = status, body

        def json(self):
            return self._body

    def fake_get(url, headers=None, params=None, timeout=None):
        calls.append((url, dict(params or {})))
        if "zernio" in url:
            platform = {"platform": "youtube", "status": "published", "analytics": None, "syncStatus": "pending"}
            if video_id:
                platform["platformPostId"] = video_id
            return Resp(zernio_status, {"postId": params["postId"], "analytics": {"views": 0, "likes": 0}, "platformAnalytics": [platform], "syncStatus": "pending"})
        items = [{"id": video_id, "statistics": stats}] if stats is not None else []
        return Resp(data_status, {"items": items} if data_status < 400 else {"error": {"code": data_status}})

    monkeypatch.setattr(collector.requests, "get", fake_get)
    return calls


def test_youtube_is_measured_through_the_data_api_while_zernio_is_still_syncing(monkeypatch):
    calls = _youtube_world(monkeypatch, stats={"viewCount": "693", "likeCount": "8", "favoriteCount": "0", "commentCount": "0"})
    status, payload = collector.youtube_fetcher()("post1")
    assert status == 200
    assert payload["analytics"]["views"] == 693 and payload["analytics"]["likes"] == 8 and payload["analytics"]["comments"] == 0
    assert payload["analytics"]["lastUpdated"]
    assert payload["platformPostUrl"] == "https://www.youtube.com/shorts/1dX4KpUe2kE"
    assert payload["syncStatus"] == "youtube-data-api"
    assert calls[0][1] == {"postId": "post1", "accountId": "yt-acct"}
    assert calls[1][0] == collector.YOUTUBE_VIDEOS_URL and calls[1][1] == {"part": "statistics", "id": "1dX4KpUe2kE", "key": "yt-key"}
    # collect() files it as measured with the video's address
    collected, tally = collector.collect([{"zernio_post_id": "post1", "scheduled_for": "2026-09-15T10:00:00-06:00"}], collector.youtube_fetcher())
    assert tally == {"measured": 1, "pending": 0, "errors": 0}
    assert collected[0]["analytics"]["views"] == 693 and collected[0]["platform_post_url"].endswith("1dX4KpUe2kE")


def test_a_short_zernio_has_not_named_yet_stays_pending(monkeypatch):
    calls = _youtube_world(monkeypatch, video_id=None)
    status, _payload = collector.youtube_fetcher()("post1")
    assert status == 202 and len(calls) == 1
    _collected, tally = collector.collect([{"zernio_post_id": "post1", "scheduled_for": "2026-09-19T10:00:00-06:00"}], collector.youtube_fetcher())
    assert tally == {"measured": 0, "pending": 1, "errors": 0}


def test_a_data_api_refusal_counts_as_an_error_not_a_measurement(monkeypatch):
    _youtube_world(monkeypatch, data_status=403)
    status, _payload = collector.youtube_fetcher()("post1")
    assert status == 403
    _collected, tally = collector.collect([{"zernio_post_id": "post1", "scheduled_for": "2026-09-15T10:00:00-06:00"}], collector.youtube_fetcher())
    assert tally == {"measured": 0, "pending": 0, "errors": 1}
    _youtube_world(monkeypatch, stats=None)   # the id Zernio gave is not on YouTube
    assert collector.youtube_fetcher()("post1")[0] == 404


def test_the_shorts_ledger_gains_a_class_type_for_the_store():
    rows = collector.with_class_type([{"class_name": "2026-07-23_expansion"}, {"class_name": "odd", "class_type": "kept"}, {"class_name": ""}])
    assert [r.get("class_type") for r in rows] == ["expansion", "kept", None]


def test_youtube_needs_the_data_api_key(monkeypatch):
    monkeypatch.setenv("ZERNIO_API_KEY", "k")
    monkeypatch.delenv("YOUTUBE_API_KEY", raising=False)
    import pytest

    with pytest.raises(SystemExit):
        collector.youtube_fetcher()

