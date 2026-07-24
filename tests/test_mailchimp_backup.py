from __future__ import annotations

import json
import hashlib
import stat
from pathlib import Path

import pytest
import requests

from mailchimp_backup import (
    BackupGap,
    MailchimpReadOnlyClient,
    REPORT_PAGE_SIZE,
    SnapshotWriter,
    classify_trigger,
    copy_ui_supplements,
    extract_campaign_renderings,
    paginate,
    validate_ui_supplements,
)


class FakeClient:
    def __init__(self, pages):
        self.pages = list(pages)
        self.calls = []

    def get(self, path, *, params=None, allow_status=()):
        self.calls.append((path, dict(params or {}), tuple(allow_status)))
        return self.pages.pop(0)


class FakeResponse:
    status_code = 200
    reason = "OK"

    def json(self):
        return {"items": [], "total_items": 0}

    def raise_for_status(self):
        return None


class FlakySession:
    def __init__(self):
        self.calls = 0
        self.auth = None

    def get(self, *args, **kwargs):
        self.calls += 1
        if self.calls == 1:
            raise requests.ReadTimeout("first call timed out")
        return FakeResponse()


def test_paginate_collects_every_item_and_validates_total():
    client = FakeClient(
        [
            {"items": [{"id": "1"}, {"id": "2"}], "total_items": 3},
            {"items": [{"id": "3"}], "total_items": 3},
        ]
    )

    result = paginate(client, "/things", "items", page_size=2)

    assert [item["id"] for item in result] == ["1", "2", "3"]
    assert [call[1]["offset"] for call in client.calls] == [0, 2]


def test_paginate_rejects_empty_page_before_total():
    client = FakeClient(
        [
            {"items": [{"id": "1"}], "total_items": 2},
            {"items": [], "total_items": 2},
        ]
    )

    with pytest.raises(BackupGap, match="ended at 1 of 2"):
        paginate(client, "/things", "items", page_size=1)


def test_paginate_rejects_total_that_changes_between_pages():
    client = FakeClient(
        [
            {"items": [{"id": "1"}], "total_items": 2},
            {"items": [{"id": "2"}], "total_items": 3},
        ]
    )

    with pytest.raises(BackupGap, match="total_items changed"):
        paginate(client, "/things", "items", page_size=1)


def test_read_only_client_retries_timeout_and_audits_both_attempts():
    client = MailchimpReadOnlyClient(
        server_prefix="us21",
        api_key="not-a-real-key",
        max_retries=1,
        retry_delay=0,
    )
    client.session = FlakySession()

    payload = client.get("/things")

    assert payload == {"items": [], "total_items": 0}
    assert client.session.calls == 2
    assert client.audit == [
        {
            "method": "GET",
            "path": "/things",
            "status": None,
            "error": "ReadTimeout",
            "attempt": 1,
        },
        {
            "method": "GET",
            "path": "/things",
            "status": 200,
            "attempt": 2,
        },
    ]


def test_reports_use_bounded_pages():
    assert REPORT_PAGE_SIZE <= 50


@pytest.mark.parametrize(
    ("steps", "tags", "complete", "expected"),
    [
        (
            [{"trigger_settings": {"tag_id": 12}}],
            {12: {"id": 12, "name": "Member", "member_count": 4}},
            True,
            "resolved",
        ),
        (
            [{"trigger_settings": {"tag_id": 12}}],
            {},
            True,
            "deleted",
        ),
        (
            [{"trigger_settings": {"tag_id": 12}}],
            {},
            False,
            "unknown",
        ),
        (
            [{"step_type": "trigger", "trigger_settings": {"source": "signup"}}],
            {},
            True,
            "not-a-tag-trigger",
        ),
        (None, {}, True, "unknown"),
    ],
)
def test_classify_trigger_distinguishes_all_required_states(
    steps, tags, complete, expected
):
    assert classify_trigger(steps, tags, tags_complete=complete)["state"] == expected


def test_campaign_content_requires_rendered_html():
    with pytest.raises(BackupGap, match="missing rendered HTML"):
        extract_campaign_renderings("abc", {"plain_text": "not enough"})


def test_campaign_content_produces_html_and_readable_text():
    html, text = extract_campaign_renderings(
        "abc", {"html": "<h1>Hello</h1><p>World</p>"}
    )

    assert html == "<h1>Hello</h1><p>World</p>"
    assert "Hello" in text
    assert "World" in text


