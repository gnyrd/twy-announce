from datetime import datetime, timedelta, timezone
import json

import pytest

from sendgrid_migration_evidence import EvidenceStore
from sendgrid_suppression_test import (
    SUPPRESSION_TEST_APPROVAL_STATEMENT,
    SuppressionTestSafetyError,
    build_suppression_test_plan,
    run_suppression_test,
)


NOW = datetime(2026, 7, 24, 20, 0, tzinfo=timezone.utc)


def plan_for(recipient="admin@tiffanywoodyoga.com"):
    return build_suppression_test_plan(
        run_id="suppression_test_20260724T200000Z",
        recipient=recipient,
        list_id="list-test",
        group_id=42,
        sender_id=9423402,
    )


def approval_for(plan):
    return {
        "approved_by": "JP",
        "statement": SUPPRESSION_TEST_APPROVAL_STATEMENT,
        "approved_at": NOW.isoformat(),
        "expires_at": (NOW + timedelta(hours=1)).isoformat(),
        "target_account_email": "admin@tiffanywoodyoga.com",
        "recipient": plan["recipient"],
        "operation_digest": plan["operation_digest"],
    }


def test_plan_accepts_only_explicit_test_recipient_allowlist():
    assert plan_for()["recipient"] == "admin@tiffanywoodyoga.com"
    assert plan_for("jpgan6@gmail.com")["recipient"] == "jpgan6@gmail.com"
    with pytest.raises(SuppressionTestSafetyError, match="allowlist"):
        plan_for("someone@example.com")


def test_plan_is_digest_locked_to_group_list_sender_and_recipient():
    plan = plan_for()
    assert plan["target_account_email"] == "admin@tiffanywoodyoga.com"
    assert plan["group"] == {"id": 42, "name": "TWY Newsletters"}
    assert plan["list_id"] == "list-test"
    assert plan["sender_id"] == 9423402
    assert len(plan["operation_digest"]) == 64
    assert plan_for() == plan


class FakeSuppressionAPI:
    def __init__(self):
        self.calls = []
        self.group = {
            "id": 42,
            "name": "TWY Newsletters",
            "description": "Tiffany Wood Yoga newsletters",
            "is_default": True,
        }
        self.contacts = [{"email": "admin@tiffanywoodyoga.com"}]
        self.suppressed = set()
        self.stats = {
            "results": [{
                "stats": {
                    "requests": 1,
                    "delivered": 0,
                    "unique_opens": 0,
                    "unique_clicks": 0,
                },
            }],
        }

    def user_email(self):
        self.calls.append(("user_email",))
        return "admin@tiffanywoodyoga.com"

    def suppression_group(self, group_id):
        self.calls.append(("suppression_group", group_id))
        return dict(self.group)

    def list_contacts(self, list_id):
        self.calls.append(("list_contacts", list_id))
        return list(self.contacts)

    def add_group_suppressions(self, group_id, emails):
        self.calls.append(("add_group_suppressions", group_id, tuple(emails)))
        self.suppressed.update(emails)

    def search_group_suppressions(self, group_id, emails):
        self.calls.append(("search_group_suppressions", group_id, tuple(emails)))
        return set(emails) & self.suppressed

    def create_single_send(self, payload):
        self.calls.append(("create_single_send", payload))
        return {"id": "single-send-1", "status": "draft"}

    def schedule_single_send(self, single_send_id, send_at):
        self.calls.append(("schedule_single_send", single_send_id, send_at))
        return {"status": "scheduled", "send_at": send_at}

    def single_send_stats(self, single_send_id, start_date):
        self.calls.append(("single_send_stats", single_send_id, start_date))
        return self.stats


def run(tmp_path, api=None, plan=None, approval=None):
    selected_plan = plan or plan_for()
    selected_approval = approval or approval_for(selected_plan)
    return run_suppression_test(
        api or FakeSuppressionAPI(),
        selected_plan,
        selected_approval,
        EvidenceStore(tmp_path / "evidence"),
        now=NOW,
        sleep_fn=lambda _: None,
        stats_attempts=1,
    )


def test_wrong_target_group_or_list_blocks_before_scheduling(tmp_path):
    wrong_account = FakeSuppressionAPI()
    wrong_account.user_email = lambda: "wrong@example.com"
    with pytest.raises(SuppressionTestSafetyError, match="account"):
        run(tmp_path / "account", wrong_account)
    assert not any(
        call[0] == "schedule_single_send"
        for call in wrong_account.calls
    )

    wrong_group = FakeSuppressionAPI()
    wrong_group.group["name"] = "Wrong"
    with pytest.raises(SuppressionTestSafetyError, match="group"):
        run(tmp_path / "group", wrong_group)
    assert not any(
        call[0] == "add_group_suppressions"
        for call in wrong_group.calls
    )

    wrong_list = FakeSuppressionAPI()
    wrong_list.contacts.append({"email": "other@example.com"})
    with pytest.raises(SuppressionTestSafetyError, match="one approved"):
        run(tmp_path / "list", wrong_list)
    assert not any(
        call[0] == "add_group_suppressions"
        for call in wrong_list.calls
    )


def test_approval_must_match_exact_plan_and_recipient(tmp_path):
    plan = plan_for()
    approval = approval_for(plan)
    approval["recipient"] = "jpgan6@gmail.com"
    with pytest.raises(SuppressionTestSafetyError, match="recipient"):
        run(tmp_path, plan=plan, approval=approval)


def test_suppression_is_verified_before_tagged_single_send(tmp_path):
    api = FakeSuppressionAPI()
    result = run(tmp_path, api)
    names = [call[0] for call in api.calls]
    assert names.index("search_group_suppressions") < names.index(
        "create_single_send"
    )
    payload = next(
        call[1] for call in api.calls if call[0] == "create_single_send"
    )
    assert payload["send_to"] == {
        "all": False,
        "list_ids": ["list-test"],
    }
    assert payload["email_config"]["suppression_group_id"] == 42
    assert result["stats"] == {
        "requests": 1,
        "delivered": 0,
        "unique_opens": 0,
        "unique_clicks": 0,
    }
    assert result["cleanup_required"] == {
        "remove_temporary_group_suppression": "admin@tiffanywoodyoga.com",
        "single_send_id": "single-send-1",
    }
    persisted = json.loads(
        (tmp_path / "evidence" / "result.json").read_text()
    )
    assert persisted["single_send_id"] == "single-send-1"
    assert (tmp_path / "evidence" / "COMPLETE").exists()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("delivered", 1),
        ("unique_opens", 1),
        ("unique_clicks", 1),
        ("requests", 0),
    ],
)
def test_any_delivery_or_missing_request_fails_enforcement_proof(
    tmp_path, field, value
):
    api = FakeSuppressionAPI()
    api.stats["results"][0]["stats"][field] = value
    with pytest.raises(SuppressionTestSafetyError, match="stats"):
        run(tmp_path, api)


def test_temporary_suppression_must_still_exist_after_stats(tmp_path):
    api = FakeSuppressionAPI()
    calls = 0

    def disappearing(group_id, emails):
        nonlocal calls
        calls += 1
        return set(emails) if calls == 1 else set()

    api.search_group_suppressions = disappearing
    with pytest.raises(SuppressionTestSafetyError, match="still present"):
        run(tmp_path, api)
