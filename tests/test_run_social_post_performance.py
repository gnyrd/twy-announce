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
    # no analytics token in these worlds: the counters stand alone unless a
    # test patches the client in
    monkeypatch.setattr(collector, "youtube_analytics_client", lambda: None)
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
    assert payload["analytics"]["engagementRate"] == 1.15   # (8 + 0) / 693 * 100, likes plus comments per hundred views
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
    _youtube_world(monkeypatch, stats={"viewCount": "0", "likeCount": "0", "commentCount": "0"})
    assert "engagementRate" not in collector.youtube_fetcher()("post1")[1]["analytics"]   # no views, no rate


def test_the_shorts_ledger_gains_a_class_type_for_the_store():
    rows = collector.with_class_type([{"class_name": "2026-07-23_expansion"}, {"class_name": "odd", "class_type": "kept"}, {"class_name": ""}])
    assert [r.get("class_type") for r in rows] == ["expansion", "kept", None]


def test_youtube_needs_the_data_api_key(monkeypatch):
    monkeypatch.setenv("ZERNIO_API_KEY", "k")
    monkeypatch.delenv("YOUTUBE_API_KEY", raising=False)
    import pytest

    with pytest.raises(SystemExit):
        collector.youtube_fetcher()


class _FakeAnalytics:
    """reports().query(...).execute() with canned rows per metric set."""

    def __init__(self, totals, sources):
        self.totals, self.sources, self.calls = totals, sources, []

    def reports(self):
        return self

    def query(self, **params):
        self.calls.append(params)
        rows = self.sources if params.get("dimensions") == "insightTrafficSourceType" else self.totals
        return type("R", (), {"execute": lambda _self: {"rows": rows}})()


def test_studio_analytics_are_flattened_into_the_store():
    fake = _FakeAnalytics(
        totals=[[1314, 135, 54, 13, 50.0, 8, 0, 2, 1, 0]],
        sources=[["SHORTS", 1285], ["NO_LINK_OTHER", 13], ["YT_SEARCH", 11], ["YT_CHANNEL", 4], ["SUBSCRIBER", 1]],
    )
    out = collector.youtube_analytics_metrics(fake, "1dX4KpUe2kE", "2026-09-15T16:00:00Z")
    assert out["engagedViews"] == 135 and out["stayedPct"] == 10.3
    assert "views" not in out and "likes" not in out   # the Data API's live counters keep those
    assert out["avgViewDurationSec"] == 13 and out["avgViewPct"] == 50.0 and out["watchMinutes"] == 54
    assert out["shares"] == 2 and out["subscribersGained"] == 1 and out["subscribersLost"] == 0
    assert out["trafficShortsFeedPct"] == 97.8 and out["trafficSearchPct"] == 0.8
    assert out["trafficChannelPct"] == 0.4 and out["trafficOtherPct"] == 1.0
    # both queries scoped to this video from its publish day
    assert all(c["filters"] == "video==1dX4KpUe2kE" and c["startDate"] == "2026-09-15" for c in fake.calls)


def test_analytics_youtube_has_not_produced_yet_write_nothing():
    fake = _FakeAnalytics(totals=[], sources=[])
    assert collector.youtube_analytics_metrics(fake, "vid", None) == {}


def test_the_fetcher_carries_analytics_beside_the_counters_and_survives_their_failure(monkeypatch):
    _youtube_world(monkeypatch, stats={"viewCount": "693", "likeCount": "8", "commentCount": "0"})
    fake = _FakeAnalytics(totals=[[693, 70, 30, 13, 50.0, 8, 0, 0, 0, 0]], sources=[["SHORTS", 693]])
    monkeypatch.setattr(collector, "youtube_analytics_client", lambda: fake)
    _status, payload = collector.youtube_fetcher()("post1")
    assert payload["analytics"]["views"] == 693 and payload["analytics"]["stayedPct"] == 10.1
    assert payload["analytics"]["trafficShortsFeedPct"] == 100.0

    class Broken:
        def reports(self):
            raise RuntimeError("analytics down")

    monkeypatch.setattr(collector, "youtube_analytics_client", lambda: Broken())
    status, payload = collector.youtube_fetcher()("post1")
    assert status == 200 and payload["analytics"]["views"] == 693 and "stayedPct" not in payload["analytics"]


def test_without_a_token_the_counters_stand_alone(monkeypatch):
    monkeypatch.delenv("YOUTUBE_ANALYTICS_OAUTH_TOKEN_FILE", raising=False)
    assert collector.youtube_analytics_client() is None



def test_youtube_history_adds_inventory_placements_once(tmp_path, monkeypatch):
    """Since 2026-09-17 the yt_short publisher writes only the inventory, so the
    collector reads its placements beside the old ledger rows."""
    import json
    ledger = tmp_path / "yt_shorts_history.json"
    ledger.write_text(json.dumps([
        {"post_type": "yt_short", "class_name": "2026-08-11_flow", "clip_name": "03_wisdom_score8_27s",
         "scheduled_for": "2026-09-15T10:00:00-06:00", "zernio_post_id": "old1"},
        {"post_type": "yt_short", "class_name": "2026-08-11_flow", "clip_name": "03_wisdom_score8_27s",
         "scheduled_for": "2026-09-19T10:00:00-06:00", "zernio_post_id": "gone"},   # withdrawn at the switch
    ]))
    inventory = tmp_path / "inventory.json"
    inventory.write_text(json.dumps({"version": 1, "items": {
        "clip:2026-08-11_flow/03_wisdom_score8_27s": {
            "kind": "clip", "class_name": "2026-08-11_flow", "clip_name": "03_wisdom_score8_27s", "class_type": "flow",
            "placements": [
                {"publisher": "yt_short", "zernio_post_id": "old1", "scheduled_for": "2026-09-15T10:00:00-06:00", "status": "published", "post_type": "yt_short", "source": {}},
                {"publisher": "yt_short", "zernio_post_id": "new1", "scheduled_for": "2026-09-21T08:00:00-06:00", "status": "scheduled", "post_type": "class", "source": {"title": "A title"}},
                {"publisher": "yt_short", "zernio_post_id": "gone", "scheduled_for": "2026-09-19T10:00:00-06:00", "status": "withdrawn", "post_type": "yt_short", "source": {}},
                {"publisher": "ig_reel", "zernio_post_id": "ig1", "scheduled_for": "2026-09-10T08:00:00-06:00", "status": "published", "post_type": "reel", "source": {}},
            ]},
        "quote:2026-06-22_strength/05_teaching_score8_9s": {
            "kind": "quote", "class_name": "2026-06-22_strength", "clip_name": "05_teaching_score8_9s", "class_type": "strength",
            "placements": [
                {"publisher": "yt_short", "zernio_post_id": "q1", "scheduled_for": "2026-09-18T07:00:00-06:00", "status": "scheduled", "post_type": "quote", "source": {"title": "Q"}},
            ]},
    }}))
    monkeypatch.setattr(collector, "history_path", lambda platform="instagram": ledger)
    monkeypatch.setattr(collector, "clips_inventory_path", lambda: inventory)
    rows = collector.platform_history("youtube")
    assert [r["zernio_post_id"] for r in rows] == ["old1", "new1", "q1"]
    assert rows[1]["class_type"] == "flow" and rows[1]["title"] == "A title" and rows[2]["kind"] == "quote"
    # Instagram reads the inventory too (since the 2026-09-17 switch): the
    # ig_reel placement joins the ledger rows, the yt_short ones do not.
    assert [r["zernio_post_id"] for r in collector.platform_history("instagram")] == ["old1", "gone", "ig1"]
