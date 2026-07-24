import copy
from datetime import datetime, timedelta, timezone
import hashlib
import json

import pytest

from sendgrid_migration_writer import (
    APPROVAL_STATEMENT,
    TARGET_ACCOUNT_EMAIL,
    WriterSafetyError,
    build_operation_plan,
    load_completed_evidence,
    validate_approval,
)


def _write_json(path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    path.chmod(0o600)


def completed_evidence(tmp_path):
    root = tmp_path / "production_pass"
    root.mkdir(mode=0o700)
    deliverable = [{
        "email": "active@example.com",
        "custom_fields": {
            "first_name": "Active",
            "twy_status": "member",
        },
        "proposed_lists": ["TWY Marketing", "TWY Yoga Lifestyle"],
        "reasons": ["mailchimp_subscribed"],
    }]
    suppressed = [{
        "email": "unsub@example.com",
        "effective_at": "2026-07-01T00:00:00Z",
        "reason": "mailchimp_unsubscribed",
        "source_status": "unsubscribed",
    }]
    cleaned = [{
        "email": "bad@example.com",
        "effective_at": "2026-06-01T00:00:00Z",
        "reason": "mailchimp_cleaned",
        "source_status": "cleaned",
    }]
    archived = [{
        "email": "old@example.com",
        "effective_at": "2024-10-01T00:00:00Z",
        "reason": "mailchimp_archived",
        "source_status": "archived",
    }]
    rows = {
        "deliverable_contacts": deliverable,
        "marketing_suppressions": suppressed,
        "cleaned_denylist": cleaned,
        "archived_exclusions": archived,
    }
    for name, value in rows.items():
        _write_json(root / f"{name}.json", value)
    _write_json(root / "manifest.json", {
        "gate_passed": True,
        "mapping_digest": "m" * 64,
        "source_digest": "s" * 64,
        "total_contacts": 4,
        "terminal_counts": {
            "archived_excluded": 1,
            "cleaned_denylist": 1,
            "deliverable": 1,
            "marketing_suppressed": 1,
        },
        "retention_manifest_counts": {
            name: len(value)
            for name, value in rows.items()
        },
    })
    (root / "COMPLETE").write_text("complete\n")
    (root / "COMPLETE").chmod(0o600)
    return root


def valid_approval(plan, now):
    return {
        "approved_by": "JP",
        "statement": APPROVAL_STATEMENT,
        "approved_at": now.isoformat(),
        "expires_at": (now + timedelta(hours=1)).isoformat(),
        "target_account_email": TARGET_ACCOUNT_EMAIL,
        "operation_digest": plan["operation_digest"],
        "source_digest": plan["source_digest"],
        "mapping_digest": plan["mapping_digest"],
        "counts": plan["counts"],
    }


def test_completed_evidence_requires_complete_marker(tmp_path):
    root = completed_evidence(tmp_path)
    (root / "COMPLETE").unlink()
    with pytest.raises(WriterSafetyError, match="COMPLETE"):
        load_completed_evidence(root)


def test_completed_evidence_rejects_blocked_gate(tmp_path):
    root = completed_evidence(tmp_path)
    manifest = json.loads((root / "manifest.json").read_text())
    manifest["gate_passed"] = False
    _write_json(root / "manifest.json", manifest)
    with pytest.raises(WriterSafetyError, match="gate"):
        load_completed_evidence(root)


def test_completed_evidence_rejects_count_or_identity_overlap(tmp_path):
    root = completed_evidence(tmp_path)
    suppressed = json.loads((root / "marketing_suppressions.json").read_text())
    suppressed[0]["email"] = "active@example.com"
    _write_json(root / "marketing_suppressions.json", suppressed)
    with pytest.raises(WriterSafetyError, match="disjoint"):
        load_completed_evidence(root)


def test_completed_evidence_rejects_profile_data_in_inactive_shape(tmp_path):
    root = completed_evidence(tmp_path)
    suppressed = json.loads((root / "marketing_suppressions.json").read_text())
    suppressed[0]["first_name"] = "Must not survive"
    _write_json(root / "marketing_suppressions.json", suppressed)
    with pytest.raises(WriterSafetyError, match="inactive"):
        load_completed_evidence(root)


def test_archived_email_can_never_appear_in_writer_inputs(tmp_path):
    root = completed_evidence(tmp_path)
    deliverable = json.loads((root / "deliverable_contacts.json").read_text())
    deliverable[0]["email"] = "old@example.com"
    _write_json(root / "deliverable_contacts.json", deliverable)
    with pytest.raises(WriterSafetyError, match="disjoint"):
        load_completed_evidence(root)


def test_operation_plan_is_canonical_and_references_private_files_by_digest(tmp_path):
    root = completed_evidence(tmp_path)
    plan = build_operation_plan(root)
    assert plan["target_account_email"] == "admin@tiffanywoodyoga.com"
    assert plan["suppression_group"] == {
        "description": "Tiffany Wood Yoga newsletters",
        "is_default": True,
        "name": "TWY Newsletters",
    }
    assert plan["custom_fields"] == {
        "twy_role": "Text",
        "twy_status": "Text",
    }
    assert plan["lists"] == ["TWY Marketing", "TWY Yoga Lifestyle"]
    assert plan["counts"] == {
        "archived_exclusions": 1,
        "cleaned_denylist": 1,
        "deliverable_contacts": 1,
        "marketing_suppressions": 1,
    }
    assert set(plan["evidence_files"]) == {
        "archived_exclusions.json",
        "cleaned_denylist.json",
        "deliverable_contacts.json",
        "marketing_suppressions.json",
    }
    assert all(
        value["sha256"] == hashlib.sha256(
            (root / name).read_bytes()
        ).hexdigest()
        for name, value in plan["evidence_files"].items()
    )
    assert "active@example.com" not in json.dumps(plan)
    assert len(plan["operation_digest"]) == 64
    assert build_operation_plan(root) == plan


@pytest.mark.parametrize(
    ("field", "replacement", "message"),
    [
        ("approved_by", "not-JP", "approver"),
        ("statement", "approve", "statement"),
        ("target_account_email", "other@example.com", "target"),
        ("operation_digest", "0" * 64, "operation"),
        ("source_digest", "0" * 64, "source"),
        ("mapping_digest", "0" * 64, "mapping"),
        ("counts", {"deliverable_contacts": 999}, "counts"),
    ],
)
def test_approval_requires_exact_plan_agreement(
    tmp_path, field, replacement, message
):
    plan = build_operation_plan(completed_evidence(tmp_path))
    now = datetime(2026, 7, 24, 19, 0, tzinfo=timezone.utc)
    approval = valid_approval(plan, now)
    approval[field] = replacement
    with pytest.raises(WriterSafetyError, match=message):
        validate_approval(plan, approval, now)


def test_approval_rejects_future_expired_or_overlong_window(tmp_path):
    plan = build_operation_plan(completed_evidence(tmp_path))
    now = datetime(2026, 7, 24, 19, 0, tzinfo=timezone.utc)
    future = valid_approval(plan, now)
    future["approved_at"] = (now + timedelta(minutes=1)).isoformat()
    with pytest.raises(WriterSafetyError, match="future"):
        validate_approval(plan, future, now)

    expired = valid_approval(plan, now)
    expired["expires_at"] = (now - timedelta(seconds=1)).isoformat()
    with pytest.raises(WriterSafetyError, match="expired"):
        validate_approval(plan, expired, now)

    overlong = valid_approval(plan, now)
    overlong["expires_at"] = (now + timedelta(hours=25)).isoformat()
    with pytest.raises(WriterSafetyError, match="24 hours"):
        validate_approval(plan, overlong, now)


def test_exact_valid_approval_is_accepted(tmp_path):
    plan = build_operation_plan(completed_evidence(tmp_path))
    now = datetime(2026, 7, 24, 19, 0, tzinfo=timezone.utc)
    validate_approval(plan, copy.deepcopy(valid_approval(plan, now)), now)
