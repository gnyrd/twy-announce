from datetime import datetime, timedelta, timezone
import json

import pytest

from sendgrid_migration_evidence import EvidenceStore
from sendgrid_suppression_test import (
    APPROVED_SUPPRESSION_PROOF_LIST_NAME,
    PREFERRED_SUPPRESSION_TEST_RECIPIENT,
    SUPPRESSION_SETUP_APPROVAL_STATEMENT,
    SUPPRESSION_CLEANUP_APPROVAL_STATEMENT,
    SUPPRESSION_TEST_APPROVAL_STATEMENT,
    SuppressionTestSafetyError,
    _canonical_digest,
    _cleanup_plan_from_result,
    _exclusive_run_lock,
    build_parser,
    build_suppression_cleanup_plan,
    build_suppression_setup_plan,
    build_suppression_test_plan,
    main,
    run_suppression_cleanup,
    run_suppression_setup_and_test,
    run_suppression_test,
)


NOW = datetime(2026, 7, 24, 20, 0, tzinfo=timezone.utc)


def plan_for(recipient="jpgan6@gmail.com"):
    return build_suppression_test_plan(
        run_id="suppression_test_20260724T200000Z",
        recipient=recipient,
        control_recipient="admin@tiffanywoodyoga.com",
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
        "control_recipient": plan["control_recipient"],
        "operation_digest": plan["operation_digest"],
    }


def test_plan_accepts_only_explicit_test_recipient_allowlist():
    assert plan_for()["recipient"] == "jpgan6@gmail.com"
    with pytest.raises(SuppressionTestSafetyError, match="allowlist"):
        plan_for("someone@example.com")
    with pytest.raises(SuppressionTestSafetyError, match="distinct approved"):
        plan_for("admin@tiffanywoodyoga.com")


def test_plan_is_digest_locked_to_group_list_sender_and_recipient():
    plan = plan_for()
    assert plan["target_account_email"] == "admin@tiffanywoodyoga.com"
    assert plan["group"] == {"id": 42, "name": "Email: Unsubscribed"}
    assert plan["list_id"] == "list-test"
    assert plan["sender_id"] == 9423402
    assert len(plan["operation_digest"]) == 64
    assert plan_for() == plan


def test_runtime_plan_binds_the_positive_control_recipient():
    plan = build_suppression_test_plan(
        run_id="suppression_control_20260726T233000Z",
        recipient="jpgan6@gmail.com",
        control_recipient="admin@tiffanywoodyoga.com",
        list_id="list-test",
        group_id=42,
        sender_id=9423402,
    )

    assert plan["recipient"] == "jpgan6@gmail.com"
    assert plan["control_recipient"] == "admin@tiffanywoodyoga.com"


