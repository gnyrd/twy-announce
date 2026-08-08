import json
from datetime import date, datetime, timezone

import pytest

from sendgrid_campaigns import SendGridCampaigns, SendGridRegistry
from sendgrid_mailings import MailingPurpose, mailing_name
from sendgrid_newsletter_workflow import (
    apply_provider_report,
    lock_due_sections,
    mark_provider_error,
    provision_drafts,
    read_local_sections,
    sections_due_for_materialization,
)


class FakeAPI:
    def __init__(self):
        self.created_lists = []
        self.created_segments = []
        self.created_sends = []
        self.single_sends = {}
        self.segment_rows = {}

    def create_list(self, name):
        self.created_lists.append(name)
        return {"id": f"list{len(self.created_lists)}", "name": name}

    def marketing_lists(self):
        return []

    def create_segment(self, **payload):
        identifier = f"segment{len(self.created_segments) + 1}"
        item = {
            "id": identifier,
            **payload,
            "status": {"query_validation": "VALID"},
        }
        self.created_segments.append(payload)
        self.segment_rows[identifier] = item
        return item

    def segment(self, identifier):
        return self.segment_rows[identifier]

    def segments(self):
        return list(self.segment_rows.values())

    def create_single_send(self, payload):
        identifier = f"send{len(self.created_sends) + 1}"
        item = {
            "id": identifier,
            "name": payload["name"],
            "status": "draft",
            "send_to": payload["send_to"],
            "email_config": payload["email_config"],
        }
        self.created_sends.append(payload)
        self.single_sends[identifier] = item
        return item

    def get_single_send(self, identifier):
        return self.single_sends[identifier]

    def single_sends_by_name(self, name):
        return [
            item
            for item in self.single_sends.values()
            if item["name"] == name
        ]

    def delete_single_send(self, identifier):
        self.single_sends.pop(identifier, None)

    def unschedule_single_send(self, identifier):
        self.single_sends[identifier]["status"] = "draft"


def _registry(path):
    path.write_text(json.dumps({
        "account_email": "admin@tiffanywoodyoga.com",
        "sender": {
            "id": 9423402,
            "email": "hello@tiffanywoodyoga.com",
        },
        "suppression_group": {
            "id": 35187,
            "name": "Email: Unsubscribed",
        },
        "lists": {
            "Email: Subscribed": {"id": "subscribed1"},
            "Member: Yoga Lifestyle": {"id": "member1"},
        },
    }))
    return SendGridRegistry.load(path)


def _sections():
    return {
        "lifestyle": {
            "subject": "Monthly",
            "body": "Monthly body",
            "preheader": "A monthly practice note",
        },
        "non_lifestyle": {
            "subject": "Invitation",
            "body": "Invitation body",
            "preheader": "A free class invitation",
        },
        "non_opener": {
            "subject": "Again",
            "body": "Resend body",
            "preheader": "A second invitation",
        },
        "gentle_nudge": {
            "subject": "A note",
            "body": "Gentle body",
            "preheader": "A softer reminder",
        },
        "reminder": {
            "subject": "Tomorrow",
            "body": "Reminder body",
            "preheader": "What to bring tomorrow",
        },
        "recording": {
            "subject": "Recording",
            "body": "Recording body",
            "preheader": "Your class recording",
        },
        "ph1": {
            "subject": "Thank you",
            "body": "First follow up",
            "preheader": "A next step after class",
        },
        "ph2": {
            "subject": "One more",
            "body": "Second follow up",
            "preheader": "A final gentle invitation",
        },
    }


