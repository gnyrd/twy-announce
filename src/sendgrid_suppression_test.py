"""Separately approved SendGrid suppression-enforcement proof harness."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
from typing import Any, Callable


TARGET_ACCOUNT_EMAIL = "admin@tiffanywoodyoga.com"
PRODUCTION_GROUP_NAME = "TWY Newsletters"
ALLOWED_RECIPIENTS = {
    "admin@tiffanywoodyoga.com",
    "jpgan6@gmail.com",
}
SUPPRESSION_TEST_APPROVAL_STATEMENT = (
    "APPROVE TWY SENDGRID SUPPRESSION ENFORCEMENT TEST"
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


def build_suppression_test_plan(
    *,
    run_id: str,
    recipient: str,
    list_id: str,
    group_id: int,
    sender_id: int,
) -> dict[str, Any]:
    normalized = _normalized_email(recipient)
    if normalized not in ALLOWED_RECIPIENTS:
        raise SuppressionTestSafetyError(
            "test recipient is outside the explicit allowlist"
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
                current.date().isoformat(),
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
        },
    }
    evidence_store.write_json("result.json", result)
    evidence_store.complete()
    return result