class FakeSuppressionAPI:
    def __init__(self):
        self.calls = []
        self.lists = {}
        self.single_sends = {}
        self.group = {
            "id": 42,
            "name": "Email: Unsubscribed",
            "description": "Tiffany Wood Yoga newsletters",
            "is_default": True,
        }
        self.contacts = [
            {"email": "jpgan6@gmail.com"},
            {"email": "admin@tiffanywoodyoga.com"},
        ]
        self.suppressed = set()
        self.stats = {
            "results": [{
                "stats": {
                    "requests": 1,
                    "delivered": 1,
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

    def marketing_lists(self):
        self.calls.append(("marketing_lists",))
        return [
            {
                "id": list_id,
                "name": name,
                "contact_count": len(self.contacts),
            }
            for list_id, name in self.lists.items()
        ]

    def create_list(self, name):
        self.calls.append(("create_list", name))
        self.lists["proof-list-1"] = name
        return {"id": "proof-list-1", "name": name}

    def delete_list(self, list_id):
        self.calls.append(("delete_list", list_id))
        self.lists.pop(list_id, None)

    def upsert_contacts(self, list_ids, contacts):
        self.calls.append(("upsert_contacts", tuple(list_ids), contacts))
        self.contacts = list(contacts)
        return "contact-job-1"

    def wait_contact_job(self, job_id, timeout_s=120):
        self.calls.append(("wait_contact_job", job_id, timeout_s))
        return {"status": "completed"}

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
        self.single_sends["single-send-1"] = dict(payload)
        return {"id": "single-send-1", "status": "draft"}

    def find_single_send_by_name(self, name):
        self.calls.append(("find_single_send_by_name", name))
        for single_send_id, payload in self.single_sends.items():
            if payload.get("name") == name:
                return {"id": single_send_id, **payload}
        return None

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
    with pytest.raises(SuppressionTestSafetyError, match="two approved"):
        run(tmp_path / "list", wrong_list)
    assert not any(
        call[0] == "add_group_suppressions"
        for call in wrong_list.calls
    )


def test_approval_must_match_exact_plan_and_recipient(tmp_path):
    plan = plan_for()
    approval = approval_for(plan)
    approval["control_recipient"] = "jpgan6@gmail.com"
    with pytest.raises(SuppressionTestSafetyError, match="control"):
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
    assert len([
        call
        for call in api.calls
        if call[0] == "single_send_stats"
    ]) == 4
    assert result["stats"] == {
        "requests": 1,
        "delivered": 1,
        "unique_opens": 0,
        "unique_clicks": 0,
    }
    assert result["cleanup_required"] == {
        "remove_temporary_group_suppression": "jpgan6@gmail.com",
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
    assert cleanup_plan["recipient"] == "jpgan6@gmail.com"
    assert cleanup_plan["control_recipient"] == "admin@tiffanywoodyoga.com"
    assert cleanup_plan["operation_digest"]
    assert (tmp_path / "evidence" / "COMPLETE").exists()


def test_control_must_be_unsuppressed_before_any_proof_mutation(tmp_path):
    api = FakeSuppressionAPI()
    api.suppressed.add("admin@tiffanywoodyoga.com")

    with pytest.raises(SuppressionTestSafetyError, match="unsuppressed control"):
        run(tmp_path, api)

    assert not any(
        call[0] in {
            "add_group_suppressions",
            "create_single_send",
            "schedule_single_send",
        }
        for call in api.calls
    )


def test_missing_approved_single_send_name_blocks_before_suppression(
    tmp_path,
):
    plan = plan_for()
    plan.pop("proof_list", None)
    plan["operation_digest"] = _canonical_digest({
        key: value
        for key, value in plan.items()
        if key != "operation_digest"
    })
    api = FakeSuppressionAPI()

    with pytest.raises(SuppressionTestSafetyError, match="approved proof name"):
        run(
            tmp_path,
            api=api,
            plan=plan,
            approval=approval_for(plan),
        )

    assert not any(
        call[0] == "add_group_suppressions"
        for call in api.calls
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("delivered", 2),
        ("unique_opens", 2),
        ("unique_clicks", 2),
        ("requests", 2),
    ],
)
def test_any_delivery_or_impossible_request_count_fails_enforcement_proof(
    tmp_path, field, value
):
    api = FakeSuppressionAPI()
    api.stats["results"][0]["stats"][field] = value
    with pytest.raises(SuppressionTestSafetyError, match="stats"):
        run(tmp_path, api)


def test_zero_requests_after_full_poll_window_is_inconclusive_and_fails(
    tmp_path,
):
    api = FakeSuppressionAPI()
    api.stats["results"][0]["stats"]["requests"] = 0
    api.stats["results"][0]["stats"]["delivered"] = 0
    sleeps = []

    with pytest.raises(SuppressionTestSafetyError, match="stats"):
        run_suppression_test(
            api,
            plan_for(),
            approval_for(plan_for()),
            EvidenceStore(tmp_path / "evidence"),
            now=NOW,
            sleep_fn=sleeps.append,
            stats_attempts=3,
        )

    assert sleeps == [10.0, 10.0]


def test_persistent_undelivered_control_is_inconclusive_and_fails(
    tmp_path,
):
    api = FakeSuppressionAPI()
    api.stats["results"][0]["stats"]["requests"] = 1
    api.stats["results"][0]["stats"]["delivered"] = 0
    sleeps = []

    with pytest.raises(SuppressionTestSafetyError, match="stats"):
        run_suppression_test(
            api,
            plan_for(),
            approval_for(plan_for()),
            EvidenceStore(tmp_path / "evidence"),
            now=NOW,
            sleep_fn=sleeps.append,
            stats_attempts=3,
        )

    assert sleeps == [10.0, 10.0]


def test_delivery_during_confirmation_fails_enforcement_proof(tmp_path):
    class DelayedDeliveryAPI(FakeSuppressionAPI):
        def __init__(self):
            super().__init__()
            self.stats_calls = 0

        def single_send_stats(self, single_send_id, start_date):
            self.stats_calls += 1
            payload = super().single_send_stats(single_send_id, start_date)
            if self.stats_calls >= 2:
                payload["results"][0]["stats"]["delivered"] = 2
            return payload

    api = DelayedDeliveryAPI()
    sleeps = []

    with pytest.raises(SuppressionTestSafetyError, match="confirmation"):
        run_suppression_test(
            api,
            plan_for(),
            approval_for(plan_for()),
            EvidenceStore(tmp_path / "evidence"),
            now=NOW,
            sleep_fn=sleeps.append,
            stats_attempts=1,
            stats_confirmation_attempts=3,
        )

    assert api.stats_calls == 2
    assert sleeps == [10.0]


def test_temporary_suppression_must_still_exist_after_stats(tmp_path):
    api = FakeSuppressionAPI()
    calls = 0

    def disappearing(group_id, emails):
        nonlocal calls
        calls += 1
        if calls == 2:
            return {"jpgan6@gmail.com"}
        return set()

    api.search_group_suppressions = disappearing
    with pytest.raises(SuppressionTestSafetyError, match="still present"):
        run(tmp_path, api)


def setup_plan_for():
    return build_suppression_setup_plan(
        run_id="suppression_setup_20260726T120000Z",
        recipient=PREFERRED_SUPPRESSION_TEST_RECIPIENT,
        control_recipient="admin@tiffanywoodyoga.com",
        list_name=APPROVED_SUPPRESSION_PROOF_LIST_NAME,
        group_id=42,
        sender_id=9423402,
    )


def setup_approval_for(plan):
    return {
        "approved_by": "JP",
        "statement": SUPPRESSION_SETUP_APPROVAL_STATEMENT,
        "approved_at": NOW.isoformat(),
        "expires_at": (NOW + timedelta(hours=1)).isoformat(),
        "target_account_email": "admin@tiffanywoodyoga.com",
        "recipient": PREFERRED_SUPPRESSION_TEST_RECIPIENT,
        "control_recipient": "admin@tiffanywoodyoga.com",
        "operation_digest": plan["operation_digest"],
    }


def test_setup_plan_locks_approved_name_and_preferred_recipient():
    plan = setup_plan_for()
    assert plan["recipient"] == "jpgan6@gmail.com"
    assert plan["proof_list"] == {
        "name": "Proof: Suppression Enforcement Control: 2026_07_26",
        "must_not_exist": True,
    }
    assert plan["group"] == {"id": 42, "name": "Email: Unsubscribed"}
    with pytest.raises(SuppressionTestSafetyError, match="preferred"):
        build_suppression_setup_plan(
            run_id="wrong_recipient",
            recipient="admin@tiffanywoodyoga.com",
            control_recipient="jpgan6@gmail.com",
            list_name=APPROVED_SUPPRESSION_PROOF_LIST_NAME,
            group_id=42,
            sender_id=9423402,
        )
    with pytest.raises(SuppressionTestSafetyError, match="approved proof list"):
        build_suppression_setup_plan(
            run_id="wrong_name",
            recipient=PREFERRED_SUPPRESSION_TEST_RECIPIENT,
            control_recipient="admin@tiffanywoodyoga.com",
            list_name="Proof: Other: 2026_07_26",
            group_id=42,
            sender_id=9423402,
        )


def test_setup_plan_binds_distinct_suppressed_and_control_recipients():
    plan = build_suppression_setup_plan(
        run_id="suppression_control_20260726T233000Z",
        recipient="jpgan6@gmail.com",
        control_recipient="admin@tiffanywoodyoga.com",
        list_name="Proof: Suppression Enforcement Control: 2026_07_26",
        group_id=42,
        sender_id=9423402,
    )

    assert plan["recipient"] == "jpgan6@gmail.com"
    assert plan["control_recipient"] == "admin@tiffanywoodyoga.com"
    assert plan["proof_list"] == {
        "name": "Proof: Suppression Enforcement Control: 2026_07_26",
        "must_not_exist": True,
    }


def test_setup_and_proof_create_exact_isolated_list_under_one_approval(tmp_path):
    api = FakeSuppressionAPI()
    api.contacts = []
    plan = setup_plan_for()

    result = run_suppression_setup_and_test(
        api,
        plan,
        setup_approval_for(plan),
        EvidenceStore(tmp_path / "evidence"),
        now=NOW,
        sleep_fn=lambda _: None,
        stats_attempts=1,
    )

    assert ("create_list", APPROVED_SUPPRESSION_PROOF_LIST_NAME) in api.calls
    assert (
        "upsert_contacts",
        ("proof-list-1",),
        [
            {"email": "jpgan6@gmail.com"},
            {"email": "admin@tiffanywoodyoga.com"},
        ],
    ) in api.calls
    single_send_payload = next(
        call[1]
        for call in api.calls
        if call[0] == "create_single_send"
    )
    assert (
        single_send_payload["name"]
        == APPROVED_SUPPRESSION_PROOF_LIST_NAME
    )
    assert result["proof_list"] == {
        "id": "proof-list-1",
        "name": APPROVED_SUPPRESSION_PROOF_LIST_NAME,
    }
    cleanup = json.loads(
        (tmp_path / "evidence" / "cleanup-plan.json").read_text()
    )
    assert cleanup["proof_list"] == {
        "id": "proof-list-1",
        "name": APPROVED_SUPPRESSION_PROOF_LIST_NAME,
        "delete": True,
    }
    assert (tmp_path / "evidence" / "COMPLETE").exists()


def test_setup_blocks_existing_proof_name_before_provider_write(tmp_path):
    api = FakeSuppressionAPI()
    api.lists["existing"] = APPROVED_SUPPRESSION_PROOF_LIST_NAME
    plan = setup_plan_for()

    with pytest.raises(SuppressionTestSafetyError, match="already exists"):
        run_suppression_setup_and_test(
            api,
            plan,
            setup_approval_for(plan),
            EvidenceStore(tmp_path / "evidence"),
            now=NOW,
            sleep_fn=lambda _: None,
            stats_attempts=1,
        )

    assert not any(call[0] == "create_list" for call in api.calls)


def test_setup_blocks_existing_proof_single_send_before_provider_write(
    tmp_path,
):
    api = FakeSuppressionAPI()
    api.single_sends["existing"] = {
        "name": APPROVED_SUPPRESSION_PROOF_LIST_NAME,
    }
    plan = setup_plan_for()

    with pytest.raises(SuppressionTestSafetyError, match="Single Send"):
        run_suppression_setup_and_test(
            api,
            plan,
            setup_approval_for(plan),
            EvidenceStore(tmp_path / "evidence"),
            now=NOW,
            sleep_fn=lambda _: None,
            stats_attempts=1,
        )

    assert not any(call[0] == "create_list" for call in api.calls)


def test_setup_blocks_wrong_account_group_and_existing_evidence(
    tmp_path,
):
    plan = setup_plan_for()

    wrong_account = FakeSuppressionAPI()
    wrong_account.user_email = lambda: "wrong@example.com"
    with pytest.raises(SuppressionTestSafetyError, match="account"):
        run_suppression_setup_and_test(
            wrong_account,
            plan,
            setup_approval_for(plan),
            EvidenceStore(tmp_path / "account"),
            now=NOW,
            sleep_fn=lambda _: None,
            stats_attempts=1,
        )
    assert not any(call[0] == "create_list" for call in wrong_account.calls)

    wrong_group = FakeSuppressionAPI()
    wrong_group.group["name"] = "Wrong"
    with pytest.raises(SuppressionTestSafetyError, match="group"):
        run_suppression_setup_and_test(
            wrong_group,
            plan,
            setup_approval_for(plan),
            EvidenceStore(tmp_path / "group"),
            now=NOW,
            sleep_fn=lambda _: None,
            stats_attempts=1,
        )
    assert not any(call[0] == "create_list" for call in wrong_group.calls)

    evidence = EvidenceStore(tmp_path / "existing")
    evidence.write_json("setup-started.json", {"existing": True})
    existing = FakeSuppressionAPI()
    with pytest.raises(SuppressionTestSafetyError, match="evidence"):
        run_suppression_setup_and_test(
            existing,
            plan,
            setup_approval_for(plan),
            evidence,
            now=NOW,
            sleep_fn=lambda _: None,
            stats_attempts=1,
        )
    assert not any(call[0] == "create_list" for call in existing.calls)


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"statement": "WRONG"}, "statement"),
        ({"approved_at": (NOW + timedelta(seconds=1)).isoformat()}, "future"),
        ({"expires_at": NOW.isoformat()}, "expired"),
        (
            {
                "approved_at": NOW.isoformat(),
                "expires_at": (NOW + timedelta(hours=25)).isoformat(),
            },
            "24 hours",
        ),
        ({"operation_digest": "0" * 64}, "operation"),
    ],
)
def test_setup_approval_fail_closed_gates_block_before_provider_access(
    tmp_path,
    change,
    message,
):
    plan = setup_plan_for()
    approval = setup_approval_for(plan)
    approval.update(change)
    api = FakeSuppressionAPI()

    with pytest.raises(SuppressionTestSafetyError, match=message):
        run_suppression_setup_and_test(
            api,
            plan,
            approval,
            EvidenceStore(tmp_path / message.replace(" ", "_")),
            now=NOW,
            sleep_fn=lambda _: None,
            stats_attempts=1,
        )

    assert api.calls == []


