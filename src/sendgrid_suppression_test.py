"""Separately approved SendGrid suppression-enforcement proof harness."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import sys
import time
from typing import Any, Callable

from sendgrid_campaigns import UNSUBSCRIBE_GROUP_NAME


TARGET_ACCOUNT_EMAIL = "admin@tiffanywoodyoga.com"
PRODUCTION_GROUP_NAME = UNSUBSCRIBE_GROUP_NAME
ALLOWED_RECIPIENTS = {
    "admin@tiffanywoodyoga.com",
    "jpgan6@gmail.com",
}
PREFERRED_SUPPRESSION_TEST_RECIPIENT = "jpgan6@gmail.com"
SUPPRESSION_CONTROL_RECIPIENT = TARGET_ACCOUNT_EMAIL
APPROVED_SUPPRESSION_PROOF_LIST_NAME = (
    "Proof: Suppression Enforcement Control: 2026_07_26"
)
SUPPRESSION_SETUP_APPROVAL_STATEMENT = (
    "APPROVE TWY SENDGRID SUPPRESSION PROOF SETUP AND TEST"
)
SUPPRESSION_TEST_APPROVAL_STATEMENT = (
    "APPROVE TWY SENDGRID SUPPRESSION ENFORCEMENT TEST"
)
SUPPRESSION_CLEANUP_APPROVAL_STATEMENT = (
    "APPROVE TWY SENDGRID SUPPRESSION TEST CLEANUP"
)


class SuppressionTestSafetyError(RuntimeError):
    """Raised before or during an unsafe suppression test condition."""


def _canonical_digest(value: Any) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@contextmanager
def _exclusive_run_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        try:
            fcntl.flock(
                descriptor,
                fcntl.LOCK_EX | fcntl.LOCK_NB,
            )
        except BlockingIOError as exc:
            raise SuppressionTestSafetyError(
                "another suppression proof operation is active"
            ) from exc
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _normalized_email(value: Any) -> str:
    email = str(value or "").strip().lower()
    if not email or "@" not in email:
        raise SuppressionTestSafetyError("invalid test recipient")
    return email


def _allowed_recipient(value: Any) -> str:
    recipient = _normalized_email(value)
    if recipient not in ALLOWED_RECIPIENTS:
        raise SuppressionTestSafetyError(
            "test recipient is outside the explicit allowlist"
        )
    return recipient


def build_suppression_test_plan(
    *,
    run_id: str,
    recipient: str,
    control_recipient: str,
    list_id: str,
    group_id: int,
    sender_id: int,
) -> dict[str, Any]:
    normalized = _allowed_recipient(recipient)
    normalized_control = _allowed_recipient(control_recipient)
    if (
        normalized != PREFERRED_SUPPRESSION_TEST_RECIPIENT
        or normalized_control != SUPPRESSION_CONTROL_RECIPIENT
        or normalized == normalized_control
    ):
        raise SuppressionTestSafetyError(
            "test requires distinct approved suppression and control recipients"
        )
    if not run_id or "/" in run_id or "\\" in run_id:
        raise SuppressionTestSafetyError("test run_id is unsafe")
    if not list_id:
        raise SuppressionTestSafetyError("test list ID is required")
    if int(group_id) <= 0 or int(sender_id) <= 0:
        raise SuppressionTestSafetyError(
            "positive group and sender IDs are required"
        )
    plan: dict[str, Any] = {
        "schema_version": 1,
        "run_id": run_id,
        "target_account_email": TARGET_ACCOUNT_EMAIL,
        "recipient": normalized,
        "control_recipient": normalized_control,
        "list_id": str(list_id),
        "proof_list": {
            "name": APPROVED_SUPPRESSION_PROOF_LIST_NAME,
            "must_not_exist": False,
        },
        "group": {
            "id": int(group_id),
            "name": PRODUCTION_GROUP_NAME,
        },
        "sender_id": int(sender_id),
    }
    plan["operation_digest"] = _canonical_digest(plan)
    return plan


def build_suppression_setup_plan(
    *,
    run_id: str,
    recipient: str,
    control_recipient: str,
    list_name: str,
    group_id: int,
    sender_id: int,
) -> dict[str, Any]:
    normalized = _allowed_recipient(recipient)
    normalized_control = _allowed_recipient(control_recipient)
    if normalized != PREFERRED_SUPPRESSION_TEST_RECIPIENT:
        raise SuppressionTestSafetyError(
            "setup requires the preferred proof recipient"
        )
    if (
        normalized_control != SUPPRESSION_CONTROL_RECIPIENT
        or normalized_control == normalized
    ):
        raise SuppressionTestSafetyError(
            "setup requires the distinct approved control recipient"
        )
    if list_name != APPROVED_SUPPRESSION_PROOF_LIST_NAME:
        raise SuppressionTestSafetyError(
            "setup requires the approved proof list name"
        )
    if not run_id or "/" in run_id or "\\" in run_id:
        raise SuppressionTestSafetyError("test run_id is unsafe")
    if int(group_id) <= 0 or int(sender_id) <= 0:
        raise SuppressionTestSafetyError(
            "positive group and sender IDs are required"
        )
    plan: dict[str, Any] = {
        "schema_version": 2,
        "run_id": run_id,
        "target_account_email": TARGET_ACCOUNT_EMAIL,
        "recipient": normalized,
        "control_recipient": normalized_control,
        "proof_list": {
            "name": list_name,
            "must_not_exist": True,
        },
        "group": {
            "id": int(group_id),
            "name": PRODUCTION_GROUP_NAME,
        },
        "sender_id": int(sender_id),
        "actions": [
            "create_isolated_proof_list",
            "add_suppressed_and_control_recipients",
            "add_temporary_group_suppression",
            "create_and_schedule_tagged_single_send",
            "verify_positive_control_delivery_stats",
        ],
    }
    plan["operation_digest"] = _canonical_digest(plan)
    return plan


def _parse_time(value: Any, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError as exc:
        raise SuppressionTestSafetyError(
            f"approval {field} is not ISO-8601"
        ) from exc
    if parsed.tzinfo is None:
        raise SuppressionTestSafetyError(
            f"approval {field} must include timezone"
        )
    return parsed.astimezone(timezone.utc)


def _validate_approval(
    plan: dict[str, Any],
    approval: dict[str, Any],
    now: datetime,
) -> None:
    expected = (
        ("approved_by", "JP", "approver"),
        ("statement", SUPPRESSION_TEST_APPROVAL_STATEMENT, "statement"),
        ("target_account_email", plan["target_account_email"], "account"),
        ("recipient", plan["recipient"], "recipient"),
        (
            "control_recipient",
            plan["control_recipient"],
            "control recipient",
        ),
        ("operation_digest", plan["operation_digest"], "operation"),
    )
    expected_fields = list(expected)
    if plan.get("setup_operation_digest"):
        expected_fields.append((
            "setup_operation_digest",
            plan["setup_operation_digest"],
            "setup operation",
        ))
    for field, value, label in expected_fields:
        if approval.get(field) != value:
            raise SuppressionTestSafetyError(
                f"approval {label} does not match test plan"
            )
    approved_at = _parse_time(approval.get("approved_at"), "approved_at")
    expires_at = _parse_time(approval.get("expires_at"), "expires_at")
    if approved_at > now:
        raise SuppressionTestSafetyError("approval time is in the future")
    if expires_at <= now:
        raise SuppressionTestSafetyError("approval is expired")
    if expires_at <= approved_at:
        raise SuppressionTestSafetyError(
            "approval expiry must follow approval time"
        )
    if expires_at - approved_at > timedelta(hours=24):
        raise SuppressionTestSafetyError(
            "approval window exceeds 24 hours"
        )


def _validate_setup_approval(
    plan: dict[str, Any],
    approval: dict[str, Any],
    now: datetime,
) -> None:
    _validate_operation_digest(plan, "setup")
    expected = (
        ("approved_by", "JP", "approver"),
        (
            "statement",
            SUPPRESSION_SETUP_APPROVAL_STATEMENT,
            "statement",
        ),
        ("target_account_email", plan["target_account_email"], "account"),
        ("recipient", plan["recipient"], "recipient"),
        (
            "control_recipient",
            plan["control_recipient"],
            "control recipient",
        ),
        ("operation_digest", plan["operation_digest"], "operation"),
    )
    expected_fields = list(expected)
    if plan.get("setup_operation_digest"):
        expected_fields.append((
            "setup_operation_digest",
            plan["setup_operation_digest"],
            "setup operation",
        ))
    for field, value, label in expected_fields:
        if approval.get(field) != value:
            raise SuppressionTestSafetyError(
                f"setup approval {label} does not match plan"
            )
    approved_at = _parse_time(approval.get("approved_at"), "approved_at")
    expires_at = _parse_time(approval.get("expires_at"), "expires_at")
    if approved_at > now:
        raise SuppressionTestSafetyError(
            "setup approval time is in the future"
        )
    if expires_at <= now:
        raise SuppressionTestSafetyError("setup approval is expired")
    if expires_at <= approved_at:
        raise SuppressionTestSafetyError(
            "setup approval expiry must follow approval time"
        )
    if expires_at - approved_at > timedelta(hours=24):
        raise SuppressionTestSafetyError(
            "setup approval window exceeds 24 hours"
        )


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SuppressionTestSafetyError(
            f"cannot read completed proof file: {path.name}"
        ) from exc
    if not isinstance(value, dict):
        raise SuppressionTestSafetyError(
            f"completed proof file is not an object: {path.name}"
        )
    return value


def _validate_operation_digest(plan: dict[str, Any], label: str) -> None:
    observed = plan.get("operation_digest")
    content = dict(plan)
    content.pop("operation_digest", None)
    if not observed or _canonical_digest(content) != observed:
        raise SuppressionTestSafetyError(f"{label} operation digest is invalid")


def _recovery_cleanup_plan(
    setup_plan: dict[str, Any],
    proof_plan: dict[str, Any],
) -> dict[str, Any]:
    _validate_operation_digest(setup_plan, "setup")
    _validate_operation_digest(proof_plan, "proof")
    recipient = _allowed_recipient(proof_plan.get("recipient"))
    control_recipient = _allowed_recipient(
        proof_plan.get("control_recipient")
    )
    proof_list = proof_plan.get("proof_list") or {}
    list_id = str(proof_plan.get("list_id") or "")
    list_name = str(proof_list.get("name") or "")
    group = proof_plan.get("group") or {}
    if (
        proof_plan.get("setup_operation_digest")
        != setup_plan["operation_digest"]
        or recipient != setup_plan.get("recipient")
        or control_recipient != setup_plan.get("control_recipient")
        or recipient == control_recipient
        or list_name != APPROVED_SUPPRESSION_PROOF_LIST_NAME
        or (setup_plan.get("proof_list") or {}).get("name") != list_name
        or not list_id
        or int(group.get("id", 0)) <= 0
        or group.get("name") != PRODUCTION_GROUP_NAME
        or group != setup_plan.get("group")
    ):
        raise SuppressionTestSafetyError(
            "partial recovery inputs do not match approved setup"
        )
    plan: dict[str, Any] = {
        "schema_version": 2,
        "action": "recover_partial_suppression_proof",
        "target_account_email": TARGET_ACCOUNT_EMAIL,
        "recipient": recipient,
        "control_recipient": control_recipient,
        "group": {
            "id": int(group["id"]),
            "name": PRODUCTION_GROUP_NAME,
        },
        "proof_list": {
            "id": list_id,
            "name": list_name,
            "delete": True,
        },
        "remove_temporary_group_suppression_if_present": True,
        "proof_operation_digest": proof_plan["operation_digest"],
        "setup_operation_digest": setup_plan["operation_digest"],
    }
    plan["operation_digest"] = _canonical_digest(plan)
    return plan


def _validate_recovery_cleanup_plan(
    proof_plan: dict[str, Any],
    plan: dict[str, Any],
) -> None:
    _validate_operation_digest(proof_plan, "proof")
    _validate_operation_digest(plan, "recovery cleanup")
    proof_list = plan.get("proof_list") or {}
    proof_group = proof_plan.get("group") or {}
    if (
        plan.get("schema_version") != 2
        or plan.get("action") != "recover_partial_suppression_proof"
        or plan.get("target_account_email") != TARGET_ACCOUNT_EMAIL
        or _allowed_recipient(plan.get("recipient"))
        != proof_plan.get("recipient")
        or _allowed_recipient(plan.get("control_recipient"))
        != proof_plan.get("control_recipient")
        or plan.get("recipient") == plan.get("control_recipient")
        or plan.get("group") != proof_group
        or proof_group.get("name") != PRODUCTION_GROUP_NAME
        or proof_list.get("id") != proof_plan.get("list_id")
        or proof_list.get("name")
        != APPROVED_SUPPRESSION_PROOF_LIST_NAME
        or proof_list.get("name")
        != (proof_plan.get("proof_list") or {}).get("name")
        or proof_list.get("delete") is not True
        or plan.get("remove_temporary_group_suppression_if_present")
        is not True
        or plan.get("proof_operation_digest")
        != proof_plan["operation_digest"]
        or plan.get("setup_operation_digest")
        != proof_plan.get("setup_operation_digest")
    ):
        raise SuppressionTestSafetyError(
            "partial recovery cleanup plan does not match proof plan"
        )


def _cleanup_plan_from_result(
    proof_plan: dict[str, Any],
    result: dict[str, Any],
) -> dict[str, Any]:
    _validate_operation_digest(proof_plan, "proof")
    cleanup = result.get("cleanup_required") or {}
    recipient = _allowed_recipient(proof_plan.get("recipient"))
    control_recipient = _allowed_recipient(
        proof_plan.get("control_recipient")
    )
    single_send_id = result.get("single_send_id")
    if (
        result.get("operation_digest") != proof_plan["operation_digest"]
        or cleanup.get("remove_temporary_group_suppression") != recipient
        or control_recipient != SUPPRESSION_CONTROL_RECIPIENT
        or recipient == control_recipient
        or cleanup.get("single_send_id") != single_send_id
        or not single_send_id
    ):
        raise SuppressionTestSafetyError(
            "completed proof cleanup requirement does not match proof plan"
        )
    group = proof_plan.get("group") or {}
    if (
        int(group.get("id", 0)) <= 0
        or group.get("name") != PRODUCTION_GROUP_NAME
    ):
        raise SuppressionTestSafetyError(
            "completed proof group does not match production group"
        )
    plan: dict[str, Any] = {
        "schema_version": 1,
        "action": "remove_temporary_group_suppression",
        "target_account_email": TARGET_ACCOUNT_EMAIL,
        "recipient": recipient,
        "control_recipient": control_recipient,
        "group": {
            "id": int(group["id"]),
            "name": PRODUCTION_GROUP_NAME,
        },
        "single_send_id": str(single_send_id),
        "proof_operation_digest": proof_plan["operation_digest"],
    }
    setup_operation_digest = result.get("setup_operation_digest")
    if setup_operation_digest is not None:
        if (
            setup_operation_digest
            != proof_plan.get("setup_operation_digest")
        ):
            raise SuppressionTestSafetyError(
                "completed proof setup digest does not match proof plan"
            )
        plan["setup_operation_digest"] = setup_operation_digest
    proof_list = result.get("proof_list")
    if proof_list is not None:
        list_id = str((proof_list or {}).get("id") or "")
        list_name = str((proof_list or {}).get("name") or "")
        if (
            list_name != APPROVED_SUPPRESSION_PROOF_LIST_NAME
            or not list_id
            or (proof_plan.get("proof_list") or {}).get("name") != list_name
        ):
            raise SuppressionTestSafetyError(
                "completed proof list does not match approved setup"
            )
        plan["action"] = (
            "remove_temporary_group_suppression_and_delete_proof_list"
        )
        plan["proof_list"] = {
            "id": list_id,
            "name": list_name,
            "delete": True,
        }
    plan["operation_digest"] = _canonical_digest(plan)
    return plan


def build_suppression_cleanup_plan(
    proof_plan: dict[str, Any],
    proof_evidence_dir: Path,
) -> dict[str, Any]:
    root = Path(proof_evidence_dir)
    if not (root / "COMPLETE").is_file():
        recovery_path = root / "recovery-cleanup-plan.json"
        if not recovery_path.is_file():
            setup_path = root / "plan.json"
            if not setup_path.is_file():
                raise SuppressionTestSafetyError(
                    "cleanup requires a completed proof or recovery plan"
                )
            recovery = _recovery_cleanup_plan(
                _read_json(setup_path),
                proof_plan,
            )
        else:
            recovery = _read_json(recovery_path)
        _validate_recovery_cleanup_plan(proof_plan, recovery)
        return recovery
    result = _read_json(root / "result.json")
    computed = _cleanup_plan_from_result(proof_plan, result)
    persisted = _read_json(root / "cleanup-plan.json")
    if persisted != computed:
        raise SuppressionTestSafetyError(
            "completed proof cleanup plan differs from proof result"
        )
    return computed


def _validate_cleanup_approval(
    plan: dict[str, Any],
    approval: dict[str, Any],
    now: datetime,
) -> None:
    _validate_operation_digest(plan, "cleanup")
    recipient = _allowed_recipient(plan.get("recipient"))
    if not plan.get("control_recipient"):
        raise SuppressionTestSafetyError(
            "cleanup plan is missing the control recipient"
        )
    control_recipient = _allowed_recipient(
        plan.get("control_recipient")
    )
    if (
        control_recipient != SUPPRESSION_CONTROL_RECIPIENT
        or recipient == control_recipient
    ):
        raise SuppressionTestSafetyError(
            "cleanup control recipient does not match approved role"
        )
    expected = (
        ("approved_by", "JP", "approver"),
        (
            "statement",
            SUPPRESSION_CLEANUP_APPROVAL_STATEMENT,
            "statement",
        ),
        ("target_account_email", plan["target_account_email"], "account"),
        ("recipient", plan["recipient"], "recipient"),
        (
            "control_recipient",
            control_recipient,
            "control recipient",
        ),
        (
            "proof_operation_digest",
            plan["proof_operation_digest"],
            "proof operation",
        ),
        ("operation_digest", plan["operation_digest"], "operation"),
    )
    expected_fields = list(expected)
    if plan.get("setup_operation_digest"):
        expected_fields.append((
            "setup_operation_digest",
            plan["setup_operation_digest"],
            "setup operation",
        ))
    for field, value, label in expected_fields:
        if approval.get(field) != value:
            raise SuppressionTestSafetyError(
                f"cleanup approval {label} does not match plan"
            )
    approved_at = _parse_time(approval.get("approved_at"), "approved_at")
    expires_at = _parse_time(approval.get("expires_at"), "expires_at")
    if approved_at > now:
        raise SuppressionTestSafetyError(
            "cleanup approval time is in the future"
        )
    if expires_at <= now:
        raise SuppressionTestSafetyError("cleanup approval is expired")
    if expires_at <= approved_at:
        raise SuppressionTestSafetyError(
            "cleanup approval expiry must follow approval time"
        )
    if expires_at - approved_at > timedelta(hours=24):
        raise SuppressionTestSafetyError(
            "cleanup approval window exceeds 24 hours"
        )


def run_suppression_cleanup(
    api,
    plan: dict[str, Any],
    approval: dict[str, Any],
    evidence_store,
    *,
    now: datetime | None = None,
    sleep_fn: Callable[[float], None] = time.sleep,
    list_delete_attempts: int = 15,
) -> dict[str, Any]:
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    _validate_cleanup_approval(plan, approval, current)
    if api.user_email() != plan["target_account_email"]:
        raise SuppressionTestSafetyError(
            "SendGrid account does not match cleanup plan"
        )
    group_id = int(plan["group"]["id"])
    group = api.suppression_group(group_id)
    if (
        int(group.get("id", -1)) != group_id
        or group.get("name") != plan["group"]["name"]
    ):
        raise SuppressionTestSafetyError(
            "SendGrid suppression group does not match cleanup plan"
        )
    recipient = plan["recipient"]
    control_recipient = plan["control_recipient"]
    approved_recipients = {recipient, control_recipient}
    recovery_cleanup = (
        plan.get("action") == "recover_partial_suppression_proof"
    )
    suppression_state = api.search_group_suppressions(
        group_id,
        sorted(approved_recipients),
    )
    if control_recipient in suppression_state:
        raise SuppressionTestSafetyError(
            "cleanup control recipient is unexpectedly suppressed"
        )
    suppression_was_present = recipient in suppression_state
    if not recovery_cleanup and not suppression_was_present:
        raise SuppressionTestSafetyError(
            "temporary suppression is not present before cleanup"
        )
    if (
        recovery_cleanup
        and plan.get(
            "remove_temporary_group_suppression_if_present"
        ) is not True
    ):
        raise SuppressionTestSafetyError(
            "partial recovery does not authorize suppression removal"
        )
    proof_list = plan.get("proof_list")
    if proof_list is not None:
        if (
            proof_list.get("name") != APPROVED_SUPPRESSION_PROOF_LIST_NAME
            or proof_list.get("delete") is not True
        ):
            raise SuppressionTestSafetyError(
                "cleanup proof list does not match approved temporary name"
            )
        list_id = str(proof_list.get("id") or "")
        matches = [
            item
            for item in api.marketing_lists()
            if str(item.get("id") or "") == list_id
        ]
        if (
            len(matches) != 1
            or matches[0].get("name") != proof_list["name"]
        ):
            raise SuppressionTestSafetyError(
                "temporary proof list does not match cleanup plan"
            )
        contacts = api.list_contacts(list_id)
        contact_emails = {
            _normalized_email(contact.get("email"))
            for contact in contacts
        }
        membership_is_exact = (
            contact_emails == approved_recipients
            and len(contacts) == 2
        )
        membership_is_approved_subset = (
            contact_emails.issubset(approved_recipients)
            and len(contact_emails) == len(contacts)
        )
        if not membership_is_exact and not (
            recovery_cleanup and membership_is_approved_subset
        ):
            raise SuppressionTestSafetyError(
                "temporary proof list membership is not exact"
            )
    evidence_store.write_json("started.json", {
        "operation_digest": plan["operation_digest"],
        "proof_operation_digest": plan["proof_operation_digest"],
        "group_id": group_id,
        "recipient": recipient,
        "control_recipient": control_recipient,
        "proof_list": proof_list,
        "recovery_cleanup": recovery_cleanup,
    })
    if suppression_was_present:
        api.remove_group_suppression(group_id, recipient)
        if api.search_group_suppressions(group_id, [recipient]):
            raise SuppressionTestSafetyError(
                "temporary suppression is still present after cleanup"
            )
    proof_list_deleted = False
    if proof_list is not None:
        api.delete_list(str(proof_list["id"]))
        for attempt in range(list_delete_attempts):
            still_present = any(
                str(item.get("id") or "") == str(proof_list["id"])
                for item in api.marketing_lists()
            )
            if not still_present:
                break
            if attempt < list_delete_attempts - 1:
                sleep_fn(1.0)
        else:
            raise SuppressionTestSafetyError(
                "temporary proof list is still present after cleanup"
            )
        proof_list_deleted = True
    result = {
        "operation_digest": plan["operation_digest"],
        "proof_operation_digest": plan["proof_operation_digest"],
        "group_id": group_id,
        "recipient": recipient,
        "control_recipient": control_recipient,
        "suppression_removed": suppression_was_present,
    }
    if proof_list is not None:
        result["proof_list_deleted"] = proof_list_deleted
    evidence_store.write_json("result.json", result)
    evidence_store.complete()
    return result


def _stats(payload: dict[str, Any] | None) -> dict[str, int] | None:
    results = (payload or {}).get("results") or []
    if not results:
        return None
    raw = results[0].get("stats") or {}
    return {
        "requests": int(raw.get("requests", 0)),
        "delivered": int(raw.get("delivered", 0)),
        "unique_opens": int(raw.get("unique_opens", 0)),
        "unique_clicks": int(raw.get("unique_clicks", 0)),
    }


def run_suppression_test(
    api,
    plan: dict[str, Any],
    approval: dict[str, Any],
    evidence_store,
    *,
    now: datetime | None = None,
    sleep_fn: Callable[[float], None],
    stats_attempts: int = 30,
    stats_confirmation_attempts: int = 3,
    persist_evidence: bool = True,
) -> dict[str, Any]:
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    _validate_approval(plan, approval, current)
    return _run_suppression_test_authorized(
        api,
        plan,
        evidence_store,
        current=current,
        sleep_fn=sleep_fn,
        stats_attempts=stats_attempts,
        stats_confirmation_attempts=stats_confirmation_attempts,
        persist_evidence=persist_evidence,
    )


def _run_suppression_test_authorized(
    api,
    plan: dict[str, Any],
    evidence_store,
    *,
    current: datetime,
    sleep_fn: Callable[[float], None],
    stats_attempts: int = 30,
    stats_confirmation_attempts: int = 3,
    persist_evidence: bool = True,
) -> dict[str, Any]:
    if api.user_email() != plan["target_account_email"]:
        raise SuppressionTestSafetyError(
            "SendGrid account does not match test plan"
        )

    group_id = int(plan["group"]["id"])
    group = api.suppression_group(group_id)
    if (
        int(group.get("id", -1)) != group_id
        or group.get("name") != plan["group"]["name"]
    ):
        raise SuppressionTestSafetyError(
            "SendGrid suppression group does not match test plan"
        )

    contacts = api.list_contacts(plan["list_id"])
    contact_emails = {
        _normalized_email(contact.get("email"))
        for contact in contacts
    }
    expected_recipients = {
        plan["recipient"],
        plan["control_recipient"],
    }
    if contact_emails != expected_recipients or len(contacts) != 2:
        raise SuppressionTestSafetyError(
            "test list must contain exactly two approved recipients"
        )
    single_send_name = str(
        (plan.get("proof_list") or {}).get("name") or ""
    )
    if single_send_name != APPROVED_SUPPRESSION_PROOF_LIST_NAME:
        raise SuppressionTestSafetyError(
            "test plan does not use the approved proof name"
        )
    if api.search_group_suppressions(
        group_id,
        sorted(expected_recipients),
    ):
        raise SuppressionTestSafetyError(
            "proof requires an unsuppressed control and temporary recipient"
        )

    evidence_store.write_json("started.json", {
        "operation_digest": plan["operation_digest"],
        "recipient": plan["recipient"],
        "control_recipient": plan["control_recipient"],
        "list_id": plan["list_id"],
        "group_id": group_id,
    })
    api.add_group_suppressions(group_id, [plan["recipient"]])
    if api.search_group_suppressions(
        group_id,
        sorted(expected_recipients),
    ) != {plan["recipient"]}:
        raise SuppressionTestSafetyError(
            "recipient suppression and unsuppressed control were not verified"
        )

    payload = {
        "name": single_send_name,
        "send_to": {
            "all": False,
            "list_ids": [plan["list_id"]],
        },
        "email_config": {
            "subject": "TWY SendGrid suppression control test",
            "html_content": (
                "<p>This is the approved positive control message for "
                "TWY newsletter suppression verification.</p>"
            ),
            "plain_content": (
                "This is the approved positive control message for "
                "TWY newsletter suppression verification.\n"
            ),
            "generate_plain_content": False,
            "editor": "code",
            "suppression_group_id": group_id,
            "sender_id": int(plan["sender_id"]),
        },
    }
    created = api.create_single_send(payload)
    single_send_id = created.get("id")
    if not single_send_id:
        raise SuppressionTestSafetyError(
            "suppression test Single Send has no ID"
        )
    api.schedule_single_send(single_send_id, "now")

    if stats_confirmation_attempts < 1:
        raise SuppressionTestSafetyError(
            "suppression stats require at least one confirmation"
        )
    observed: dict[str, int] | None = None
    for attempt in range(stats_attempts):
        observed = _stats(
            api.single_send_stats(
                single_send_id,
                (current - timedelta(days=1)).date().isoformat(),
            )
        )
        if observed:
            if (
                observed["requests"] not in {0, 1}
                or observed["delivered"] not in {0, 1}
                or observed["unique_opens"] not in {0, 1}
                or observed["unique_clicks"] not in {0, 1}
                or observed["delivered"] > observed["requests"]
            ):
                raise SuppressionTestSafetyError(
                    "suppression test stats did not prove enforcement"
                )
            if (
                observed["requests"] == 1
                and observed["delivered"] == 1
            ):
                break
        if attempt < stats_attempts - 1:
            sleep_fn(10.0)
    if (
        observed is None
        or observed["requests"] != 1
        or observed["delivered"] != 1
    ):
        raise SuppressionTestSafetyError(
            "suppression test stats did not prove enforcement"
        )
    for _ in range(stats_confirmation_attempts):
        sleep_fn(10.0)
        confirmed = _stats(
            api.single_send_stats(
                single_send_id,
                (current - timedelta(days=1)).date().isoformat(),
            )
        )
        if (
            confirmed is None
            or confirmed["requests"] != 1
            or confirmed["delivered"] != 1
            or confirmed["unique_opens"] not in {0, 1}
            or confirmed["unique_clicks"] not in {0, 1}
        ):
            raise SuppressionTestSafetyError(
                "suppression test stats confirmation failed"
            )
        observed = confirmed
    enforcement_mode = "positive_control_delivered_and_confirmed"
    if api.search_group_suppressions(
        group_id,
        sorted(expected_recipients),
    ) != {plan["recipient"]}:
        raise SuppressionTestSafetyError(
            "temporary suppression is not still present after stats"
        )

    result = {
        "operation_digest": plan["operation_digest"],
        "single_send_id": single_send_id,
        "stats": observed,
        "enforcement_mode": enforcement_mode,
        "cleanup_required": {
            "remove_temporary_group_suppression": plan["recipient"],
            "single_send_id": single_send_id,
            "cleanup_plan": "cleanup-plan.json",
        },
    }
    cleanup_plan = _cleanup_plan_from_result(plan, result)
    if persist_evidence:
        evidence_store.write_json("result.json", result)
        evidence_store.write_json("cleanup-plan.json", cleanup_plan)
        evidence_store.complete()
    return result


def run_suppression_setup_and_test(
    api,
    plan: dict[str, Any],
    approval: dict[str, Any],
    evidence_store,
    *,
    now: datetime | None = None,
    sleep_fn: Callable[[float], None],
    stats_attempts: int = 30,
    contact_membership_attempts: int = 30,
    contact_membership_poll_seconds: float = 5.0,
) -> dict[str, Any]:
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    _validate_setup_approval(plan, approval, current)
    if (
        contact_membership_attempts < 1
        or contact_membership_poll_seconds <= 0
    ):
        raise SuppressionTestSafetyError(
            "contact membership polling bounds are invalid"
        )
    if plan.get("schema_version") != 2:
        raise SuppressionTestSafetyError(
            "setup plan schema is not supported"
        )
    recipient = _allowed_recipient(plan.get("recipient"))
    control_recipient = _allowed_recipient(
        plan.get("control_recipient")
    )
    if recipient != PREFERRED_SUPPRESSION_TEST_RECIPIENT:
        raise SuppressionTestSafetyError(
            "setup requires the preferred proof recipient"
        )
    if (
        control_recipient != SUPPRESSION_CONTROL_RECIPIENT
        or control_recipient == recipient
    ):
        raise SuppressionTestSafetyError(
            "setup requires the distinct approved control recipient"
        )
    proof_list = plan.get("proof_list") or {}
    list_name = str(proof_list.get("name") or "")
    if (
        list_name != APPROVED_SUPPRESSION_PROOF_LIST_NAME
        or proof_list.get("must_not_exist") is not True
    ):
        raise SuppressionTestSafetyError(
            "setup plan does not use the approved proof list"
        )
    if api.user_email() != plan["target_account_email"]:
        raise SuppressionTestSafetyError(
            "SendGrid account does not match setup plan"
        )
    group_id = int((plan.get("group") or {}).get("id", 0))
    group = api.suppression_group(group_id)
    if (
        int(group.get("id", -1)) != group_id
        or group.get("name") != PRODUCTION_GROUP_NAME
        or plan["group"].get("name") != PRODUCTION_GROUP_NAME
    ):
        raise SuppressionTestSafetyError(
            "SendGrid suppression group does not match setup plan"
        )
    if any(
        item.get("name") == list_name
        for item in api.marketing_lists()
    ):
        raise SuppressionTestSafetyError(
            "approved temporary proof list already exists"
        )
    if api.find_single_send_by_name(list_name) is not None:
        raise SuppressionTestSafetyError(
            "approved temporary proof Single Send already exists"
        )
    if api.search_group_suppressions(
        group_id,
        sorted({recipient, control_recipient}),
    ):
        raise SuppressionTestSafetyError(
            "setup requires both proof recipients to start unsuppressed"
        )
    if (evidence_store.root / "setup-started.json").exists():
        raise SuppressionTestSafetyError(
            "suppression setup evidence already exists"
        )

    evidence_store.write_json("setup-started.json", {
        "operation_digest": plan["operation_digest"],
        "recipient": recipient,
        "control_recipient": control_recipient,
        "proof_list_name": list_name,
        "group_id": group_id,
    })
    created = api.create_list(list_name)
    list_id = str((created or {}).get("id") or "")
    if not list_id or created.get("name") != list_name:
        raise SuppressionTestSafetyError(
            "SendGrid proof list creation did not return the exact object"
        )
    proof_plan = build_suppression_test_plan(
        run_id=str(plan["run_id"]),
        recipient=recipient,
        control_recipient=control_recipient,
        list_id=list_id,
        group_id=group_id,
        sender_id=int(plan["sender_id"]),
    )
    proof_plan["proof_list"] = {
        "name": list_name,
        "must_not_exist": True,
    }
    proof_plan["setup_operation_digest"] = plan["operation_digest"]
    proof_plan["operation_digest"] = _canonical_digest({
        key: value
        for key, value in proof_plan.items()
        if key != "operation_digest"
    })
    recovery_cleanup_plan = _recovery_cleanup_plan(plan, proof_plan)
    evidence_store.write_json("proof-plan.json", proof_plan)
    evidence_store.write_json(
        "recovery-cleanup-plan.json",
        recovery_cleanup_plan,
    )
    evidence_store.write_json("setup-created.json", {
        "operation_digest": plan["operation_digest"],
        "proof_list": {"id": list_id, "name": list_name},
    })
    job_id = api.upsert_contacts(
        [list_id],
        [
            {"email": recipient},
            {"email": control_recipient},
        ],
    )
    api.wait_contact_job(job_id, timeout_s=300)
    expected_members = {recipient, control_recipient}
    membership_is_exact = False
    for attempt in range(contact_membership_attempts):
        contacts = api.list_contacts(list_id)
        contact_emails = {
            _normalized_email(contact.get("email"))
            for contact in contacts
        }
        if (
            not contact_emails.issubset(expected_members)
            or len(contact_emails) != len(contacts)
        ):
            raise SuppressionTestSafetyError(
                "temporary proof list membership is not exact"
            )
        if contact_emails == expected_members and len(contacts) == 2:
            membership_is_exact = True
            break
        if attempt + 1 < contact_membership_attempts:
            sleep_fn(contact_membership_poll_seconds)
    if not membership_is_exact:
        raise SuppressionTestSafetyError(
            "temporary proof list membership is not exact"
        )

    result = _run_suppression_test_authorized(
        api,
        proof_plan,
        evidence_store,
        current=current,
        sleep_fn=sleep_fn,
        stats_attempts=stats_attempts,
        persist_evidence=False,
    )
    result["setup_operation_digest"] = plan["operation_digest"]
    result["proof_list"] = {
        "id": list_id,
        "name": list_name,
    }
    cleanup_plan = _cleanup_plan_from_result(proof_plan, result)
    evidence_store.write_json("result.json", result)
    evidence_store.write_json("cleanup-plan.json", cleanup_plan)
    evidence_store.complete()
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Approval-locked TWY SendGrid suppression proof"
        )
    )
    commands = parser.add_subparsers(dest="command", required=True)
    plan = commands.add_parser(
        "plan",
        help="write one provider-inert setup and proof plan",
    )
    plan.add_argument("--run-id", required=True)
    execute = commands.add_parser(
        "run",
        help="execute one approved setup and suppression proof",
    )
    execute.add_argument("--plan-file", type=Path, required=True)
    execute.add_argument("--approval-file", type=Path, required=True)
    execute.add_argument("--expected-operation-digest", required=True)
    execute.add_argument("--run-id", required=True)
    cleanup = commands.add_parser(
        "cleanup",
        help="remove one approved suppression and temporary proof list",
    )
    cleanup.add_argument("--proof-plan", type=Path, required=True)
    cleanup.add_argument("--proof-evidence-dir", type=Path, required=True)
    cleanup.add_argument("--approval-file", type=Path, required=True)
    cleanup.add_argument("--expected-cleanup-digest", required=True)
    cleanup.add_argument("--cleanup-id", required=True)
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        from sendgrid_migration_evidence import EvidenceStore
        from twy_paths import (
            data_root,
            load_env,
            sendgrid_proof_dir,
            sendgrid_suppression_cleanup_dir,
        )

        if args.command == "plan":
            from sendgrid_campaigns import SendGridRegistry

            root = sendgrid_proof_dir(args.run_id)
            if root.exists() and any(root.iterdir()):
                raise SuppressionTestSafetyError(
                    "suppression proof run already has evidence"
                )
            registry = SendGridRegistry.load(
                data_root() / "sendgrid" / "production_objects.json"
            )
            plan = build_suppression_setup_plan(
                run_id=args.run_id,
                recipient=PREFERRED_SUPPRESSION_TEST_RECIPIENT,
                control_recipient=SUPPRESSION_CONTROL_RECIPIENT,
                list_name=APPROVED_SUPPRESSION_PROOF_LIST_NAME,
                group_id=registry.suppression_group_id,
                sender_id=registry.sender_id,
            )
            destination = EvidenceStore(root).write_json(
                "plan.json",
                plan,
            )
            print(json.dumps({
                "run_id": args.run_id,
                "operation_digest": plan["operation_digest"],
                "plan_file": str(destination),
            }, indent=2, sort_keys=True))
            return 0

        if args.command == "run":
            plan = _read_json(args.plan_file)
            if plan.get("run_id") != args.run_id:
                raise SuppressionTestSafetyError(
                    "run ID does not match setup plan"
                )
            root = sendgrid_proof_dir(args.run_id)
            expected_plan_file = root / "plan.json"
            if args.plan_file.resolve() != expected_plan_file.resolve():
                raise SuppressionTestSafetyError(
                    "setup plan is outside the exact proof evidence directory"
                )
            if (
                plan.get("operation_digest")
                != args.expected_operation_digest
            ):
                raise SuppressionTestSafetyError(
                    "expected operation digest does not match setup plan"
                )
            approval = _read_json(args.approval_file)
            load_env()
            api_key = os.getenv("SENDGRID_API_KEY")
            if not api_key:
                print(
                    "missing required configuration: SENDGRID_API_KEY",
                    file=sys.stderr,
                )
                return 2
            from sendgrid_api import SendGridAPI

            with _exclusive_run_lock(
                data_root()
                / "sendgrid"
                / "suppression_proof_run.lock"
            ):
                result = run_suppression_setup_and_test(
                    SendGridAPI(api_key),
                    plan,
                    approval,
                    EvidenceStore(root),
                    sleep_fn=time.sleep,
                )
            print(json.dumps({
                "operation_digest": result["operation_digest"],
                "setup_operation_digest": result[
                    "setup_operation_digest"
                ],
                "single_send_id": result["single_send_id"],
                "stats": result["stats"],
                "cleanup_required": True,
            }, indent=2, sort_keys=True))
            return 0

        proof_plan = _read_json(args.proof_plan)
        cleanup_plan = build_suppression_cleanup_plan(
            proof_plan,
            args.proof_evidence_dir,
        )
        approval = _read_json(args.approval_file)
        if (
            cleanup_plan["operation_digest"]
            != args.expected_cleanup_digest
        ):
            raise SuppressionTestSafetyError(
                "expected cleanup digest does not match cleanup plan"
            )

        from sendgrid_api import SendGridAPI

        load_env()
        api_key = os.getenv("SENDGRID_API_KEY")
        if not api_key:
            print(
                "missing required configuration: SENDGRID_API_KEY",
                file=sys.stderr,
            )
            return 2
        with _exclusive_run_lock(
            data_root()
            / "sendgrid"
            / "suppression_proof_run.lock"
        ):
            result = run_suppression_cleanup(
                SendGridAPI(api_key),
                cleanup_plan,
                approval,
                EvidenceStore(
                    sendgrid_suppression_cleanup_dir(args.cleanup_id)
                ),
            )
        print(json.dumps({
            "operation_digest": result["operation_digest"],
            "proof_operation_digest": result[
                "proof_operation_digest"
            ],
            "group_id": result["group_id"],
            "suppression_removed": result["suppression_removed"],
        }, indent=2, sort_keys=True))
        return 0
    except SuppressionTestSafetyError as exc:
        print(
            f"suppression proof safety gate blocked: {exc}",
            file=sys.stderr,
        )
        return 3
    except OSError as exc:
        print(
            "suppression proof operational error; provider cleanup may "
            f"be required: {exc}",
            file=sys.stderr,
        )
        return 4


if __name__ == "__main__":
    raise SystemExit(main())
