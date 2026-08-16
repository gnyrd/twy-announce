import pytest

import meta_insights
from meta_insights import MEDIA_METRICS, MetaError, MetaInsights


class FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


def routed_get(routes):
    """Build a fake requests.get that returns the first matching route payload."""
    calls = []

    def _get(url, params=None, timeout=None):
        params = params or {}
        calls.append((url, params))
        for predicate, payload in routes:
            if predicate(url, params):
                return FakeResp(payload)
        raise AssertionError(f"no route for {url} {params}")

    _get.calls = calls
    return _get


def install(monkeypatch, routes):
    fake = routed_get(routes)
    monkeypatch.setattr(meta_insights.requests, "get", fake)
    return fake


def test_page_summary_parses(monkeypatch):
    install(monkeypatch, [
        (lambda u, p: u.endswith("/136254809853695"),
         {"name": "Tiffany Wood Yoga", "fan_count": 2320,
          "instagram_business_account": {"username": "tiffanywoodyoga", "followers_count": 2315}}),
    ])
    node = MetaInsights("tok").page_summary()
    assert node["name"] == "Tiffany Wood Yoga"
    assert node["instagram_business_account"]["followers_count"] == 2315


def test_meta_error_raised_on_error_payload(monkeypatch):
    install(monkeypatch, [(lambda u, p: True, {"error": {"message": "bad token", "code": 190}})])
    with pytest.raises(MetaError) as excinfo:
        MetaInsights("tok").page_summary()
    assert excinfo.value.error["code"] == 190


def test_ig_account_reach_trims_to_days(monkeypatch):
    install(monkeypatch, [
        (lambda u, p: u.endswith("/insights"),
         {"data": [{"name": "reach", "values": [
             {"end_time": "2026-08-13T07:00:00+0000", "value": 10},
             {"end_time": "2026-08-14T07:00:00+0000", "value": 20},
             {"end_time": "2026-08-15T07:00:00+0000", "value": 30},
         ]}]}),
    ])
    series = MetaInsights("tok").ig_account_reach(days=2)
    assert series == [{"date": "2026-08-14", "reach": 20}, {"date": "2026-08-15", "reach": 30}]


def test_ig_recent_media_attaches_insights(monkeypatch):
    install(monkeypatch, [
        (lambda u, p: u.endswith("/media"),
         {"data": [{"id": "M1", "media_type": "VIDEO", "timestamp": "2026-08-14T00:00:00+0000", "permalink": "http://x/1"}]}),
        (lambda u, p: u.endswith("M1/insights") and p.get("metric") == MEDIA_METRICS,
         {"data": [{"name": "reach", "values": [{"value": 48}]}, {"name": "likes", "values": [{"value": 2}]}]}),
    ])
    media = MetaInsights("tok").ig_recent_media(limit=1)
    assert media[0]["id"] == "M1"
    assert media[0]["media_type"] == "VIDEO"
    assert media[0]["insights"] == {"reach": 48, "likes": 2}


def test_media_insights_falls_back_to_reach(monkeypatch):
    # The full metric set errors for this media type; the reader must fall back
    # to reach rather than dropping the row.
    install(monkeypatch, [
        (lambda u, p: u.endswith("/media"),
         {"data": [{"id": "M2", "media_type": "IMAGE", "timestamp": "2026-08-14T00:00:00+0000", "permalink": "http://x/2"}]}),
        (lambda u, p: u.endswith("M2/insights") and p.get("metric") == MEDIA_METRICS,
         {"error": {"message": "(#100) incompatible metric", "code": 100}}),
        (lambda u, p: u.endswith("M2/insights") and p.get("metric") == "reach",
         {"data": [{"name": "reach", "values": [{"value": 15}]}]}),
    ])
    media = MetaInsights("tok").ig_recent_media(limit=1)
    assert media[0]["insights"] == {"reach": 15}


def test_summary_shape_and_fb_note(monkeypatch):
    install(monkeypatch, [
        (lambda u, p: u.endswith("/136254809853695") and "fields" in p and "name" in p["fields"],
         {"name": "Tiffany Wood Yoga", "fan_count": 2320,
          "instagram_business_account": {"username": "tiffanywoodyoga", "followers_count": 2315}}),
        (lambda u, p: u.endswith("/insights") and p.get("metric") == "reach" and p.get("period") == "day",
         {"data": [{"name": "reach", "values": [{"end_time": "2026-08-15T07:00:00+0000", "value": 30}]}]}),
        (lambda u, p: u.endswith("/media"), {"data": []}),
        (lambda u, p: u.endswith("/posts"), {"data": []}),
    ])
    summary = MetaInsights("tok").summary(media_limit=3, reach_days=7)
    assert summary["page"]["name"] == "Tiffany Wood Yoga"
    assert summary["instagram"]["account"]["username"] == "tiffanywoodyoga"
    assert summary["instagram"]["reach_daily"] == [{"date": "2026-08-15", "reach": 30}]
    assert "deprecated" in summary["facebook"]["note"]