def test_provision_creates_locked_lists_segments_and_all_eight_drafts(
    tmp_path,
):
    api = FakeAPI()
    registry = _registry(tmp_path / "registry.json")
    campaigns = SendGridCampaigns(
        api=api,
        registry=registry,
        state_path=tmp_path / "state.json",
    )
    result = provision_drafts(
        campaigns=campaigns,
        year=2026,
        month=8,
        class_date=date(2026, 8, 8),
        sections=_sections(),
    )

    assert api.created_lists == [
        "Yoga Habit: Interested: 2026_08",
        "Yoga Habit: Registered: 2026_08",
    ]
    assert set(result) == set(_sections())
    assert len(api.created_sends) == 8
    assert len(api.created_segments) == 5
    assert {
        payload["name"] for payload in api.created_sends
    } == {
        mailing_name(2026, 8, purpose)
        for purpose in MailingPurpose
    }

    monthly = next(
        payload for payload in api.created_sends
        if payload["name"].endswith(": Monthly")
    )
    registered = next(
        payload for payload in api.created_sends
        if payload["name"].endswith(": Registered Reminder")
    )
    assert monthly["send_to"]["list_ids"] == ["member1"]
    registered_list_id = SendGridRegistry.load(
        registry.path
    ).list_id("Yoga Habit: Registered: 2026_08")
    assert registered["send_to"]["list_ids"] == [registered_list_id]


def test_partial_registered_reminder_does_not_require_general_draft(tmp_path):
    api = FakeAPI()
    registry = _registry(tmp_path / "registry.json")
    campaigns = SendGridCampaigns(
        api=api,
        registry=registry,
        state_path=tmp_path / "state.json",
    )
    result = provision_drafts(
        campaigns=campaigns,
        year=2026,
        month=8,
        class_date=date(2026, 8, 8),
        sections={
            "reminder": {
                "subject": "Tomorrow",
                "body": "Reminder body",
                "preheader": "What to bring tomorrow",
            },
        },
    )
    assert set(result) == {"reminder"}


def test_monthly_only_does_not_require_a_habit_class(tmp_path):
    api = FakeAPI()
    registry = _registry(tmp_path / "registry.json")
    campaigns = SendGridCampaigns(
        api=api,
        registry=registry,
        state_path=tmp_path / "state.json",
    )

    result = provision_drafts(
        campaigns=campaigns,
        year=2026,
        month=8,
        class_date=None,
        sections={
            "lifestyle": {
                "subject": "Monthly",
                "body": "Monthly body",
                "preheader": "A monthly practice note",
            }
        },
    )

    assert set(result) == {"lifestyle"}
    assert api.created_lists == []
    assert not api.created_segments


def test_provision_passes_section_preheader_to_draft_creation(tmp_path):
    api = FakeAPI()
    registry = _registry(tmp_path / "registry.json")
    campaigns = SendGridCampaigns(
        api=api,
        registry=registry,
        state_path=tmp_path / "state.json",
    )

    provision_drafts(
        campaigns=campaigns,
        year=2026,
        month=8,
        class_date=None,
        sections={
            "lifestyle": {
                "subject": "Monthly",
                "body": "Monthly body",
                "preheader": "A useful inbox preview",
            }
        },
    )

    html = api.created_sends[0]["email_config"]["html_content"]
    assert "A useful inbox preview" in html
    assert "display:none" in html


def test_provision_requires_preheader_before_provider_creation(tmp_path):
    api = FakeAPI()
    registry = _registry(tmp_path / "registry.json")
    campaigns = SendGridCampaigns(
        api=api,
        registry=registry,
        state_path=tmp_path / "state.json",
    )

    with pytest.raises(ValueError, match="preheader"):
        provision_drafts(
            campaigns=campaigns,
            year=2026,
            month=8,
            class_date=None,
            sections={
                "lifestyle": {
                    "subject": "Monthly",
                    "body": "Monthly body",
                    "preheader": "",
                }
            },
        )

    assert api.created_sends == []


def test_provision_rejects_preheader_that_repeats_subject(tmp_path):
    api = FakeAPI()
    registry = _registry(tmp_path / "registry.json")
    campaigns = SendGridCampaigns(
        api=api,
        registry=registry,
        state_path=tmp_path / "state.json",
    )

    with pytest.raises(ValueError, match="preheader"):
        provision_drafts(
            campaigns=campaigns,
            year=2026,
            month=8,
            class_date=None,
            sections={
                "lifestyle": {
                    "subject": "Monthly",
                    "body": "Monthly body",
                    "preheader": "Monthly",
                }
            },
        )

    assert api.created_sends == []


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ("# Duplicate heading\n\nBody.", "markdown heading"),
        ("Body with an em dash — here.", "prohibited punctuation"),
        ("Body with a semicolon; here.", "prohibited punctuation"),
        ("Join {CLASS_TITLE}.", "unresolved token"),
    ],
)
def test_provision_rejects_unsafe_final_copy_before_provider_creation(
    tmp_path,
    body,
    message,
):
    api = FakeAPI()
    registry = _registry(tmp_path / "registry.json")
    campaigns = SendGridCampaigns(
        api=api,
        registry=registry,
        state_path=tmp_path / "state.json",
    )

    with pytest.raises(ValueError, match=message):
        provision_drafts(
            campaigns=campaigns,
            year=2026,
            month=8,
            class_date=None,
            sections={
                "lifestyle": {
                    "subject": "Monthly",
                    "body": body,
                    "preheader": "A useful preview",
                }
            },
        )

    assert api.created_sends == []


