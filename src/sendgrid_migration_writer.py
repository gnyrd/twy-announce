#!/usr/bin/env python3
"""Approval-locked writer for completed TWY SendGrid migration evidence."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
from typing import Any


TARGET_ACCOUNT_EMAIL = "admin@tiffanywoodyoga.com"
APPROVAL_STATEMENT = "APPROVE TWY SENDGRID PRODUCTION CONTACT APPLY"
SUPPRESSION_GROUP = {
    "name": "TWY Newsletters",
    "description": "Tiffany Wood Yoga newsletters",
    "is_default": True,
}
CUSTOM_FIELDS = {
    "twy_role": "Text",
    "twy_status": "Text",
}
MANIFEST_FILES = (
    "deliverable_contacts",
    "marketing_suppressions",
    "cleaned_denylist",
    "archived_exclusions",
)
INACTIVE_KEYS = {
    "email",
    "effective_at",
    "reason",
    "source_status",
}


class WriterSafetyError(RuntimeError):
    """Raised before a migration writer safety boundary can be crossed."""


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise WriterSafetyError(f"cannot read valid JSON: {path.name}") from exc


def _normalized_email(value: Any) -> str:
    email = str(value or "").strip().lower()
    if not email or "@" not in email or email != value:
        raise WriterSafetyError("evidence contains a non-normalized email")
    return email


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _canonical_digest(value: Any) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def load_completed_evidence(path: Path) -> dict[str, Any]:
    root = Path(path)
    if not (root / "COMPLETE").is_file():
        raise WriterSafetyError("completed evidence requires COMPLETE marker")

    manifest = _read_json(root / "manifest.json")
    if not isinstance(manifest, dict) or manifest.get("gate_passed") is not True:
        raise WriterSafetyError("migration evidence gate is not passed")
    if (manifest.get("terminal_counts") or {}).get("quarantine", 0):
        raise WriterSafetyError("migration evidence contains quarantine")

    manifests: dict[str, list[dict[str, Any]]] = {}
    email_sets: dict[str, set[str]] = {}
    expected_counts = manifest.get("retention_manifest_counts") or {}
    for name in MANIFEST_FILES:
        rows = _read_json(root / f"{name}.json")
        if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
            raise WriterSafetyError(f"{name} must be a JSON array of objects")
        if expected_counts.get(name) != len(rows):
            raise WriterSafetyError(f"{name} count differs from manifest")
        emails = [_normalized_email(row.get("email")) for row in rows]
        if len(set(emails)) != len(emails):
            raise WriterSafetyError(f"{name} contains duplicate identities")
        if name != "deliverable_contacts":
            for row in rows:
                if set(row) != INACTIVE_KEYS:
                    raise WriterSafetyError(
                        f"{name} inactive record has non-minimal fields"
                    )
        manifests[name] = rows
        email_sets[name] = set(emails)

    names = list(MANIFEST_FILES)
    for index, left in enumerate(names):
        for right in names[index + 1:]:
            if email_sets[left] & email_sets[right]:
                raise WriterSafetyError(
                    f"retention manifests are not disjoint: {left}, {right}"
                )
    total = sum(len(rows) for rows in manifests.values())
    if manifest.get("total_contacts") != total:
        raise WriterSafetyError("retention manifest total differs from source")
    return {
        "root": root,
        "manifest": manifest,
        "manifests": manifests,
    }


def build_operation_plan(evidence_dir: Path) -> dict[str, Any]:
    evidence = load_completed_evidence(evidence_dir)
    root = evidence["root"]
    manifest = evidence["manifest"]
    deliverable = evidence["manifests"]["deliverable_contacts"]
    lists = sorted({
        str(list_name)
        for row in deliverable
        for list_name in row.get("proposed_lists") or []
    })
    evidence_files = {
        f"{name}.json": {
            "count": len(evidence["manifests"][name]),
            "sha256": _sha256(root / f"{name}.json"),
        }
        for name in MANIFEST_FILES
    }
    plan: dict[str, Any] = {
        "schema_version": 1,
        "target_account_email": TARGET_ACCOUNT_EMAIL,
        "source_digest": manifest.get("source_digest"),
        "mapping_digest": manifest.get("mapping_digest"),
        "counts": {
            name: len(evidence["manifests"][name])
            for name in sorted(MANIFEST_FILES)
        },
        "evidence_files": evidence_files,
        "lists": lists,
        "custom_fields": CUSTOM_FIELDS,
        "suppression_group": SUPPRESSION_GROUP,
        "batch_limits": {
            "contact_upsert": 500,
            "exact_email_search": 500,
            "group_suppression": 500,
        },
    }
    plan["operation_digest"] = _canonical_digest(plan)
    return plan


def _parse_time(value: Any, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError as exc:
        raise WriterSafetyError(f"approval {field} is not ISO-8601") from exc
    if parsed.tzinfo is None:
        raise WriterSafetyError(f"approval {field} must include timezone")
    return parsed.astimezone(timezone.utc)


def validate_approval(
    plan: dict[str, Any],
    approval: dict[str, Any],
    now: datetime | None = None,
) -> None:
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    exact = (
        ("approved_by", "JP", "approver"),
        ("statement", APPROVAL_STATEMENT, "statement"),
        ("target_account_email", plan.get("target_account_email"), "target"),
        ("operation_digest", plan.get("operation_digest"), "operation"),
        ("source_digest", plan.get("source_digest"), "source"),
        ("mapping_digest", plan.get("mapping_digest"), "mapping"),
        ("counts", plan.get("counts"), "counts"),
    )
    for field, expected, label in exact:
        if approval.get(field) != expected:
            raise WriterSafetyError(f"approval {label} does not match plan")

    approved_at = _parse_time(approval.get("approved_at"), "approved_at")
    expires_at = _parse_time(approval.get("expires_at"), "expires_at")
    if approved_at > current:
        raise WriterSafetyError("approval time is in the future")
    if expires_at <= current:
        raise WriterSafetyError("approval is expired")
    if expires_at <= approved_at:
        raise WriterSafetyError("approval expiry must follow approval time")
    if expires_at - approved_at > timedelta(hours=24):
        raise WriterSafetyError("approval window exceeds 24 hours")
