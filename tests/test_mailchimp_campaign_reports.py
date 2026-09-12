import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from mailchimp_campaign_reports import build_document, fetch_sent_campaigns, summarise


def test_fetch_walks_every_page_oldest_first():
    calls = []

    def fake(params):
        calls.append(params["offset"])
        page = [{"id": f"c{params['offset']}"}] if params["offset"] < 2 else []
        return {"total_items": 2, "campaigns": page}

    campaigns = fetch_sent_campaigns(fake)
    assert [c["id"] for c in campaigns] == ["c0", "c1"]
    assert calls == [0, 1]


def test_summary_turns_rates_into_percent_and_keeps_the_send():
    campaign = {
        "id": "abc", "send_time": "2026-05-01T15:49:00+00:00", "emails_sent": 909,
        "settings": {"title": "2026-05 Non-Lifestyle Yoga Habit", "subject_line": "Free class"},
        "recipients": {"list_name": "TWY"},
        "report_summary": {"unique_opens": 371, "open_rate": 0.4125, "subscriber_clicks": 4, "click_rate": 0.0044},
    }
    row = summarise(campaign)
    assert row["open_rate"] == 41.25 and row["click_rate"] == 0.44
    assert row["emails_sent"] == 909 and row["send_time"].startswith("2026-05-01")
    document = build_document([campaign], datetime(2026, 9, 12, 13, tzinfo=timezone.utc))
    assert document["since"] == "2026-04-01" and document["campaigns"][0]["id"] == "abc"