def test_snapshot_writer_refuses_to_reuse_existing_run(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()

    with pytest.raises(FileExistsError):
        SnapshotWriter(run_dir)


def test_snapshot_writer_records_checksums(tmp_path):
    writer = SnapshotWriter(tmp_path / "run")
    writer.write_json("one.json", {"hello": "world"})
    writer.write_text("two.txt", "text\n")

    files = writer.file_ledger()

    assert [entry["path"] for entry in files] == ["one.json", "two.txt"]
    assert all(len(entry["sha256"]) == 64 for entry in files)


def test_snapshot_writer_keeps_every_nested_directory_private(tmp_path):
    writer = SnapshotWriter(tmp_path / "run")
    writer.write_text("one/two/three.txt", "private\n")

    assert stat.S_IMODE((writer.run_dir / "one").stat().st_mode) == 0o700
    assert stat.S_IMODE((writer.run_dir / "one/two").stat().st_mode) == 0o700
    assert stat.S_IMODE(
        (writer.run_dir / "one/two/three.txt").stat().st_mode
    ) == 0o600


def test_ui_supplements_require_every_inaccessible_journey(tmp_path):
    supplement_dir = tmp_path / "ui"
    supplement_dir.mkdir()
    (supplement_dir / "2745.json").write_text(
        json.dumps(
            {
                "journey_id": 2745,
                "source": "mailchimp-builder-ui",
                "steps": [{"position": 0, "type": "trigger"}],
                "campaign_ids": ["email-a"],
            }
        )
    )

    gaps = validate_ui_supplements(
        supplement_dir,
        required_journey_ids={2745, 2925},
        exported_campaign_ids={"email-a"},
    )

    assert any("2925" in gap for gap in gaps)


def test_ui_supplement_rejects_unexported_campaign_reference(tmp_path):
    supplement_dir = tmp_path / "ui"
    supplement_dir.mkdir()
    (supplement_dir / "2745.json").write_text(
        json.dumps(
            {
                "journey_id": 2745,
                "source": "mailchimp-builder-ui",
                "steps": [{"position": 0, "type": "trigger"}],
                "campaign_ids": ["missing-email"],
            }
        )
    )

    gaps = validate_ui_supplements(
        supplement_dir,
        required_journey_ids={2745},
        exported_campaign_ids={"email-a"},
    )

    assert any("missing-email" in gap for gap in gaps)


def test_ui_supplement_verifies_builder_html_and_dom_snapshot(tmp_path):
    supplement_dir = tmp_path / "ui"
    supplement_dir.mkdir()
    html_path = supplement_dir / "builder-emails/2745/email.html"
    html_path.parent.mkdir(parents=True)
    html_body = b"<html><body>Complete email</body></html>"
    html_path.write_bytes(html_body)
    dom_path = supplement_dir / "raw/2745.dom.txt"
    dom_path.parent.mkdir()
    dom_body = b"builder DOM evidence"
    dom_path.write_bytes(dom_body)
    (supplement_dir / "2745.json").write_text(
        json.dumps(
            {
                "journey_id": 2745,
                "source": "mailchimp-builder-ui",
                "dom_snapshot": {
                    "path": "raw/2745.dom.txt",
                    "bytes": len(dom_body),
                    "sha256": hashlib.sha256(dom_body).hexdigest(),
                },
                "steps": [
                    {
                        "position": 0,
                        "type": "send-email",
                        "content_ref": {
                            "kind": "builder-preview-html",
                            "path": "builder-emails/2745/email.html",
                            "bytes": len(html_body),
                            "sha256": hashlib.sha256(html_body).hexdigest(),
                        },
                    }
                ],
                "campaign_ids": [],
                "email_count": 1,
            }
        )
    )

    gaps = validate_ui_supplements(
        supplement_dir,
        required_journey_ids={2745},
        exported_campaign_ids=set(),
    )

    assert gaps == []

    html_path.write_bytes(html_body.replace(b"email", b"emAil"))
    gaps = validate_ui_supplements(
        supplement_dir,
        required_journey_ids={2745},
        exported_campaign_ids=set(),
    )
    assert any("checksum mismatch" in gap for gap in gaps)


def test_ui_supplement_requires_content_for_every_email_step(tmp_path):
    supplement_dir = tmp_path / "ui"
    supplement_dir.mkdir()
    (supplement_dir / "2745.json").write_text(
        json.dumps(
            {
                "journey_id": 2745,
                "source": "mailchimp-builder-ui",
                "steps": [{"position": 0, "type": "send-email"}],
                "campaign_ids": [],
                "email_count": 1,
            }
        )
    )

    gaps = validate_ui_supplements(
        supplement_dir,
        required_journey_ids={2745},
        exported_campaign_ids=set(),
    )

    assert any("content_ref missing" in gap for gap in gaps)


def test_copy_ui_supplements_places_files_in_snapshot_ledger(tmp_path):
    source = tmp_path / "ui"
    source.mkdir()
    (source / "2745.json").write_text('{"journey_id": 2745}\n')
    writer = SnapshotWriter(tmp_path / "snapshot")

    copied = copy_ui_supplements(source, writer)

    assert copied == 1
    assert (writer.run_dir / "ui-supplements/2745.json").is_file()
    assert any(
        item["path"] == "ui-supplements/2745.json"
        for item in writer.file_ledger()
    )