def test_setup_rejects_unexpected_post_upsert_membership(tmp_path):
    class UnexpectedMemberAPI(FakeSuppressionAPI):
        def upsert_contacts(self, list_ids, contacts):
            job_id = super().upsert_contacts(list_ids, contacts)
            self.contacts.append({"email": "other@example.com"})
            return job_id

    api = UnexpectedMemberAPI()
    plan = setup_plan_for()
    sleeps = []

    with pytest.raises(
        SuppressionTestSafetyError,
        match="unexpected or duplicate",
    ):
        run_suppression_setup_and_test(
            api,
            plan,
            setup_approval_for(plan),
            EvidenceStore(tmp_path / "evidence"),
            now=NOW,
            sleep_fn=sleeps.append,
            stats_attempts=1,
        )

    assert sleeps == []
    assert not any(
        call[0] in {
            "add_group_suppressions",
            "create_single_send",
            "schedule_single_send",
        }
        for call in api.calls
    )


def test_setup_rejects_duplicate_post_upsert_membership_without_sleep(
    tmp_path,
):
    class DuplicateMemberAPI(FakeSuppressionAPI):
        def upsert_contacts(self, list_ids, contacts):
            job_id = super().upsert_contacts(list_ids, contacts)
            self.contacts.append(dict(self.contacts[0]))
            return job_id

    api = DuplicateMemberAPI()
    plan = setup_plan_for()
    sleeps = []

    with pytest.raises(
        SuppressionTestSafetyError,
        match="unexpected or duplicate",
    ):
        run_suppression_setup_and_test(
            api,
            plan,
            setup_approval_for(plan),
            EvidenceStore(tmp_path / "evidence"),
            now=NOW,
            sleep_fn=sleeps.append,
            stats_attempts=1,
        )

    assert sleeps == []
    assert not any(
        call[0] in {
            "add_group_suppressions",
            "create_single_send",
            "schedule_single_send",
        }
        for call in api.calls
    )


