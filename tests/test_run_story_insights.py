"""run_story_insights: capture stories before they expire, never lose a row."""

import importlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


@pytest.fixture
def collector(tmp_path, monkeypatch):
    module = importlib.import_module("run_story_insights")
    monkeypatch.setattr(
        module, "story_insights_path",
        lambda year, month: tmp_path / f"{year:04d}-{month:02d}.json",
    )
    return module, tmp_path


NOW = datetime(2026, 8, 19, 18, 0, tzinfo=timezone.utc)


def story(sid="111", stamp="2026-08-19T12:02:35+0000", **metrics):
    return {"id": sid, "media_type": "VIDEO", "timestamp": stamp,
            "permalink": f"https://example.invalid/s/{sid}",
            "metrics": metrics or {"reach": 11, "views": 17}}


def stored(tmp_path, period="2026-08"):
    return json.loads((tmp_path / f"{period}.json").read_text())["stories"]


def test_a_story_is_captured_with_its_metrics(collector):
    module, tmp = collector
    tally = module.capture([story()], now=NOW)
    assert tally["new"] == 1
    row = stored(tmp)["111"]
    assert row["metrics"] == {"reach": 11, "views": 17}
    assert row["first_seen"] == row["last_seen"] == NOW.isoformat()


def test_a_second_reading_updates_in_place_and_keeps_first_seen(collector):
    module, tmp = collector
    module.capture([story(reach=11)], now=NOW)
    later = NOW.replace(hour=22)
    tally = module.capture([story(reach=40, views=61)], now=later)
    assert tally == {"seen": 1, "new": 0, "updated": 1, "unchanged": 0,
                     "periods": ["2026-08"]}
    row = stored(tmp)["111"]
    assert row["metrics"] == {"reach": 40, "views": 61}
    assert row["first_seen"] == NOW.isoformat()
    assert row["last_seen"] == later.isoformat()


def test_a_story_with_no_metrics_is_still_written(collector):
    """The id cannot be recovered after expiry. Keep the row, fill it later."""
    module, tmp = collector
    module.capture([{"id": "222", "timestamp": "2026-08-19T09:00:00+0000",
                     "metrics": {}}], now=NOW)
    assert "222" in stored(tmp)
    module.capture([story(sid="222", stamp="2026-08-19T09:00:00+0000", reach=5)],
                   now=NOW)
    assert stored(tmp)["222"]["metrics"] == {"reach": 5}


def test_an_unchanged_reading_reports_unchanged(collector):
    module, _ = collector
    module.capture([story()], now=NOW)
    tally = module.capture([story()], now=NOW.replace(hour=23))
    assert tally["unchanged"] == 1 and tally["updated"] == 0


def test_a_story_lands_in_the_month_it_was_POSTED_not_read(collector):
    """A 23:50 story read after midnight must not appear in two files."""
    module, tmp = collector
    posted = story(stamp="2026-08-31T23:50:00+0000")
    module.capture([posted], now=datetime(2026, 9, 1, 0, 30, tzinfo=timezone.utc))
    assert (tmp / "2026-08.json").exists()
    assert not (tmp / "2026-09.json").exists()


def test_a_dry_run_writes_nothing(collector):
    module, tmp = collector
    module.capture([story()], now=NOW, write=False)
    assert list(tmp.glob("*.json")) == []


def test_a_story_with_no_id_is_skipped(collector):
    module, tmp = collector
    module.capture([{"timestamp": "2026-08-19T12:00:00+0000"}], now=NOW)
    assert not (tmp / "2026-08.json").exists()


def test_an_unreadable_store_does_not_lose_the_new_reading(collector):
    module, tmp = collector
    (tmp / "2026-08.json").write_text("{ this is not json")
    module.capture([story()], now=NOW)
    assert "111" in stored(tmp)


def test_a_bad_timestamp_falls_back_to_now(collector):
    module, tmp = collector
    module.capture([story(stamp="not-a-date")], now=NOW)
    assert (tmp / "2026-08.json").exists()
