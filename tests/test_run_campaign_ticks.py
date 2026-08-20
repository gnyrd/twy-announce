"""Tests for the daily campaign tick's launcher factory.

_build_launcher_factory wires the live campaign launcher for a real run. Two
seams are under test here: a section email must draw RESOLVED copy (tokens
like {CLASS_TITLE} filled in), never the raw newsletter draft, and the
factory must seed the month's recording draft the same way run_sendgrid_
mailings.py does, so the campaign path is self-sufficient once the old
newsletter workflow retires.

No test here reaches the network: SendGridAPI, SendGridRegistry and
recording_ready are all stubbed. read_local_sections, resolve_section_tokens
and ensure_recording_draft are the real, file-based, stateless functions
from sendgrid_newsletter_workflow, exercised against a TWY_DATA_DIR pointed
at tmp_path.
"""
import json
import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(__file__))

import campaign_launch as campaign_launch_module
import provision_recording_product as provision_recording_product_module
import run_campaign_ticks as runner
import sendgrid_api as sendgrid_api_module
import sendgrid_campaigns as sendgrid_campaigns_module
import sendgrid_newsletter_workflow as workflow_module
from sendgrid_newsletter_workflow import ensure_recording_draft
from test_campaign_launch import FakeAPI, FakeRegistry, SEGMENT_ID


# Far enough in the future that CampaignLauncher's default now_fn (real UTC
# now) always sees these scheduled sends as ahead of it.
YEAR, MONTH = 2030, 1


class KeyedFakeAPI(FakeAPI):
    """FakeAPI takes no constructor args; SendGridAPI(key) passes one."""

    def __init__(self, api_key=None):
        super().__init__()
        self.api_key = api_key


class LoadableFakeRegistry(FakeRegistry):
    @classmethod
    def load(cls, path):
        return cls()


def _campaign(emails, *, journey_id="yoga_habit"):
    return {
        "type": "campaign",
        "journey_id": journey_id,
        "name": "Yoga Habit",
        "campaign_month": f"{YEAR:04d}_{MONTH:02d}",
        "segment_id": SEGMENT_ID,
        "emails": emails,
    }


def _patch_provider(monkeypatch):
    """Stub every provider- and network-touching seam the factory reaches.

    Leaves read_local_sections, resolve_section_tokens and
    ensure_recording_draft real: they are pure, local, file-based functions
    and the whole point of these tests is to exercise them for real.
    """
    monkeypatch.setattr(sendgrid_api_module, "SendGridAPI", KeyedFakeAPI)
    monkeypatch.setattr(
        sendgrid_campaigns_module, "SendGridRegistry", LoadableFakeRegistry
    )
    monkeypatch.setattr(
        provision_recording_product_module,
        "recording_ready",
        lambda year, month: False,
    )


def _write_recording_record(tmp_path, **overrides):
    from twy_paths import habit_recording_state_path

    payload = {"class_title": "Open to Grace", "product_id": 95861}
    payload.update(overrides)
    path = habit_recording_state_path(YEAR, MONTH)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return payload


def _spy_launcher(monkeypatch):
    """Capture the sections kwarg a CampaignLauncher is built with, while
    delegating to the real class so the fail-closed guard in _email_content
    still runs for real (end to end, not mocked out)."""
    real_cls = campaign_launch_module.CampaignLauncher
    captured = {}

    class RecordingCampaignLauncher(real_cls):
        def __init__(self, **kwargs):
            captured["sections"] = kwargs.get("sections")
            super().__init__(**kwargs)

    monkeypatch.setattr(
        campaign_launch_module, "CampaignLauncher", RecordingCampaignLauncher
    )
    return captured


def _no_live_class_date(monkeypatch):
    """Both class-date resolvers stubbed to None so a test that does not care
    about the class-date gate never makes a live classes-API request."""
    monkeypatch.setattr(runner, "real_habit_class_date", lambda year, month: None)
    monkeypatch.setattr(runner, "get_habit_class_date", lambda year, month: None)


def test_section_email_copy_reaches_the_launcher_resolved(monkeypatch, tmp_path):
    monkeypatch.setenv("SENDGRID_API_KEY", "test-key")
    monkeypatch.setenv("TWY_DATA_DIR", str(tmp_path / "data"))
    _patch_provider(monkeypatch)
    _no_live_class_date(monkeypatch)
    captured = _spy_launcher(monkeypatch)

    # Seed the recording draft template (carries {CLASS_TITLE} and
    # {RECORDING_CTA}) plus a recording record fixture so the resolver has a
    # fact to substitute the tokens with.
    assert ensure_recording_draft(YEAR, MONTH) is True
    _write_recording_record(tmp_path)

    pinned = _campaign(
        [{"section": "recording", "subject": "STATIC", "body": "STATIC"}]
    )
    launch_one = runner._build_launcher_factory()
    result = launch_one(pinned, YEAR, MONTH)

    resolved = captured["sections"]["recording"]
    combined = "\n".join(
        [resolved["subject"], resolved["preheader"], resolved["body"]]
    )
    assert "{" not in combined
    assert "Open to Grace" in resolved["subject"]
    pending = [row for row in result["sends"] if row.get("content_pending")]
    assert pending == []
    assert len(result["sends"]) == 1
    assert result["sends"][0]["skipped"] is False


