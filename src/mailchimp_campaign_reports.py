#!/usr/bin/env python3
"""Snapshot Mailchimp's sent-campaign report summaries to one file.

Mailchimp retired on 2026-08-19, but the newsletters chart on the stats site
runs back to April 1 (JP, 2026-09-12), and the April to July 2026 mailings
went out from Mailchimp. This reads every campaign sent since 2026-04-01 with
its report_summary (unique opens, open rate, click rate) and writes
mailchimp_campaign_reports.json under the newsletters data dir, where the
stats app reads it through twy_paths. Run once; run again only to refresh.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import requests
from twy_paths import load_env, mailchimp_campaign_reports_path

SINCE = "2026-04-01T00:00:00+00:00"
FIELDS = (
    "total_items,campaigns.id,campaigns.send_time,campaigns.emails_sent,"
    "campaigns.settings.title,campaigns.settings.subject_line,"
    "campaigns.report_summary,campaigns.recipients.list_name,"
    "campaigns.recipients.recipient_count"
)


def fetch_sent_campaigns(get, since: str = SINCE) -> list[dict]:
    """Every sent campaign since the date, oldest first, across pages."""
    campaigns: list[dict] = []
    offset = 0
    while True:
        payload = get({
            "status": "sent", "since_send_time": since, "count": 200, "offset": offset,
            "sort_field": "send_time", "sort_dir": "ASC", "fields": FIELDS,
        })
        page = payload.get("campaigns") or []
        campaigns.extend(page)
        offset += len(page)
        if not page or offset >= int(payload.get("total_items") or 0):
            return campaigns


def summarise(campaign: dict) -> dict:
    summary = campaign.get("report_summary") or {}
    settings = campaign.get("settings") or {}
    recipients = campaign.get("recipients") or {}
    return {
        "id": campaign.get("id"),
        "title": settings.get("title"),
        "subject": settings.get("subject_line"),
        "list_name": recipients.get("list_name"),
        "send_time": campaign.get("send_time"),
        "emails_sent": int(campaign.get("emails_sent") or 0),
        "unique_opens": summary.get("unique_opens"),
        "open_rate": round(float(summary.get("open_rate") or 0) * 100, 2),
        "subscriber_clicks": summary.get("subscriber_clicks"),
        "click_rate": round(float(summary.get("click_rate") or 0) * 100, 2),
    }


def build_document(campaigns: list[dict], now: datetime) -> dict:
    return {
        "version": 1,
        "source": "Mailchimp API /campaigns report_summary",
        "captured_at": now.astimezone(timezone.utc).isoformat(),
        "since": SINCE[:10],
        "campaigns": [summarise(campaign) for campaign in campaigns],
    }


def write_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as handle:
            json.dump(payload, handle, indent=2)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def api_get():
    key = os.environ["MAILCHIMP_API_KEY"]
    prefix = os.getenv("MAILCHIMP_SERVER_PREFIX") or key.rsplit("-", 1)[-1]

    def get(params: dict) -> dict:
        response = requests.get(
            f"https://{prefix}.api.mailchimp.com/3.0/campaigns",
            auth=("anystring", key), params=params, timeout=60,
        )
        response.raise_for_status()
        return response.json()

    return get


def main() -> int:
    load_env()
    campaigns = fetch_sent_campaigns(api_get())
    path = mailchimp_campaign_reports_path()
    write_atomic(path, build_document(campaigns, datetime.now(timezone.utc)))
    print(f"wrote {len(campaigns)} campaigns to {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
