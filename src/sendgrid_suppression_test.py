"""Separately approved SendGrid suppression-enforcement proof harness."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
from typing import Any, Callable

from sendgrid_campaigns import UNSUBSCRIBE_GROUP_NAME


TARGET_ACCOUNT_EMAIL = "admin@tiffanywoodyoga.com"
PRODUCTION_GROUP_NAME = UNSUBSCRIBE_GROUP_NAME
ALLOWED_RECIPIENTS = {
    "admin@tiffanywoodyoga.com",
    "jpgan6@gmail.com",
}
PREFERRED_SUPPRESSION_TEST_RECIPIENT = "jpgan6@gmail.com"
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
    list_id: str,
    group_id: int,
    sender_id: int,
) -> dict[str, Any]:
    normalized = _allowed_recipient(recipient)
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
        "list_id": str(list_id),
        "group": {
            "id": int(group_id),
            "name": PRODUCTION_GROUP_NAME,
        },
        "sender_id": int(sender_id),
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
        ("operation_digest", plan["operation_digest"], "operation"),
    )
    for field, value, label in expected:
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


def _cleanup_plan_from_result(
    proof_plan: dict[str, Any],
    result: dict[str, Any],
) -> dict[str, Any]:
    _validate_operation_digest(proof_plan, "proof")
    cleanup = result.get("cleanup_required") or {}
    recipient = _allowed_recipient(proof_plan.get("recipient"))
    single_send_id = result.get("single_send_id")
    if (
        result.get("operation_digest") != proof_plan["operation_digest"]
        or cleanup.get("remove_temporary_group_suppression") != recipient
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
        "group": {
            "id": int(group["id"]),
            "name": PRODUCTION_GROUP_NAME,
        },
        "single_send_id": str(single_send_id),
        "proof_operation_digest": proof_plan["operation_digest"],
    }
    plan["operation_digest"] = _canonical_digest(plan)
    return plan


def build_suppression_cleanup_plan(
    proof_plan: dict[str, Any],
    proof_evidence_dir: Path,
) -> dict[str, Any]:
    root = Path(proof_evidence_dir)
    if not (root / "COMPLETE").is_file():
        raise SuppressionTestSafetyError(
            "cleanup requires a completed proof"
        )
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
    _allowed_recipient(plan.get("recipient"))
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
            "proof_operation_digest",
            plan["proof_operation_digest"],
            "proof operation",
        ),
        ("operation_digest", plan["operation_digest"], "operation"),
    )
    for field, value, label in expected:
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
    if api.search_group_suppressions(
        group_id,
        [recipient],
    ) != {recipient}:
        raise SuppressionTestSafetyError(
            "temporary suppression is not present before cleanup"
        )
    evidence_store.write_json("started.json", {
        "operation_digest": plan["operation_digest"],
        "proof_operation_digest": plan["proof_operation_digest"],
        "group_id": group_id,
        "recipient": recipient,
    })
    api.remove_group_suppression(group_id, recipient)
    if api.search_group_suppressions(group_id, [recipient]):
        raise SuppressionTestSafetyError(
            "temporary suppression is still present after cleanup"
        )
    result = {
        "operation_digest": plan["operation_digest"],
        "proof_operation_digest": plan["proof_operation_digest"],
        "group_id": group_id,
        "recipient": recipient,
        "suppression_removed": True,
    }
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
) -> dict[str, Any]:
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    _validate_approval(plan, approval, current)
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
    if contact_emails != {plan["recipient"]} or len(contacts) != 1:
        raise SuppressionTestSafetyError(
            "test list must contain exactly one approved recipient"
        )

    evidence_store.write_json("started.json", {
        "operation_digest": plan["operation_digest"],
        "recipient": plan["recipient"],
        "list_id": plan["list_id"],
        "group_id": group_id,
    })
    api.add_group_suppressions(group_id, [plan["recipient"]])
    if api.search_group_suppressions(
        group_id,
        [plan["recipient"]],
    ) != {plan["recipient"]}:
        raise SuppressionTestSafetyError(
            "recipient suppression membership was not verified"
        )

    payload = {
        "name": f"TWY Suppression Enforcement {plan['run_id']}",
        "send_to": {
            "all": False,
            "list_ids": [plan["list_id"]],
        },
        "email_config": {
            "subject": "TWY SendGrid suppression enforcement test",
            "html_content": (
                "<p>This message must not be delivered. "
                "It verifies newsletter suppression.</p>"
            ),
            "plain_content": (
                "This message must not be delivered. "
                "It verifies newsletter suppression.\n"
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

    observed: dict[str, int] | None = None
    for attempt in range(stats_attempts):
        observed = _stats(
            api.single_send_stats(
                single_send_id,
                (current - timedelta(days=1)).date().isoformat(),
            )
        )
        if observed and observed["requests"] >= 1:
            break
        if attempt < stats_attempts - 1:
            sleep_fn(10.0)
    if observed is None or observed != {
        "requests": 1,
        "delivered": 0,
        "unique_opens": 0,
        "unique_clicks": 0,
    }:
        raise SuppressionTestSafetyError(
            "suppression test stats did not prove enforcement"
        )
    if api.search_group_suppressions(
        group_id,
        [plan["recipient"]],
    ) != {plan["recipient"]}:
        raise SuppressionTestSafetyError(
            "temporary suppression is not still present after stats"
        )

    result = {
        "operation_digest": plan["operation_digest"],
        "single_send_id": single_send_id,
        "stats": observed,
        "cleanup_required": {
            "remove_temporary_group_suppression": plan["recipient"],
            "single_send_id": single_send_id,
            "cleanup_plan": "cleanup-plan.json",
        },
    }
    cleanup_plan = _cleanup_plan_from_result(plan, result)
    evidence_store.write_json("result.json", result)
    evidence_store.write_json("cleanup-plan.json", cleanup_plan)
    evidence_store.complete()
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Approval-locked cleanup for a completed TWY suppression proof"
        )
    )
    commands = parser.add_subparsers(dest="command", required=True)
    cleanup = commands.add_parser(
        "cleanup",
        help="remove one separately approved proof suppression",
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
        from sendgrid_migration_evidence import EvidenceStore
        from twy_paths import (
            load_env,
            sendgrid_suppression_cleanup_dir,
        )

        load_env()
        api_key = os.getenv("SENDGRID_API_KEY")
        if not api_key:
            print(
                "missing required configuration: SENDGRID_API_KEY",
                file=sys.stderr,
            )
            return 2
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
            f"suppression cleanup safety gate blocked: {exc}",
            file=sys.stderr,
        )
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
