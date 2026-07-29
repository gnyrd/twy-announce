import json

import pytest

import sendgrid_newsletter_review
from sendgrid_newsletter_review import collect_review


class FakeSendGridAPI:
    def __init__(self, single_send, design=None):
        self.single_send = single_send
        self.design = design or {}
        self.calls = []

    def get_single_send(self, single_send_id):
        self.calls.append(("get_single_send", single_send_id))
        assert single_send_id == "sg-id"
        return self.single_send

    def get_design(self, design_id):
        self.calls.append(("get_design", design_id))
        assert design_id == "design-id"
        return self.design


def _generated_file(tmp_path):
    generated = tmp_path / "generated.md"
    generated.write_text(
        "# Generated subject\n\nGenerated body\n",
        encoding="utf-8",
    )
    return generated


def test_collect_review_requires_triggered_single_send(tmp_path):
    api = FakeSendGridAPI(single_send={"id": "sg-id", "status": "draft"})

    with pytest.raises(ValueError, match="must be triggered"):
        collect_review(
            api=api,
            single_send_id="sg-id",
            generated_path=_generated_file(tmp_path),
            mailing_name="2026_08: Yoga Habit: General Invitation",
            audience_key="non_lifestyle",
            captured_at="2026-08-04T10:15:00-06:00",
        )

    assert api.calls == [("get_single_send", "sg-id")]


def test_collect_review_reads_design_and_writes_provider_neutral_record(
    monkeypatch,
    tmp_path,
):
    diffs = tmp_path / "newsletter-diffs"
    monkeypatch.setattr(
        sendgrid_newsletter_review,
        "newsletter_diffs_dir",
        lambda: diffs,
    )
    monkeypatch.setattr(
        sendgrid_newsletter_review,
        "ensure_empty_review_is_done",
        lambda record: None,
    )
    api = FakeSendGridAPI(
        single_send={
            "id": "sg-id",
            "status": "triggered",
            "name": "2026_08: Yoga Habit: General Invitation",
            "send_at": "2026-08-04T16:00:00Z",
            "email_config": {"design_id": "design-id"},
        },
        design={
            "id": "design-id",
            "subject": "Tiff subject",
            "html_content": "<p>Tiff body</p>",
        },
    )

    path = collect_review(
        api=api,
        single_send_id="sg-id",
        generated_path=_generated_file(tmp_path),
        mailing_name="2026_08: Yoga Habit: General Invitation",
        audience_key="non_lifestyle",
        captured_at="2026-08-04T10:15:00-06:00",
    )

    record = json.loads(path.read_text(encoding="utf-8"))
    assert path.parent == diffs / "2026-08"
    assert record["provider"] == "sendgrid"
    assert record["provider_design_id"] == "design-id"
    assert record["generated"]["subject"] == "Generated subject"
    assert record["sent"]["subject"] == "Tiff subject"
    assert "Tiff body" in record["sent"]["body"]
    assert api.calls == [
        ("get_single_send", "sg-id"),
        ("get_design", "design-id"),
    ]


def test_collect_review_supports_api_created_inline_content(
    monkeypatch,
    tmp_path,
):
    diffs = tmp_path / "newsletter-diffs"
    monkeypatch.setattr(
        sendgrid_newsletter_review,
        "newsletter_diffs_dir",
        lambda: diffs,
    )
    monkeypatch.setattr(
        sendgrid_newsletter_review,
        "ensure_empty_review_is_done",
        lambda record: None,
    )
    api = FakeSendGridAPI(
        single_send={
            "id": "sg-id",
            "status": "triggered",
            "name": "2026_08: Yoga Habit: General Invitation",
            "send_at": "2026-08-04T16:00:00Z",
            "email_config": {
                "subject": "Tiff subject",
                "html_content": "<p>Inline Tiff body</p>",
            },
        }
    )

    path = collect_review(
        api=api,
        single_send_id="sg-id",
        generated_path=_generated_file(tmp_path),
        mailing_name="2026_08: Yoga Habit: General Invitation",
        audience_key="non_lifestyle",
        captured_at="2026-08-04T10:15:00-06:00",
    )

    record = json.loads(path.read_text(encoding="utf-8"))
    assert record["provider_design_id"] is None
    assert record["sent"]["subject"] == "Tiff subject"
    assert "Inline Tiff body" in record["sent"]["body"]
    assert api.calls == [("get_single_send", "sg-id")]


def test_collect_review_rejects_mismatched_mailing_name(tmp_path):
    api = FakeSendGridAPI(
        single_send={
            "id": "sg-id",
            "status": "triggered",
            "name": "2026_08: Yoga Lifestyle: Monthly",
        }
    )

    with pytest.raises(ValueError, match="does not match"):
        collect_review(
            api=api,
            single_send_id="sg-id",
            generated_path=_generated_file(tmp_path),
            mailing_name="2026_08: Yoga Habit: General Invitation",
            audience_key="non_lifestyle",
            captured_at="2026-08-04T10:15:00-06:00",
        )