def test_setup_waits_for_delayed_exact_membership_visibility(tmp_path):
    class DelayedMembershipAPI(FakeSuppressionAPI):
        def __init__(self):
            super().__init__()
            self.membership_reads = 0

        def list_contacts(self, list_id):
            self.membership_reads += 1
            if self.membership_reads <= 2:
                self.calls.append(("list_contacts", list_id))
                return []
            return super().list_contacts(list_id)

    api = DelayedMembershipAPI()
    plan = setup_plan_for()
    sleeps = []

    result = run_suppression_setup_and_test(
        api,
        plan,
        setup_approval_for(plan),
        EvidenceStore(tmp_path / "evidence"),
        now=NOW,
        sleep_fn=sleeps.append,
        stats_attempts=1,
        contact_membership_attempts=3,
    )

    assert result["stats"]["requests"] == 1
    assert api.membership_reads >= 3
    assert sleeps[:2] == [5.0, 5.0]


def test_setup_membership_visibility_timeout_is_bounded_and_fails_closed(
    tmp_path,
):
    class InvisibleMembershipAPI(FakeSuppressionAPI):
        def list_contacts(self, list_id):
            self.calls.append(("list_contacts", list_id))
            return []

    api = InvisibleMembershipAPI()
    plan = setup_plan_for()
    sleeps = []

    with pytest.raises(
        SuppressionTestSafetyError,
        match="bounded poll window",
    ):
        run_suppression_setup_and_test(
            api,
            plan,
            setup_approval_for(plan),
            EvidenceStore(tmp_path / "evidence"),
            now=NOW,
            sleep_fn=sleeps.append,
            stats_attempts=1,
            contact_membership_attempts=3,
        )

    assert sleeps == [5.0, 5.0]
    assert not any(
        call[0] in {
            "add_group_suppressions",
            "create_single_send",
            "schedule_single_send",
        }
        for call in api.calls
    )


