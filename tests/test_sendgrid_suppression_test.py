from datetime import datetime, timedelta, timezone
import json

import pytest

from sendgrid_migration_evidence import EvidenceStore
from sendgrid_suppression_test import (
    PREFERRED_SUPPRESSION_TEST_RECIPIENT,
    SUPPRESSION_CLEANUP_APPROVAL_STATEMENT,
    SUPPRESSION_TEST_APPROVAL_STATEMENT,
    SuppressionTestSafetyError,
    build_parser,
    build_suppression_cleanup_plan,
    build_suppression_test_plan,
    main,
    run_suppression_cleanup,
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

    def remove_group_suppression(self, group_id, email):
        self.calls.append(("remove_group_suppression", group_id, email))
        self.suppressed.discard(email)

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
    stats_call = next(
        call for call in api.calls if call[0] == "single_send_stats"
    )
    assert stats_call[2] == "2026-07-23"
    assert result["stats"] == {
        "requests": 1,
        "delivered": 0,
        "unique_opens": 0,
        "unique_clicks": 0,
    }
    assert result["cleanup_required"] == {
        "remove_temporary_group_suppression": "admin@tiffanywoodyoga.com",
        "single_send_id": "single-send-1",
        "cleanup_plan": "cleanup-plan.json",
    }
    persisted = json.loads(
        (tmp_path / "evidence" / "result.json").read_text()
    )
    assert persisted["single_send_id"] == "single-send-1"
    cleanup_plan = json.loads(
        (tmp_path / "evidence" / "cleanup-plan.json").read_text()
    )
    assert cleanup_plan["proof_operation_digest"] == plan_for()[
        "operation_digest"
    ]
    assert cleanup_plan["recipient"] == "admin@tiffanywoodyoga.com"
    assert cleanup_plan["operation_digest"]
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


def cleanup_approval_for(plan):
    return {
        "approved_by": "JP",
        "statement": SUPPRESSION_CLEANUP_APPROVAL_STATEMENT,
        "approved_at": NOW.isoformat(),
        "expires_at": (NOW + timedelta(hours=1)).isoformat(),
        "target_account_email": "admin@tiffanywoodyoga.com",
        "recipient": plan["recipient"],
        "proof_operation_digest": plan["proof_operation_digest"],
        "operation_digest": plan["operation_digest"],
    }


def completed_proof_for_cleanup(tmp_path):
    proof_plan = plan_for(PREFERRED_SUPPRESSION_TEST_RECIPIENT)
    api = FakeSuppressionAPI()
    api.contacts = [{"email": PREFERRED_SUPPRESSION_TEST_RECIPIENT}]
    run(
        tmp_path,
        api=api,
        plan=proof_plan,
        approval=approval_for(proof_plan),
    )
    return (
        api,
        build_suppression_cleanup_plan(
            proof_plan,
            tmp_path / "evidence",
        ),
    )


def test_cleanup_plan_requires_completed_proof_and_prefers_jpgan6(tmp_path):
    assert PREFERRED_SUPPRESSION_TEST_RECIPIENT == "jpgan6@gmail.com"
    api, cleanup_plan = completed_proof_for_cleanup(tmp_path)
    assert cleanup_plan["recipient"] == "jpgan6@gmail.com"
    assert cleanup_plan["group"] == {
        "id": 42,
        "name": "TWY Newsletters",
    }
    assert cleanup_plan["single_send_id"] == "single-send-1"
    assert cleanup_plan["proof_operation_digest"]
    assert cleanup_plan["operation_digest"]
    assert api.suppressed == {"jpgan6@gmail.com"}

    (tmp_path / "evidence" / "COMPLETE").unlink()
    with pytest.raises(SuppressionTestSafetyError, match="completed proof"):
        build_suppression_cleanup_plan(
            plan_for(PREFERRED_SUPPRESSION_TEST_RECIPIENT),
            tmp_path / "evidence",
        )


def test_cleanup_requires_a_separate_exact_approval(tmp_path):
    api, cleanup_plan = completed_proof_for_cleanup(tmp_path)
    approval = cleanup_approval_for(cleanup_plan)
    approval["statement"] = SUPPRESSION_TEST_APPROVAL_STATEMENT
    with pytest.raises(SuppressionTestSafetyError, match="statement"):
        run_suppression_cleanup(
            api,
            cleanup_plan,
            approval,
            EvidenceStore(tmp_path / "cleanup-evidence"),
            now=NOW,
        )
    assert not any(
        call[0] == "remove_group_suppression"
        for call in api.calls
    )


def test_cleanup_removes_only_the_proof_suppression_and_verifies_absence(
    tmp_path,
):
    api, cleanup_plan = completed_proof_for_cleanup(tmp_path)
    result = run_suppression_cleanup(
        api,
        cleanup_plan,
        cleanup_approval_for(cleanup_plan),
        EvidenceStore(tmp_path / "cleanup-evidence"),
        now=NOW,
    )

    assert (
        "remove_group_suppression",
        42,
        "jpgan6@gmail.com",
    ) in api.calls
    assert api.suppressed == set()
    assert result == {
        "operation_digest": cleanup_plan["operation_digest"],
        "proof_operation_digest": cleanup_plan["proof_operation_digest"],
        "group_id": 42,
        "recipient": "jpgan6@gmail.com",
        "suppression_removed": True,
    }
    assert (tmp_path / "cleanup-evidence" / "COMPLETE").exists()


def test_cleanup_blocks_wrong_account_group_or_missing_membership(
    tmp_path,
):
    api, cleanup_plan = completed_proof_for_cleanup(tmp_path / "account")
    api.user_email = lambda: "wrong@example.com"
    with pytest.raises(SuppressionTestSafetyError, match="account"):
        run_suppression_cleanup(
            api,
            cleanup_plan,
            cleanup_approval_for(cleanup_plan),
            EvidenceStore(tmp_path / "account-cleanup"),
            now=NOW,
        )
    assert not any(
        call[0] == "remove_group_suppression"
        for call in api.calls
    )

    api, cleanup_plan = completed_proof_for_cleanup(tmp_path / "group")
    api.group["name"] = "Wrong"
    with pytest.raises(SuppressionTestSafetyError, match="group"):
        run_suppression_cleanup(
            api,
            cleanup_plan,
            cleanup_approval_for(cleanup_plan),
            EvidenceStore(tmp_path / "group-cleanup"),
            now=NOW,
        )

    api, cleanup_plan = completed_proof_for_cleanup(tmp_path / "membership")
    api.suppressed.clear()
    with pytest.raises(SuppressionTestSafetyError, match="not present"):
        run_suppression_cleanup(
            api,
            cleanup_plan,
            cleanup_approval_for(cleanup_plan),
            EvidenceStore(tmp_path / "membership-cleanup"),
            now=NOW,
        )


def test_cleanup_cli_requires_proof_approval_digest_and_cleanup_id():
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["cleanup"])
    parsed = parser.parse_args([
        "cleanup",
        "--proof-plan", "/private/proof-plan.json",
        "--proof-evidence-dir", "/private/proof-evidence",
        "--approval-file", "/private/cleanup-approval.json",
        "--expected-cleanup-digest", "a" * 64,
        "--cleanup-id", "cleanup_20260724T210000Z",
    ])
    assert parsed.command == "cleanup"


