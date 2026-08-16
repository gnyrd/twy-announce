"""Platform routing for the per-post performance collector."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import run_social_post_performance as collector


def test_history_path_selects_the_ledger_per_platform():
    assert collector.history_path("instagram").name == "ig_history.json"
    assert collector.history_path("facebook").name == "fb_history.json"
    assert collector.history_path().name == "ig_history.json"


def test_account_env_maps_each_platform():
    assert collector.ACCOUNT_ENV["facebook"] == "ZERNIO_FACEBOOK_ACCOUNT_ID"
    assert collector.ACCOUNT_ENV["instagram"] == "ZERNIO_INSTAGRAM_ACCOUNT_ID"


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