@pytest.mark.parametrize("failure_point", ["wait_contact_job", "create_single_send"])
def test_partial_setup_persists_digest_locked_recovery_cleanup(
    tmp_path,
    failure_point,
):
    class FailingAPI(FakeSuppressionAPI):
        def wait_contact_job(self, job_id, timeout_s=120):
            if failure_point == "wait_contact_job":
                raise RuntimeError("injected contact job failure")
            return super().wait_contact_job(job_id, timeout_s)

        def create_single_send(self, payload):
            if failure_point == "create_single_send":
                raise RuntimeError("injected Single Send failure")
            return super().create_single_send(payload)

    api = FailingAPI()
    plan = setup_plan_for()
    root = tmp_path / failure_point

    with pytest.raises(RuntimeError, match="injected"):
        run_suppression_setup_and_test(
            api,
            plan,
            setup_approval_for(plan),
            EvidenceStore(root),
            now=NOW,
            sleep_fn=lambda _: None,
            stats_attempts=1,
        )

    proof_plan = json.loads((root / "proof-plan.json").read_text())
    recovery = build_suppression_cleanup_plan(proof_plan, root)
    assert recovery["action"] == "recover_partial_suppression_proof"
    assert recovery["proof_list"] == {
        "id": "proof-list-1",
        "name": APPROVED_SUPPRESSION_PROOF_LIST_NAME,
        "delete": True,
    }
    assert recovery["remove_temporary_group_suppression_if_present"] is True
    assert recovery["setup_operation_digest"] == plan["operation_digest"]
    assert recovery["proof_operation_digest"] == proof_plan[
        "operation_digest"
    ]

    cleanup_result = run_suppression_cleanup(
        api,
        recovery,
        cleanup_approval_for(recovery),
        EvidenceStore(tmp_path / f"{failure_point}_cleanup"),
        now=NOW,
    )
    assert cleanup_result["proof_list_deleted"] is True
    assert cleanup_result["suppression_removed"] == (
        failure_point == "create_single_send"
    )
    assert api.lists == {}
    assert api.suppressed == set()