def test_cleanup_cli_rejects_digest_mismatch_before_provider_access(
    tmp_path, capsys
):
    proof_plan = plan_for(PREFERRED_SUPPRESSION_TEST_RECIPIENT)
    api = FakeSuppressionAPI()
    api.contacts = [{"email": PREFERRED_SUPPRESSION_TEST_RECIPIENT}]
    run(
        tmp_path,
        api=api,
        plan=proof_plan,
        approval=approval_for(proof_plan),
    )
    cleanup_plan = build_suppression_cleanup_plan(
        proof_plan,
        tmp_path / "evidence",
    )
    proof_plan_path = tmp_path / "proof-plan.json"
    approval_path = tmp_path / "cleanup-approval.json"
    proof_plan_path.write_text(json.dumps(proof_plan))
    approval_path.write_text(json.dumps(cleanup_approval_for(cleanup_plan)))

    result = main([
        "cleanup",
        "--proof-plan", str(proof_plan_path),
        "--proof-evidence-dir", str(tmp_path / "evidence"),
        "--approval-file", str(approval_path),
        "--expected-cleanup-digest", "0" * 64,
        "--cleanup-id", "cleanup_20260724T210000Z",
    ])

    assert result == 3
    assert "expected cleanup digest" in capsys.readouterr().err


def test_cleanup_cli_missing_key_fails_before_api_construction(
    tmp_path, monkeypatch, capsys
):
    proof_plan = plan_for(PREFERRED_SUPPRESSION_TEST_RECIPIENT)
    api = FakeSuppressionAPI()
    api.contacts = [{"email": PREFERRED_SUPPRESSION_TEST_RECIPIENT}]
    run(
        tmp_path,
        api=api,
        plan=proof_plan,
        approval=approval_for(proof_plan),
    )
    cleanup_plan = build_suppression_cleanup_plan(
        proof_plan,
        tmp_path / "evidence",
    )
    proof_plan_path = tmp_path / "proof-plan.json"
    approval_path = tmp_path / "cleanup-approval.json"
    proof_plan_path.write_text(json.dumps(proof_plan))
    approval_path.write_text(json.dumps(cleanup_approval_for(cleanup_plan)))
    monkeypatch.setattr("twy_paths.load_env", lambda: None)
    monkeypatch.delenv("SENDGRID_API_KEY", raising=False)

    class MustNotConstruct:
        def __init__(self, *_args, **_kwargs):
            raise AssertionError("SendGridAPI constructed without an API key")

    monkeypatch.setattr("sendgrid_api.SendGridAPI", MustNotConstruct)
    result = main([
        "cleanup",
        "--proof-plan", str(proof_plan_path),
        "--proof-evidence-dir", str(tmp_path / "evidence"),
        "--approval-file", str(approval_path),
        "--expected-cleanup-digest", cleanup_plan["operation_digest"],
        "--cleanup-id", "cleanup_20260724T210000Z",
    ])

    assert result == 2
    assert "missing required configuration" in capsys.readouterr().err
