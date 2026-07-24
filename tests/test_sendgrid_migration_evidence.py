import json
from pathlib import Path

import pytest

from sendgrid_contact_mapping import DesiredContact
from sendgrid_migration_evidence import (
    EvidenceAlreadyExists,
    EvidenceStore,
    canonical_digest,
    retention_manifests,
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


def test_retention_manifests_are_disjoint_complete_and_minimal():
    contacts = [
        desired("active@example.com", "deliverable"),
        desired("unsub@example.com", "marketing_suppressed"),
        desired("bad@example.com", "cleaned_denylist"),
        desired("old@example.com", "archived_excluded"),
    ]
    manifests = retention_manifests(contacts)
    assert [row["email"] for row in manifests["deliverable_contacts"]] == [
        "active@example.com"
    ]
    assert manifests["marketing_suppressions"] == [{
        "email": "unsub@example.com",
        "effective_at": "",
        "reason": "",
        "source_status": "marketing_suppressed",
    }]
    assert manifests["cleaned_denylist"][0]["email"] == "bad@example.com"
    assert manifests["archived_exclusions"][0]["email"] == "old@example.com"
    assert sum(len(rows) for rows in manifests.values()) == len(contacts)
    rendered_inactive = json.dumps({
        key: rows
        for key, rows in manifests.items()
        if key != "deliverable_contacts"
    })
    assert "custom_fields" not in rendered_inactive
    assert "proposed_lists" not in rendered_inactive
    assert "tags" not in rendered_inactive