def cleanup_approval_for(plan):
    approval = {
        "approved_by": "JP",
        "statement": SUPPRESSION_CLEANUP_APPROVAL_STATEMENT,
        "approved_at": NOW.isoformat(),
        "expires_at": (NOW + timedelta(hours=1)).isoformat(),
        "target_account_email": "admin@tiffanywoodyoga.com",
        "recipient": plan["recipient"],
        "control_recipient": plan["control_recipient"],
        "proof_operation_digest": plan["proof_operation_digest"],
        "operation_digest": plan["operation_digest"],
    }
    if plan.get("setup_operation_digest"):
        approval["setup_operation_digest"] = plan[
            "setup_operation_digest"
        ]
    return approval


def completed_proof_for_cleanup(tmp_path):
    proof_plan = plan_for(PREFERRED_SUPPRESSION_TEST_RECIPIENT)
    api = FakeSuppressionAPI()
    api.contacts = [
        {"email": PREFERRED_SUPPRESSION_TEST_RECIPIENT},
        {"email": "admin@tiffanywoodyoga.com"},
    ]
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
        "name": "Email: Unsubscribed",
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


def test_cleanup_missing_control_recipient_fails_cleanly_before_provider_access(
    tmp_path,
):
    api, cleanup_plan = completed_proof_for_cleanup(tmp_path)
    approval = cleanup_approval_for(cleanup_plan)
    cleanup_plan.pop("control_recipient")
    cleanup_plan["operation_digest"] = _canonical_digest({
        key: value
        for key, value in cleanup_plan.items()
        if key != "operation_digest"
    })
    approval.pop("control_recipient")
    approval["operation_digest"] = cleanup_plan["operation_digest"]
    api.calls.clear()

    with pytest.raises(
        SuppressionTestSafetyError,
        match="control recipient",
    ):
        run_suppression_cleanup(
            api,
            cleanup_plan,
            approval,
            EvidenceStore(tmp_path / "cleanup-evidence"),
            now=NOW,
        )

    assert api.calls == []


