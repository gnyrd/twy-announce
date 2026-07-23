import json

import pytest

from sendgrid_proof_models import (
    ALLOWED_STATUSES,
    CapabilityResult,
    EvidenceStore,
    ProofConfig,
    ProofManifest,
    RecipientSafetyError,
    assert_allowed_recipients,
    redact,
)


def test_default_config_pins_recipient_and_synthetic_shapes():
    config = ProofConfig()
    assert config.deliverable_addresses == frozenset({
        "admin@tiffanywoodyoga.com",
        "jpgan6@gmail.com",
    })
    assert config.synthetic_addresses == frozenset({
        "subscribed@twy-sendgrid-proof.invalid",
        "unsubscribed@twy-sendgrid-proof.invalid",
        "cleaned@twy-sendgrid-proof.invalid",
    })
    assert config.scheduled_delay_seconds == 600


def test_allowlist_accepts_only_exact_casefolded_membership():
    assert_allowed_recipients({
        " ADMIN@TIFFANYWOODYOGA.COM ",
        "jpgan6@gmail.com",
    })


def test_allowlist_rejects_third_recipient():
    with pytest.raises(RecipientSafetyError):
        assert_allowed_recipients({
            "admin@tiffanywoodyoga.com",
            "jpgan6@gmail.com",
            "someone@example.com",
        })


def test_allowlist_rejects_missing_recipient():
    with pytest.raises(RecipientSafetyError):
        assert_allowed_recipients({"admin@tiffanywoodyoga.com"})


def test_config_rejects_non_reserved_synthetic_address():
    with pytest.raises(ValueError):
        ProofConfig(synthetic_addresses=frozenset({"synthetic@example.com"}))


def test_redact_removes_secrets_headers_and_signed_query_values():
    value = {
        "Authorization": "Bearer SG.secret",
        "api_key": "SG.secret",
        "nested": {
            "cookie": "session=abc",
            "url": "https://storage.example/export.csv?X-Amz-Signature=secret&x=1",
        },
        "safe": "keep",
    }
    result = redact(value)
    assert result["Authorization"] == "[REDACTED]"
    assert result["api_key"] == "[REDACTED]"
    assert result["nested"]["cookie"] == "[REDACTED]"
    assert result["nested"]["url"] == "https://storage.example/export.csv"
    assert result["safe"] == "keep"


def test_evidence_store_writes_redacted_json_atomically(tmp_path):
    store = EvidenceStore(tmp_path)
    path = store.write_json("request.json", {"token": "secret", "ok": True})
    assert path == tmp_path / "request.json"
    assert json.loads(path.read_text()) == {"token": "[REDACTED]", "ok": True}
    assert list(tmp_path.glob("*.tmp")) == []


def test_evidence_store_writes_binary_artifact_atomically(tmp_path):
    store = EvidenceStore(tmp_path)
    path = store.write_bytes("contacts/export.csv", b"email\nproof@example.com\n")
    assert path == tmp_path / "contacts/export.csv"
    assert path.read_bytes() == b"email\nproof@example.com\n"
    assert list((tmp_path / "contacts").glob("*.tmp")) == []


def test_manifest_round_trips_for_resume(tmp_path):
    store = EvidenceStore(tmp_path)
    manifest = ProofManifest(run_id="run-1", phase="seed", object_ids={"list": "l1"})
    store.write_manifest(manifest)
    assert store.read_manifest() == manifest


def test_capability_status_is_closed_enum():
    assert ALLOWED_STATUSES == frozenset({
        "proven", "unavailable", "plan-gated", "unknown",
    })
    with pytest.raises(ValueError):
        CapabilityResult(status="passed", evidence=(), detail="bad")
