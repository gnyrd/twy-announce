#!/usr/bin/env python3
"""Approval-locked writer for completed TWY SendGrid migration evidence."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
from collections import defaultdict
import hashlib
import json
import os
from pathlib import Path
import sys
from typing import Any

from sendgrid_migration_evidence import EvidenceStore


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
DELIVERABLE_KEYS = {
    "email",
    "custom_fields",
    "proposed_lists",
    "reasons",
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
        if name == "deliverable_contacts":
            for row in rows:
                if set(row) != DELIVERABLE_KEYS:
                    raise WriterSafetyError(
                        "deliverable contact has unexpected fields"
                    )
        else:
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
            "exact_email_search": 100,
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


def _chunks(values: list[Any], size: int):
    for index in range(0, len(values), size):
        yield values[index:index + size]


def _resolve_named_resources(
    existing: list[dict[str, Any]],
    names: list[str],
    create,
    kind: str,
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for name in names:
        matches = [row for row in existing if row.get("name") == name]
        if len(matches) > 1:
            raise WriterSafetyError(f"duplicate {kind} named {name}")
        if matches:
            result[name] = matches[0]
        else:
            created = create(name)
            if created.get("name") != name or not created.get("id"):
                raise WriterSafetyError(f"created {kind} did not match {name}")
            existing.append(created)
            result[name] = created
    return result


def _resolve_custom_fields(
    api,
    expected: dict[str, str],
) -> dict[str, dict[str, Any]]:
    existing = api.field_definitions()
    result: dict[str, dict[str, Any]] = {}
    for name, field_type in sorted(expected.items()):
        matches = [row for row in existing if row.get("name") == name]
        if len(matches) > 1:
            raise WriterSafetyError(f"duplicate custom field named {name}")
        if matches:
            row = matches[0]
            if str(row.get("field_type")).lower() != field_type.lower():
                raise WriterSafetyError(
                    f"custom field {name} has wrong type"
                )
        else:
            row = api.create_field_definition(name, field_type)
            if (
                row.get("name") != name
                or str(row.get("field_type")).lower() != field_type.lower()
                or not row.get("id")
            ):
                raise WriterSafetyError(
                    f"created custom field {name} failed verification"
                )
            existing.append(row)
        result[name] = row
    return result


def _resolve_suppression_group(
    api,
    expected: dict[str, Any],
) -> dict[str, Any]:
    matches = [
        row
        for row in api.suppression_groups()
        if row.get("name") == expected["name"]
    ]
    if len(matches) > 1:
        raise WriterSafetyError(
            f"duplicate suppression group named {expected['name']}"
        )
    if matches:
        selected = matches[0]
    else:
        selected = api.create_suppression_group(
            expected["name"],
            expected["description"],
            expected["is_default"],
        )
    group_id = selected.get("id")
    if group_id is None:
        raise WriterSafetyError("suppression group has no ID")
    verified = api.suppression_group(int(group_id))
    wanted = {
        "id": int(group_id),
        "name": expected["name"],
        "description": expected["description"],
        "is_default": expected["is_default"],
    }
    actual = {
        "id": int(verified.get("id", -1)),
        "name": verified.get("name"),
        "description": verified.get("description"),
        "is_default": verified.get("is_default"),
    }
    if actual != wanted:
        raise WriterSafetyError("suppression group failed exact verification")
    return verified


def _private_json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def _assert_private_json_compatible(path: Path, value: Any) -> None:
    if not path.exists():
        return
    if path.read_bytes() != _private_json_bytes(value):
        raise WriterSafetyError(
            f"existing {path.name} differs from approved evidence"
        )
    if path.stat().st_mode & 0o777 != 0o600:
        raise WriterSafetyError(f"existing {path.name} is not mode 0600")


def _write_private_json_once(path: Path, value: Any) -> None:
    payload = _private_json_bytes(value)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.parent.chmod(0o700)
    if path.exists():
        _assert_private_json_compatible(path, value)
        return

    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            if path.read_bytes() != payload:
                raise WriterSafetyError(
                    f"concurrent {path.name} differs from approved evidence"
                )
        path.chmod(0o600)
    finally:
        if temporary.exists():
            temporary.unlink()


def _contact_payload(
    row: dict[str, Any],
    custom_fields: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    payload: dict[str, Any] = {"email": row["email"]}
    custom_payload: dict[str, str] = {}
    for name, value in (row.get("custom_fields") or {}).items():
        if name in {"first_name", "last_name"}:
            payload[name] = value
        elif name in custom_fields:
            custom_payload[str(custom_fields[name]["id"])] = value
        else:
            raise WriterSafetyError(f"unresolved contact field: {name}")
    if custom_payload:
        payload["custom_fields"] = custom_payload
    return payload


def apply_operation_plan(
    api,
    plan: dict[str, Any],
    approval: dict[str, Any],
    evidence_dir: Path,
    denylist_path: Path,
    report_dir: Path,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    report_root = Path(report_dir)
    if (report_root / "COMPLETE").exists():
        raise WriterSafetyError("apply report is already complete")
    fresh_plan = build_operation_plan(evidence_dir)
    if fresh_plan != plan:
        raise WriterSafetyError("operation plan differs from current evidence")
    validate_approval(plan, approval, now)
    evidence = load_completed_evidence(evidence_dir)
    cleaned_rows = evidence["manifests"]["cleaned_denylist"]
    _assert_private_json_compatible(Path(denylist_path), cleaned_rows)
    if api.user_email() != plan["target_account_email"]:
        raise WriterSafetyError("SendGrid target account does not match approval")

    store = EvidenceStore(report_root)
    store.write_json("started.json", {
        "operation_digest": plan["operation_digest"],
        "source_digest": plan["source_digest"],
        "mapping_digest": plan["mapping_digest"],
        "counts": plan["counts"],
    })

    lists = _resolve_named_resources(
        api.marketing_lists(),
        plan["lists"],
        api.create_list,
        "list",
    )
    fields = _resolve_custom_fields(api, plan["custom_fields"])
    group = _resolve_suppression_group(api, plan["suppression_group"])
    group_id = int(group["id"])

    suppressed_rows = evidence["manifests"]["marketing_suppressions"]
    suppressed_emails = [row["email"] for row in suppressed_rows]
    suppression_batch = plan["batch_limits"]["group_suppression"]
    for batch in _chunks(suppressed_emails, suppression_batch):
        api.add_group_suppressions(group_id, batch)
    verified_suppressions: set[str] = set()
    for batch in _chunks(suppressed_emails, suppression_batch):
        verified_suppressions.update(
            api.search_group_suppressions(group_id, batch)
        )
    if verified_suppressions != set(suppressed_emails):
        raise WriterSafetyError("group suppression postcondition failed")

    _write_private_json_once(Path(denylist_path), cleaned_rows)

    deliverable = evidence["manifests"]["deliverable_contacts"]
    grouped: dict[tuple[str, ...], list[dict[str, Any]]] = defaultdict(list)
    for row in deliverable:
        grouped[tuple(sorted(row.get("proposed_lists") or []))].append(row)

    jobs: list[dict[str, Any]] = []
    contact_batch = plan["batch_limits"]["contact_upsert"]
    for list_names, rows in sorted(grouped.items()):
        list_ids = [str(lists[name]["id"]) for name in list_names]
        for batch in _chunks(rows, contact_batch):
            contacts = [_contact_payload(row, fields) for row in batch]
            job_id = api.upsert_contacts(list_ids, contacts)
            outcome = api.wait_contact_job(job_id)
            results = outcome.get("results") or {}
            if (
                outcome.get("status") != "completed"
                or results.get("errored_count", 0) != 0
                or results.get("requested_count") != len(batch)
            ):
                raise WriterSafetyError(
                    f"contact import {job_id} failed postcondition"
                )
            jobs.append({
                "job_id": job_id,
                "requested_count": len(batch),
                "created_count": results.get("created_count", 0),
                "updated_count": results.get("updated_count", 0),
            })

    expected_lists = {
        row["email"]: {
            str(lists[name]["id"])
            for name in row.get("proposed_lists") or []
        }
        for row in deliverable
    }
    verified_contacts = 0
    search_batch = plan["batch_limits"]["exact_email_search"]
    for batch in _chunks([row["email"] for row in deliverable], search_batch):
        found = api.contacts_by_emails(batch)
        for email in batch:
            contact = found.get(email)
            if contact is None:
                raise WriterSafetyError(
                    "deliverable contact postcondition is missing"
                )
            actual_lists = {
                str(list_id) for list_id in contact.get("list_ids") or []
            }
            if not expected_lists[email].issubset(actual_lists):
                raise WriterSafetyError(
                    "deliverable contact list postcondition failed"
                )
            verified_contacts += 1

    result = {
        "operation_digest": plan["operation_digest"],
        "counts": plan["counts"],
        "resource_ids": {
            "lists": {
                name: str(row["id"])
                for name, row in sorted(lists.items())
            },
            "custom_fields": {
                name: str(row["id"])
                for name, row in sorted(fields.items())
            },
            "suppression_group_id": group_id,
        },
        "contact_jobs": jobs,
        "postconditions": {
            "cleaned_denylist_written": True,
            "contacts_verified": verified_contacts,
            "group_suppressions_verified": len(verified_suppressions),
        },
    }
    store.write_json("result.json", result)
    store.complete()
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Approval-locked TWY SendGrid production contact writer"
    )
    commands = parser.add_subparsers(dest="command", required=True)

    plan = commands.add_parser(
        "plan",
        help="build a private operation plan without provider writes",
    )
    plan.add_argument("--evidence-dir", type=Path, required=True)
    plan.add_argument("--operation-plan", type=Path, required=True)

    apply = commands.add_parser(
        "apply",
        help="apply one separately approved operation plan",
    )
    apply.add_argument("--evidence-dir", type=Path, required=True)
    apply.add_argument("--operation-plan", type=Path, required=True)
    apply.add_argument("--approval-file", type=Path, required=True)
    apply.add_argument("--expected-plan-digest", required=True)
    apply.add_argument("--apply-id", required=True)
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "plan":
            plan = build_operation_plan(args.evidence_dir)
            _write_private_json_once(args.operation_plan, plan)
            print(json.dumps({
                "operation_digest": plan["operation_digest"],
                "counts": plan["counts"],
                "target_account_email": plan["target_account_email"],
            }, indent=2, sort_keys=True))
            return 0

        plan = _read_json(args.operation_plan)
        approval = _read_json(args.approval_file)
        if plan.get("operation_digest") != args.expected_plan_digest:
            raise WriterSafetyError(
                "expected plan digest does not match operation plan"
            )

        from sendgrid_api import SendGridAPI
        from twy_paths import (
            load_env,
            sendgrid_cleaned_denylist_path,
            sendgrid_migration_apply_dir,
        )

        load_env()
        api_key = os.getenv("SENDGRID_API_KEY")
        if not api_key:
            print(
                "missing required configuration: SENDGRID_API_KEY",
                file=sys.stderr,
            )
            return 2
        result = apply_operation_plan(
            SendGridAPI(api_key),
            plan,
            approval,
            args.evidence_dir,
            sendgrid_cleaned_denylist_path(),
            sendgrid_migration_apply_dir(args.apply_id),
        )
        print(json.dumps({
            "operation_digest": result["operation_digest"],
            "counts": result["counts"],
            "postconditions": result["postconditions"],
        }, indent=2, sort_keys=True))
        return 0
    except WriterSafetyError as exc:
        print(f"writer safety gate blocked: {exc}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