def test_cleanup_plan_rejects_recipient_outside_proof_allowlist():
    proof_plan = plan_for(PREFERRED_SUPPRESSION_TEST_RECIPIENT)
    proof_plan["recipient"] = "real-subscriber@example.com"
    proof_plan["operation_digest"] = _canonical_digest({
        key: value
        for key, value in proof_plan.items()
        if key != "operation_digest"
    })
    result = {
        "operation_digest": proof_plan["operation_digest"],
        "single_send_id": "single-send-1",
        "cleanup_required": {
            "remove_temporary_group_suppression":
                "real-subscriber@example.com",
            "single_send_id": "single-send-1",
        },
    }
    with pytest.raises(SuppressionTestSafetyError, match="allowlist"):
        _cleanup_plan_from_result(proof_plan, result)


def test_cleanup_execution_rechecks_recipient_allowlist_before_provider_access(
    tmp_path,
):
    _, cleanup_plan = completed_proof_for_cleanup(tmp_path)
    cleanup_plan["recipient"] = "real-subscriber@example.com"
    cleanup_plan["operation_digest"] = _canonical_digest({
        key: value
        for key, value in cleanup_plan.items()
        if key != "operation_digest"
    })
    approval = cleanup_approval_for(cleanup_plan)
    api = FakeSuppressionAPI()
    api.suppressed = {"real-subscriber@example.com"}

    with pytest.raises(SuppressionTestSafetyError, match="allowlist"):
        run_suppression_cleanup(
            api,
            cleanup_plan,
            approval,
            EvidenceStore(tmp_path / "cleanup-evidence"),
            now=NOW,
        )
    assert api.calls == []


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
        "control_recipient": "admin@tiffanywoodyoga.com",
        "suppression_removed": True,
    }
    assert (tmp_path / "cleanup-evidence" / "COMPLETE").exists()


def test_cleanup_deletes_only_digest_locked_temporary_proof_list(tmp_path):
    api = FakeSuppressionAPI()
    api.contacts = []
    setup_plan = setup_plan_for()
    proof_root = tmp_path / "proof"
    run_suppression_setup_and_test(
        api,
        setup_plan,
        setup_approval_for(setup_plan),
        EvidenceStore(proof_root),
        now=NOW,
        sleep_fn=lambda _: None,
        stats_attempts=1,
    )
    proof_plan = json.loads(
        (proof_root / "proof-plan.json").read_text()
    )
    cleanup_plan = build_suppression_cleanup_plan(proof_plan, proof_root)

    result = run_suppression_cleanup(
        api,
        cleanup_plan,
        cleanup_approval_for(cleanup_plan),
        EvidenceStore(tmp_path / "cleanup"),
        now=NOW,
    )

    assert ("delete_list", "proof-list-1") in api.calls
    assert result["proof_list_deleted"] is True
    assert api.lists == {}


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
    planned = parser.parse_args([
        "plan",
        "--run-id", "suppression_proof_20260726T120000Z",
    ])
    assert planned.command == "plan"
    execution = parser.parse_args([
        "run",
        "--plan-file", "/private/plan.json",
        "--approval-file", "/private/approval.json",
        "--expected-operation-digest", "b" * 64,
        "--run-id", "suppression_proof_20260726T120000Z",
    ])
    assert execution.command == "run"


