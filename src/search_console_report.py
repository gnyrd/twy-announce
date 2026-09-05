"""Daily Google Search Console snapshot for the stats dashboard.

Reads the domain property through the Search Console API with a service
account, and writes one JSON file per day under data/search_console/ plus
latest.json. Nothing is posted; the stats /website page reads latest.json.

Usage:
    python3 src/search_console_report.py            # collect and write
    python3 src/search_console_report.py --dry-run  # collect, print, no write

Credentials live in twy-secrets. Either an OAuth user token
(GSC_OAUTH_TOKEN_FILE, minted 2026-09-04 as admin@tiffanywoodyoga.com
against the twy-search-console project's desktop client; the org policy
iam.disableServiceAccountKeyCreation blocks service-account keys) or a
service account key (GSC_SERVICE_ACCOUNT_FILE) whose email has been added
as a user on the property.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Protocol
from urllib.parse import urlparse
from xml.etree import ElementTree
from zoneinfo import ZoneInfo

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, "/root/twy/paths")

from twy_paths import load_env, search_console_dir, search_console_latest_path

MT = ZoneInfo("America/Denver")
PROPERTY = "sc-domain:tiffanywoodyoga.com"
SITEMAP_URL = "https://tiffanywoodyoga.com/sitemap.xml"
# Search Console data lags about two days behind the calendar.
DATA_LAG_DAYS = 2
DAILY_WINDOW_DAYS = 90
TOP_WINDOW_DAYS = 28
TOP_LIMIT = 20
SCOPES = ["https://www.googleapis.com/auth/webmasters.readonly"]
HOST_LABELS = {
    "tiffanywoodyoga.com": "main",
    "www.tiffanywoodyoga.com": "main",
    "studio.tiffanywoodyoga.com": "studio",
    "habit.tiffanywoodyoga.com": "habit",
}


class Client(Protocol):
    def query(self, body: dict[str, Any]) -> dict[str, Any]: ...
    def sitemaps(self) -> list[dict[str, Any]]: ...
    def inspect(self, url: str) -> dict[str, Any]: ...


class ApiClient:
    """Thin wrapper over googleapiclient so the collector can be tested with a fake."""

    def __init__(self, credentials, property_url: str = PROPERTY):
        from googleapiclient.discovery import build

        self._service = build("searchconsole", "v1", credentials=credentials, cache_discovery=False)
        self._property = property_url

    def query(self, body: dict[str, Any]) -> dict[str, Any]:
        return self._service.searchanalytics().query(siteUrl=self._property, body=body).execute()

    def sitemaps(self) -> list[dict[str, Any]]:
        return self._service.sitemaps().list(siteUrl=self._property).execute().get("sitemap") or []

    def inspect(self, url: str) -> dict[str, Any]:
        return (
            self._service.urlInspection()
            .index()
            .inspect(body={"inspectionUrl": url, "siteUrl": self._property})
            .execute()
        )


def credentials_from_env():
    token_file = os.getenv("GSC_OAUTH_TOKEN_FILE", "").strip()
    key_file = os.getenv("GSC_SERVICE_ACCOUNT_FILE", "").strip()
    if token_file:
        if not Path(token_file).exists():
            raise RuntimeError(f"GSC_OAUTH_TOKEN_FILE does not exist: {token_file}")
        from google.oauth2.credentials import Credentials

        return Credentials.from_authorized_user_file(token_file, scopes=SCOPES)
    if key_file:
        if not Path(key_file).exists():
            raise RuntimeError(f"GSC_SERVICE_ACCOUNT_FILE does not exist: {key_file}")
        from google.oauth2 import service_account

        return service_account.Credentials.from_service_account_file(key_file, scopes=SCOPES)
    raise RuntimeError("Neither GSC_OAUTH_TOKEN_FILE nor GSC_SERVICE_ACCOUNT_FILE is set")


def client_from_env() -> Client:
    return ApiClient(credentials_from_env())


def _row(row: dict[str, Any], keys: list[str]) -> dict[str, Any]:
    out = dict(zip(keys, row.get("keys") or []))
    out.update(
        {
            "clicks": int(row.get("clicks") or 0),
            "impressions": int(row.get("impressions") or 0),
            "ctr": round(float(row.get("ctr") or 0), 4),
            "position": round(float(row.get("position") or 0), 1),
        }
    )
    return out


def _totals(rows: list[dict[str, Any]]) -> dict[str, Any]:
    clicks = sum(r["clicks"] for r in rows)
    impressions = sum(r["impressions"] for r in rows)
    positioned = [r for r in rows if r.get("position") is not None and r["impressions"]]
    weighted = sum(r["position"] * r["impressions"] for r in positioned)
    weight = sum(r["impressions"] for r in positioned)
    return {
        "clicks": clicks,
        "impressions": impressions,
        "ctr": round(clicks / impressions, 4) if impressions else 0.0,
        "position": round(weighted / weight, 1) if weight else None,
    }


def daily_series(client: Client, start: date, end: date) -> list[dict[str, Any]]:
    payload = client.query(
        {
            "startDate": start.isoformat(),
            "endDate": end.isoformat(),
            "dimensions": ["date"],
            "rowLimit": 1000,
        }
    )
    rows = [_row(r, ["date"]) for r in payload.get("rows") or []]
    by_date = {r["date"]: r for r in rows}
    series = []
    day = start
    while day <= end:
        series.append(
            by_date.get(
                day.isoformat(),
                {"date": day.isoformat(), "clicks": 0, "impressions": 0, "ctr": 0.0, "position": None},
            )
        )
        day += timedelta(days=1)
    return series


def top_rows(client: Client, dimension: str, start: date, end: date) -> list[dict[str, Any]]:
    payload = client.query(
        {
            "startDate": start.isoformat(),
            "endDate": end.isoformat(),
            "dimensions": [dimension],
            "rowLimit": TOP_LIMIT,
        }
    )
    return [_row(r, [dimension]) for r in payload.get("rows") or []]


def by_host(client: Client, start: date, end: date) -> dict[str, dict[str, Any]]:
    payload = client.query(
        {
            "startDate": start.isoformat(),
            "endDate": end.isoformat(),
            "dimensions": ["page"],
            "rowLimit": 5000,
        }
    )
    groups: dict[str, list[dict[str, Any]]] = {}
    for raw in payload.get("rows") or []:
        row = _row(raw, ["page"])
        host = urlparse(row["page"]).netloc.lower()
        groups.setdefault(HOST_LABELS.get(host, host), []).append(row)
    return {label: _totals(rows) for label, rows in groups.items()}


def sitemap_urls(fetch: Callable[[str], str], sitemap_url: str = SITEMAP_URL) -> list[str]:
    root = ElementTree.fromstring(fetch(sitemap_url))
    urls = []
    for element in root.iter():
        if element.tag.endswith("}loc") or element.tag == "loc":
            if element.text:
                urls.append(element.text.strip())
    return urls


def index_coverage(client: Client, urls: list[str]) -> dict[str, Any]:
    """One URL Inspection per sitemap URL: is Google holding it in its index?"""
    pages = []
    for url in urls:
        try:
            result = (client.inspect(url).get("inspectionResult") or {}).get("indexStatusResult") or {}
            pages.append(
                {
                    "url": url,
                    "verdict": result.get("verdict"),
                    "coverage_state": result.get("coverageState"),
                    "last_crawl": result.get("lastCrawlTime"),
                    "indexed": result.get("verdict") == "PASS",
                }
            )
        except Exception as exc:  # noqa: BLE001 - one bad URL must not sink the report
            pages.append({"url": url, "verdict": "ERROR", "coverage_state": str(exc)[:200], "last_crawl": None, "indexed": False})
    indexed = sum(1 for p in pages if p["indexed"])
    return {
        "sitemap_url_count": len(urls),
        "indexed_count": indexed,
        "not_indexed": [p for p in pages if not p["indexed"]],
        "pages": pages,
    }


def collect(client: Client, *, today: date, fetch: Callable[[str], str], captured_at: datetime) -> dict[str, Any]:
    end = today - timedelta(days=DATA_LAG_DAYS)
    daily_start = end - timedelta(days=DAILY_WINDOW_DAYS - 1)
    top_start = end - timedelta(days=TOP_WINDOW_DAYS - 1)
    daily = daily_series(client, daily_start, end)
    last_28 = [r for r in daily if r["date"] >= top_start.isoformat()]
    previous_28 = [r for r in daily if (top_start - timedelta(days=TOP_WINDOW_DAYS)).isoformat() <= r["date"] < top_start.isoformat()]
    snapshot: dict[str, Any] = {
        "status": "ok",
        "captured_at": captured_at.isoformat(),
        "property": PROPERTY,
        "data_through": end.isoformat(),
        "window_days": TOP_WINDOW_DAYS,
        "last_28_days": _totals(last_28),
        "previous_28_days": _totals(previous_28),
        "daily": daily,
        "by_host": by_host(client, top_start, end),
        "top_queries": top_rows(client, "query", top_start, end),
        "top_pages": top_rows(client, "page", top_start, end),
    }
    try:
        snapshot["sitemaps"] = [
            {
                "path": s.get("path"),
                "last_submitted": s.get("lastSubmitted"),
                "last_downloaded": s.get("lastDownloaded"),
                "is_pending": s.get("isPending"),
                "errors": int(s.get("errors") or 0),
                "warnings": int(s.get("warnings") or 0),
                "submitted": sum(int(c.get("submitted") or 0) for c in s.get("contents") or []),
                "indexed": sum(int(c.get("indexed") or 0) for c in s.get("contents") or []),
            }
            for s in client.sitemaps()
        ]
    except Exception as exc:  # noqa: BLE001
        snapshot["sitemaps"] = []
        snapshot["sitemaps_error"] = str(exc)[:200]
    try:
        snapshot["index"] = index_coverage(client, sitemap_urls(fetch))
    except Exception as exc:  # noqa: BLE001
        snapshot["index"] = None
        snapshot["index_error"] = str(exc)[:200]
    return snapshot


def write_snapshot(snapshot: dict[str, Any], *, out_dir: Path, latest_path: Path, day: date) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{day.isoformat()}.json"
    text = json.dumps(snapshot, indent=2, sort_keys=True)
    path.write_text(text)
    latest_path.write_text(text)
    return path


def _fetch(url: str) -> str:
    response = requests.get(url, timeout=30)
    response.raise_for_status()
    return response.text


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="TWY daily Google Search Console snapshot")
    parser.add_argument("--dry-run", action="store_true", help="Collect and print, do not write")
    args = parser.parse_args(argv)
    load_env()
    now = datetime.now(MT)
    try:
        client = client_from_env()
        snapshot = collect(client, today=now.date(), fetch=_fetch, captured_at=now)
    except Exception as exc:  # noqa: BLE001 - the page shows the failure instead of stale silence
        snapshot = {"status": "error", "captured_at": now.isoformat(), "property": PROPERTY, "error": str(exc)[:300]}
    if args.dry_run:
        print(json.dumps(snapshot, indent=2, sort_keys=True))
        return 0 if snapshot["status"] == "ok" else 1
    path = write_snapshot(snapshot, out_dir=search_console_dir(), latest_path=search_console_latest_path(), day=now.date())
    print(f"wrote {path} ({snapshot['status']})")
    return 0 if snapshot["status"] == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