def test_section_email_with_no_recording_record_holds_the_email(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("SENDGRID_API_KEY", "test-key")
    monkeypatch.setenv("TWY_DATA_DIR", str(tmp_path / "data"))
    _patch_provider(monkeypatch)
    _no_live_class_date(monkeypatch)
    captured = _spy_launcher(monkeypatch)

    # Seed only the draft template. With no recording record, the resolver
    # has nothing to substitute the tokens with, so they must survive
    # untouched and the launcher's fail-closed guard must hold the email.
    assert ensure_recording_draft(YEAR, MONTH) is True

    pinned = _campaign(
        [{"section": "recording", "subject": "STATIC", "body": "STATIC"}]
    )
    launch_one = runner._build_launcher_factory()
    result = launch_one(pinned, YEAR, MONTH)

    resolved = captured["sections"]["recording"]
    combined = "\n".join(
        [resolved["subject"], resolved["preheader"], resolved["body"]]
    )
    assert "{CLASS_TITLE}" in combined or "{RECORDING_CTA}" in combined
    pending = [row for row in result["sends"] if row.get("content_pending")]
    assert len(pending) == 1
    assert pending[0]["skipped"] is True
    assert len(result["sends"]) == 1


def test_factory_seeds_the_recording_draft_when_the_month_has_a_class_date(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("SENDGRID_API_KEY", "test-key")
    monkeypatch.setenv("TWY_DATA_DIR", str(tmp_path / "data"))
    _patch_provider(monkeypatch)
    monkeypatch.setattr(
        runner, "real_habit_class_date", lambda year, month: date(YEAR, MONTH, 10)
    )
    monkeypatch.setattr(runner, "get_habit_class_date", lambda year, month: None)
    calls = []
    monkeypatch.setattr(
        workflow_module,
        "ensure_recording_draft",
        lambda year, month: calls.append((year, month)),
    )

    pinned = _campaign([{"subject": "S", "preheader": "P", "body": "B"}])
    launch_one = runner._build_launcher_factory()
    launch_one(pinned, YEAR, MONTH)

    assert calls == [(YEAR, MONTH)]


def test_factory_does_not_seed_the_recording_draft_with_no_class_date(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("SENDGRID_API_KEY", "test-key")
    monkeypatch.setenv("TWY_DATA_DIR", str(tmp_path / "data"))
    _patch_provider(monkeypatch)
    _no_live_class_date(monkeypatch)
    calls = []
    monkeypatch.setattr(
        workflow_module,
        "ensure_recording_draft",
        lambda year, month: calls.append((year, month)),
    )

    pinned = _campaign([{"subject": "S", "preheader": "P", "body": "B"}])
    launch_one = runner._build_launcher_factory()
    launch_one(pinned, YEAR, MONTH)

    assert calls == []


def test_factory_is_fail_soft_when_class_date_resolution_raises(
    monkeypatch, tmp_path
):
    """A classes-API hiccup resolving this month's real Habit class date must
    hold the recording seed, never take down the whole journey's launch this
    tick: read_local_sections still runs and the launcher still builds and
    sends whatever it can. real_habit_class_date already swallows
    requests.RequestException internally (API unreachable); this covers the
    residual case where it raises something else entirely (a malformed
    response), which the network-only guard does not catch."""
    monkeypatch.setenv("SENDGRID_API_KEY", "test-key")
    monkeypatch.setenv("TWY_DATA_DIR", str(tmp_path / "data"))
    _patch_provider(monkeypatch)

    def _raises(year, month):
        raise RuntimeError("classes API is on fire")

    monkeypatch.setattr(runner, "real_habit_class_date", _raises)
    monkeypatch.setattr(runner, "get_habit_class_date", lambda year, month: None)
    calls = []
    monkeypatch.setattr(
        workflow_module,
        "ensure_recording_draft",
        lambda year, month: calls.append((year, month)),
    )

    pinned = _campaign([{"subject": "S", "preheader": "P", "body": "B"}])
    launch_one = runner._build_launcher_factory()
    # Must not raise: this journey's launch still completes this tick.
    result = launch_one(pinned, YEAR, MONTH)

    assert calls == []
    assert len(result["sends"]) == 1
    assert result["sends"][0]["skipped"] is False