def test_plan_cli_writes_both_approved_recipient_roles(
    tmp_path,
    monkeypatch,
    capsys,
):
    class Registry:
        suppression_group_id = 42
        sender_id = 9423402

    root = tmp_path / "proof"
    monkeypatch.setattr(
        "twy_paths.sendgrid_proof_dir",
        lambda _run_id: root,
    )
    monkeypatch.setattr(
        "twy_paths.data_root",
        lambda: tmp_path,
    )
    monkeypatch.setattr(
        "sendgrid_campaigns.SendGridRegistry.load",
        lambda _path: Registry(),
    )

    result = main([
        "plan",
        "--run-id", "suppression_control_20260726T233000Z",
    ])

    assert result == 0
    plan = json.loads((root / "plan.json").read_text())
    assert plan["recipient"] == "jpgan6@gmail.com"
    assert plan["control_recipient"] == "admin@tiffanywoodyoga.com"
    assert "operation_digest" in json.loads(capsys.readouterr().out)


def test_cleanup_cli_rejects_digest_mismatch_before_provider_access(
    tmp_path, capsys
):
    proof_plan = plan_for(PREFERRED_SUPPRESSION_TEST_RECIPIENT)
    api = FakeSuppressionAPI()
    api.contacts = [
        {"email": PREFERRED_SUPPRESSION_TEST_RECIPIENT},
        {"email": "admin@tiffanywoodyoga.com"},
    ]
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


def test_setup_run_cli_rejects_digest_mismatch_before_provider_access(
    tmp_path,
    monkeypatch,
    capsys,
):
    root = tmp_path / "proof"
    root.mkdir()
    plan = setup_plan_for()
    plan_path = root / "plan.json"
    approval_path = tmp_path / "approval.json"
    plan_path.write_text(json.dumps(plan))
    approval_path.write_text(json.dumps(setup_approval_for(plan)))
    monkeypatch.setattr(
        "twy_paths.sendgrid_proof_dir",
        lambda _run_id: root,
    )
    monkeypatch.setattr(
        "twy_paths.load_env",
        lambda: pytest.fail("load_env must not run"),
    )

    result = main([
        "run",
        "--plan-file", str(plan_path),
        "--approval-file", str(approval_path),
        "--expected-operation-digest", "0" * 64,
        "--run-id", plan["run_id"],
    ])

    assert result == 3
    assert "expected operation digest" in capsys.readouterr().err


def test_setup_and_cleanup_share_one_nonblocking_process_lock(tmp_path):
    lock_path = tmp_path / "suppression_proof_run.lock"

    with _exclusive_run_lock(lock_path):
        with pytest.raises(SuppressionTestSafetyError, match="active"):
            with _exclusive_run_lock(lock_path):
                pytest.fail("concurrent lock unexpectedly acquired")

    with _exclusive_run_lock(lock_path):
        pass


def test_operational_error_is_not_reported_as_a_safety_gate(
    monkeypatch,
    capsys,
):
    def fail_path(_run_id):
        raise OSError("injected filesystem failure")

    monkeypatch.setattr("twy_paths.sendgrid_proof_dir", fail_path)

    result = main([
        "plan",
        "--run-id", "suppression_proof_20260726T120000Z",
    ])

    assert result == 4
    error = capsys.readouterr().err
    assert "operational error" in error
    assert "cleanup may be required" in error
    assert "safety gate blocked" not in error


def test_cleanup_cli_missing_key_fails_before_api_construction(
    tmp_path, monkeypatch, capsys
):
    proof_plan = plan_for(PREFERRED_SUPPRESSION_TEST_RECIPIENT)
    api = FakeSuppressionAPI()
    api.contacts = [
        {"email": PREFERRED_SUPPRESSION_TEST_RECIPIENT},
        {"email": "admin@tiffanywoodyoga.com"},
    ]
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
