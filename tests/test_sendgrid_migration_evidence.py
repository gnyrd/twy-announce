import json
from pathlib import Path

import pytest

from sendgrid_contact_mapping import DesiredContact
from sendgrid_migration_evidence import (
    EvidenceAlreadyExists,
    EvidenceStore,
    canonical_digest,
    summarize,
)


def desired(email, terminal):
    return DesiredContact(
        email=email,
        terminal_class=terminal,
        proposed_lists=frozenset(),
        custom_fields={},
        provenance={"mailchimp_status": terminal},
        reasons=(),
    )


def test_digest_is_independent_of_input_order():
    first = [desired("a@example.com", "deliverable"), desired("b@example.com", "quarantine")]
    assert canonical_digest(first) == canonical_digest(reversed(first))


def test_evidence_is_private_atomic_and_immutable(tmp_path):
    root = tmp_path / "run"
    store = EvidenceStore(root)
    store.write_json("manifest.json", {"ok": True})
    assert root.stat().st_mode & 0o777 == 0o700
    assert (root / "manifest.json").stat().st_mode & 0o777 == 0o600
    store.complete()
    with pytest.raises(EvidenceAlreadyExists):
        EvidenceStore(root)


def test_evidence_redacts_secret_keys_and_signed_urls(tmp_path):
    store = EvidenceStore(tmp_path / "run")
    store.write_json("state.json", {
        "Authorization": "Bearer secret",
        "api_key": "secret",
        "url": "https://example.com/file?X-Amz-Signature=secret",
    })
    payload = (tmp_path / "run" / "state.json").read_text()
    assert "secret" not in payload
    assert "X-Amz" not in payload


def test_summary_contains_counts_not_email_addresses():
    contacts = [
        desired("private1@example.com", "deliverable"),
        desired("private2@example.com", "marketing_suppressed"),
    ]
    summary = summarize(contacts)
    rendered = json.dumps(summary)
    assert summary["terminal_counts"] == {
        "deliverable": 1,
        "marketing_suppressed": 1,
    }
    assert "private1@example.com" not in rendered
