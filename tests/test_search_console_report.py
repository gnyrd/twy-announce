from datetime import date, datetime, timezone
import json

import search_console_report as gsc

SITEMAP = """<?xml version="1.0"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
<url><loc>https://tiffanywoodyoga.com/</loc></url>
<url><loc>https://tiffanywoodyoga.com/about</loc></url>
<url><loc>https://tiffanywoodyoga.com/blog/thin</loc></url>
</urlset>"""


class FakeClient:
    def __init__(self):
        self.bodies = []

    def query(self, body):
        self.bodies.append(body)
        dims = body["dimensions"]
        if dims == ["date"]:
            return {"rows": [
                {"keys": ["2026-09-01"], "clicks": 2, "impressions": 20, "ctr": 0.1, "position": 8.0},
                {"keys": ["2026-09-02"], "clicks": 0, "impressions": 13, "ctr": 0.0, "position": 9.0},
            ]}
        if dims == ["page"]:
            return {"rows": [
                {"keys": ["https://tiffanywoodyoga.com/"], "clicks": 2, "impressions": 30, "ctr": 0.066, "position": 8.4},
                {"keys": ["https://studio.tiffanywoodyoga.com/calendar"], "clicks": 0, "impressions": 3, "ctr": 0, "position": 12.0},
            ]}
        if dims == ["query"]:
            return {"rows": [{"keys": ["tiffany wood"], "clicks": 0, "impressions": 7, "ctr": 0, "position": 9.0}]}
        raise AssertionError(dims)

    def sitemaps(self):
        return [{"path": "https://tiffanywoodyoga.com/sitemap.xml", "lastDownloaded": "2026-09-03T00:00:00Z",
                 "isPending": False, "errors": 0, "warnings": 0,
                 "contents": [{"type": "web", "submitted": 34, "indexed": 20}]}]

    def inspect(self, url):
        verdict = "NEUTRAL" if url.endswith("/thin") else "PASS"
        return {"inspectionResult": {"indexStatusResult": {
            "verdict": verdict, "coverageState": "Submitted and indexed" if verdict == "PASS" else "Excluded by noindex tag",
            "lastCrawlTime": "2026-09-03T01:00:00Z"}}}


def test_collect_builds_snapshot_with_totals_hosts_and_index():
    client = FakeClient()
    snap = gsc.collect(client, today=date(2026, 9, 4), fetch=lambda url: SITEMAP,
                       captured_at=datetime(2026, 9, 4, 13, 0, tzinfo=timezone.utc))

    assert snap["status"] == "ok"
    assert snap["data_through"] == "2026-09-02"
    assert len(snap["daily"]) == 90
    assert snap["daily"][-1] == {"date": "2026-09-02", "clicks": 0, "impressions": 13, "ctr": 0.0, "position": 9.0}
    assert snap["daily"][0]["clicks"] == 0  # missing days are filled with zeros
    assert snap["last_28_days"] == {"clicks": 2, "impressions": 33, "ctr": 0.0606, "position": 8.4}
    assert snap["by_host"]["main"]["impressions"] == 30
    assert snap["by_host"]["studio"]["clicks"] == 0
    assert snap["top_queries"][0]["query"] == "tiffany wood"
    assert snap["sitemaps"][0]["submitted"] == 34
    assert snap["index"]["sitemap_url_count"] == 3
    assert snap["index"]["indexed_count"] == 2
    assert snap["index"]["not_indexed"][0]["url"].endswith("/thin")
    # Every analytics query stops at the data lag, never at today.
    assert all(b["endDate"] == "2026-09-02" for b in client.bodies)
    json.dumps(snap)  # serialisable


def test_index_coverage_survives_one_failing_inspection():
    class Flaky(FakeClient):
        def inspect(self, url):
            if url.endswith("/about"):
                raise RuntimeError("quota")
            return super().inspect(url)

    cov = gsc.index_coverage(Flaky(), ["https://tiffanywoodyoga.com/", "https://tiffanywoodyoga.com/about"])
    assert cov["indexed_count"] == 1
    assert cov["not_indexed"][0]["verdict"] == "ERROR"


def test_write_snapshot_writes_day_file_and_latest(tmp_path):
    out = tmp_path / "search_console"
    path = gsc.write_snapshot({"status": "ok"}, out_dir=out, latest_path=out / "latest.json", day=date(2026, 9, 4))
    assert path == out / "2026-09-04.json"
    assert json.loads((out / "latest.json").read_text()) == {"status": "ok"}


def test_credentials_from_env_requires_a_credential_file(monkeypatch):
    monkeypatch.delenv("GSC_SERVICE_ACCOUNT_FILE", raising=False)
    monkeypatch.delenv("GSC_OAUTH_TOKEN_FILE", raising=False)
    try:
        gsc.credentials_from_env()
    except RuntimeError as exc:
        assert "GSC_OAUTH_TOKEN_FILE" in str(exc)
    else:
        raise AssertionError("expected RuntimeError")


def test_credentials_from_env_reports_missing_token_file(monkeypatch, tmp_path):
    monkeypatch.setenv("GSC_OAUTH_TOKEN_FILE", str(tmp_path / "nope.json"))
    try:
        gsc.credentials_from_env()
    except RuntimeError as exc:
        assert "does not exist" in str(exc)
    else:
        raise AssertionError("expected RuntimeError")