def test_read_local_sections_loads_markdown_and_preheader_metadata(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setenv("TWY_DATA_DIR", str(tmp_path))
    period = tmp_path / "newsletters" / "2026-08"
    period.mkdir(parents=True)
    (period / "lifestyle.md").write_text(
        "# August subject\n\nOpening paragraph.\n\nSecond paragraph.",
        encoding="utf-8",
    )
    (period / ".metadata.json").write_text(
        json.dumps({
            "version": 1,
            "drafts": {
                "lifestyle": {
                    "preheader": "A useful inbox preview",
                },
            },
        }),
        encoding="utf-8",
    )

    sections = read_local_sections(2026, 8)

    assert sections == {
        "lifestyle": {
            "subject": "August subject",
            "body": "Opening paragraph.\n\nSecond paragraph.",
            "preheader": "A useful inbox preview",
        },
    }


def test_sections_due_for_materialization_waits_until_24_hour_window():
    sections = {
        "lifestyle": {
            "subject": "August subject",
            "body": "Opening paragraph.",
            "preheader": "A useful inbox preview",
        },
    }

    before = sections_due_for_materialization(
        year=2026,
        month=8,
        class_date=None,
        sections=sections,
        now=datetime(2026, 8, 2, 15, 48, tzinfo=timezone.utc),
    )
    at_window = sections_due_for_materialization(
        year=2026,
        month=8,
        class_date=None,
        sections=sections,
        now=datetime(2026, 8, 2, 15, 49, tzinfo=timezone.utc),
    )
    after_send_time = sections_due_for_materialization(
        year=2026,
        month=8,
        class_date=None,
        sections=sections,
        now=datetime(2026, 8, 3, 15, 49, tzinfo=timezone.utc),
    )

    assert before == {}
    assert at_window == sections
    assert after_send_time == {}


def _write_local_newsletter(
    root,
    *,
    state="draft",
    hold=False,
    subject="August subject",
    body="Opening paragraph.",
):
    period = root / "newsletters" / "2026-08"
    period.mkdir(parents=True, exist_ok=True)
    (period / "lifestyle.md").write_text(
        f"# {subject}\n\n{body}\n",
        encoding="utf-8",
    )
    (period / ".metadata.json").write_text(
        json.dumps({
            "version": 1,
            "drafts": {
                "lifestyle": {
                    "state": state,
                    "hold": hold,
                    "preheader": "A useful inbox preview",
                },
            },
        }),
        encoding="utf-8",
    )
    return period


def test_lock_due_sections_waits_until_materialization_window(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setenv("TWY_DATA_DIR", str(tmp_path))
    period = _write_local_newsletter(tmp_path)

    sections = lock_due_sections(
        year=2026,
        month=8,
        class_date=None,
        now=datetime(2026, 8, 2, 15, 48, tzinfo=timezone.utc),
    )

    assert sections == {}
    metadata = json.loads((period / ".metadata.json").read_text())
    assert metadata["drafts"]["lifestyle"]["state"] == "draft"
    assert not (period / "snapshots").exists()


def test_lock_due_sections_creates_immutable_snapshot_at_window(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setenv("TWY_DATA_DIR", str(tmp_path))
    period = _write_local_newsletter(tmp_path)

    sections = lock_due_sections(
        year=2026,
        month=8,
        class_date=None,
        now=datetime(2026, 8, 2, 15, 49, tzinfo=timezone.utc),
    )

    assert sections["lifestyle"]["subject"] == "August subject"
    metadata = json.loads((period / ".metadata.json").read_text())
    entry = metadata["drafts"]["lifestyle"]
    assert entry["state"] == "locked"
    assert entry["locked_at"] == "2026-08-02T15:49:00+00:00"
    assert entry["original_snapshot"]
    assert entry["generation_history"] == [entry["original_snapshot"]]
    snapshot = period / entry["locked_snapshot"]
    payload = json.loads(snapshot.read_text())
    assert payload["kind"] == "locked"
    assert payload["content"]["body"] == "Opening paragraph."

    (period / "lifestyle.md").write_text(
        "# Changed later\n\nThis must not be scheduled.\n",
        encoding="utf-8",
    )
    resumed = lock_due_sections(
        year=2026,
        month=8,
        class_date=None,
        now=datetime(2026, 8, 2, 15, 55, tzinfo=timezone.utc),
    )
    assert resumed == sections
    assert json.loads(snapshot.read_text()) == payload


def test_lock_due_sections_ignores_legacy_hold_and_sends_current_draft(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setenv("TWY_DATA_DIR", str(tmp_path))
    period = _write_local_newsletter(tmp_path, hold=True)

    sections = lock_due_sections(
        year=2026,
        month=8,
        class_date=None,
        now=datetime(2026, 8, 2, 15, 49, tzinfo=timezone.utc),
    )

    assert sections["lifestyle"]["subject"] == "August subject"
    metadata = json.loads((period / ".metadata.json").read_text())
    assert metadata["drafts"]["lifestyle"]["state"] == "locked"


def test_lock_due_sections_uses_tiff_approved_snapshot(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setenv("TWY_DATA_DIR", str(tmp_path))
    period = _write_local_newsletter(
        tmp_path,
        subject="Changed after approval",
        body="This must not replace Tiff's approval.",
    )
    approved_path = (
        period / "snapshots" / "lifestyle" / "approved-example.json"
    )
    approved_path.parent.mkdir(parents=True)
    approved_path.write_text(json.dumps({
        "version": 1,
        "kind": "approved",
        "audience": "lifestyle",
        "captured_at": "20260729T120000Z",
        "content_sha256": "unused-by-reader",
        "content": {
            "subject": "Tiff approved subject",
            "preheader": "Tiff approved preheader",
            "body": "Tiff approved body.",
        },
    }))
    metadata = json.loads((period / ".metadata.json").read_text())
    metadata["drafts"]["lifestyle"].update({
        "approved_at": "20260729T120000Z",
        "approved_snapshot": str(
            approved_path.relative_to(period)
        ),
        "edited_at": "20260729T120000Z",
    })
    (period / ".metadata.json").write_text(json.dumps(metadata))

    sections = lock_due_sections(
        year=2026,
        month=8,
        class_date=None,
        now=datetime(2026, 8, 2, 15, 49, tzinfo=timezone.utc),
    )

    assert sections["lifestyle"] == {
        "subject": "Tiff approved subject",
        "preheader": "Tiff approved preheader",
        "body": "Tiff approved body.",
    }
    metadata = json.loads((period / ".metadata.json").read_text())
    locked_path = period / metadata["drafts"]["lifestyle"]["locked_snapshot"]
    locked = json.loads(locked_path.read_text())
    assert locked["content"]["subject"] == "Tiff approved subject"


def test_apply_provider_report_tracks_schedule_and_immutable_sent_snapshot(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setenv("TWY_DATA_DIR", str(tmp_path))
    period = _write_local_newsletter(tmp_path)
    lock_due_sections(
        year=2026,
        month=8,
        class_date=None,
        now=datetime(2026, 8, 2, 15, 49, tzinfo=timezone.utc),
    )

    apply_provider_report(
        year=2026,
        month=8,
        report={
            "Monthly": {
                "id": "send1",
                "status": "scheduled",
                "provider_status": "scheduled",
                "send_at": "2026-08-03T15:49:00+00:00",
            },
        },
        now=datetime(2026, 8, 2, 15, 50, tzinfo=timezone.utc),
    )
    metadata = json.loads((period / ".metadata.json").read_text())
    entry = metadata["drafts"]["lifestyle"]
    assert entry["state"] == "scheduled"
    assert entry["provider"]["single_send_id"] == "send1"

    apply_provider_report(
        year=2026,
        month=8,
        report={
            "Monthly": {
                "id": "send1",
                "status": "triggered",
                "provider_status": "triggered",
                "send_at": "2026-08-03T15:49:00+00:00",
            },
        },
        now=datetime(2026, 8, 3, 15, 50, tzinfo=timezone.utc),
    )
    metadata = json.loads((period / ".metadata.json").read_text())
    entry = metadata["drafts"]["lifestyle"]
    assert entry["state"] == "sent"
    sent_snapshot = period / entry["sent_snapshot"]
    sent = json.loads(sent_snapshot.read_text())
    assert sent["kind"] == "sent"
    assert sent["content"]["subject"] == "August subject"

    apply_provider_report(
        year=2026,
        month=8,
        report={
            "Monthly": {
                "id": "send1",
                "status": "triggered",
                "provider_status": "triggered",
                "send_at": "2026-08-03T15:49:00+00:00",
            },
        },
        now=datetime(2026, 8, 3, 16, 5, tzinfo=timezone.utc),
    )
    assert json.loads(sent_snapshot.read_text()) == sent


def test_sent_snapshot_creates_pending_review_from_original_tweee_draft(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setenv("TWY_DATA_DIR", str(tmp_path))
    period = _write_local_newsletter(
        tmp_path,
        subject="Tiff subject",
        body="Tiff body.",
    )
    original_path = (
        period / "snapshots" / "lifestyle" / "generated-original.json"
    )
    original_path.parent.mkdir(parents=True)
    original_path.write_text(json.dumps({
        "version": 1,
        "kind": "generated",
        "audience": "lifestyle",
        "captured_at": "20260729T010000Z",
        "content_sha256": "unused-by-reader",
        "content": {
            "subject": "Tweee subject",
            "preheader": "Tweee preview",
            "body": "Tweee body.",
        },
    }))
    metadata = json.loads((period / ".metadata.json").read_text())
    metadata["drafts"]["lifestyle"].update({
        "preheader": "Tiff preview",
        "original_snapshot": str(
            original_path.relative_to(period)
        ),
    })
    (period / ".metadata.json").write_text(json.dumps(metadata))
    lock_due_sections(
        year=2026,
        month=8,
        class_date=None,
        now=datetime(2026, 8, 2, 15, 49, tzinfo=timezone.utc),
    )

    apply_provider_report(
        year=2026,
        month=8,
        report={
            "Monthly": {
                "id": "send1",
                "status": "triggered",
                "provider_status": "triggered",
                "send_at": "2026-08-03T15:49:00+00:00",
            },
        },
        now=datetime(2026, 8, 3, 15, 50, tzinfo=timezone.utc),
    )

    reviews = list(
        (tmp_path / "newsletter-diffs" / "2026-08").glob(
            "*.review.json"
        )
    )
    assert len(reviews) == 1
    review = json.loads(reviews[0].read_text())
    assert review["audience_key"] == "lifestyle"
    assert review["generated"]["subject"] == "Tweee subject"
    assert review["sent"]["subject"] == "Tiff subject"
    assert "preheader" in {
        candidate["kind"] for candidate in review["candidates"]
    }


def test_provider_failure_moves_locked_draft_to_error(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setenv("TWY_DATA_DIR", str(tmp_path))
    period = _write_local_newsletter(tmp_path)
    lock_due_sections(
        year=2026,
        month=8,
        class_date=None,
        now=datetime(2026, 8, 2, 15, 49, tzinfo=timezone.utc),
    )

    mark_provider_error(
        year=2026,
        month=8,
        audiences={"lifestyle"},
        error="provider verification failed",
        now=datetime(2026, 8, 2, 15, 50, tzinfo=timezone.utc),
    )

    metadata = json.loads((period / ".metadata.json").read_text())
    entry = metadata["drafts"]["lifestyle"]
    assert entry["state"] == "error"
    assert entry["error"] == "provider verification failed"
