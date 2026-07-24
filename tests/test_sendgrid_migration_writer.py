import copy
from datetime import datetime, timedelta, timezone
import hashlib
import json

import pytest

from sendgrid_migration_writer import (
    APPROVAL_STATEMENT,
    TARGET_ACCOUNT_EMAIL,
    WriterSafetyError,
    apply_operation_plan,
    build_parser,
    build_operation_plan,
    load_completed_evidence,
    main,
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


def test_completed_evidence_rejects_unexpected_deliverable_fields(tmp_path):
    root = completed_evidence(tmp_path)
    deliverable = json.loads((root / "deliverable_contacts.json").read_text())
    deliverable[0]["phone_number"] = "+15555550123"
    _write_json(root / "deliverable_contacts.json", deliverable)
    with pytest.raises(WriterSafetyError, match="deliverable"):
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


class FakeWriterAPI:
    def __init__(self):
        self.calls = []
        self.account_email = TARGET_ACCOUNT_EMAIL
        self.lists = []
        self.fields = [
            {"id": "_rf0_T", "name": "first_name", "field_type": "Text"},
            {"id": "_rf1_T", "name": "last_name", "field_type": "Text"},
        ]
        self.groups = []
        self.suppressed = set()
        self.contacts = {}
        self.jobs = {}

    def user_email(self):
        self.calls.append(("user_email",))
        return self.account_email

    def marketing_lists(self):
        self.calls.append(("marketing_lists",))
        return copy.deepcopy(self.lists)

    def create_list(self, name):
        self.calls.append(("create_list", name))
        row = {"id": f"list-{len(self.lists) + 1}", "name": name}
        self.lists.append(row)
        return copy.deepcopy(row)

    def field_definitions(self):
        self.calls.append(("field_definitions",))
        return copy.deepcopy(self.fields)

    def create_field_definition(self, name, field_type):
        self.calls.append(("create_field_definition", name, field_type))
        row = {
            "id": f"w{len(self.fields) + 1}",
            "name": name,
            "field_type": field_type,
        }
        self.fields.append(row)
        return copy.deepcopy(row)

    def suppression_groups(self):
        self.calls.append(("suppression_groups",))
        return copy.deepcopy(self.groups)

    def create_suppression_group(self, name, description, is_default):
        self.calls.append((
            "create_suppression_group",
            name,
            description,
            is_default,
        ))
        row = {
            "id": 42,
            "name": name,
            "description": description,
            "is_default": is_default,
        }
        self.groups.append(row)
        return copy.deepcopy(row)

    def suppression_group(self, group_id):
        self.calls.append(("suppression_group", group_id))
        return copy.deepcopy(next(
            row for row in self.groups if row["id"] == group_id
        ))

    def add_group_suppressions(self, group_id, emails):
        self.calls.append(("add_group_suppressions", group_id, tuple(emails)))
        self.suppressed.update(emails)

    def search_group_suppressions(self, group_id, emails):
        self.calls.append(("search_group_suppressions", group_id, tuple(emails)))
        return set(emails) & self.suppressed

    def upsert_contacts(self, list_ids, contacts):
        self.calls.append((
            "upsert_contacts",
            tuple(list_ids),
            tuple(row["email"] for row in contacts),
        ))
        for row in contacts:
            stored = copy.deepcopy(row)
            stored["list_ids"] = list(list_ids)
            self.contacts[row["email"]] = stored
        job_id = f"job-{len(self.jobs) + 1}"
        self.jobs[job_id] = len(contacts)
        return job_id

    def wait_contact_job(self, job_id):
        self.calls.append(("wait_contact_job", job_id))
        count = self.jobs[job_id]
        return {
            "status": "completed",
            "results": {
                "requested_count": count,
                "created_count": count,
                "updated_count": 0,
                "errored_count": 0,
            },
        }

    def contacts_by_emails(self, emails):
        self.calls.append(("contacts_by_emails", tuple(emails)))
        return {
            email: copy.deepcopy(self.contacts[email])
            for email in emails
            if email in self.contacts
        }


def _apply(tmp_path, api=None):
    evidence = completed_evidence(tmp_path)
    plan = build_operation_plan(evidence)
    now = datetime(2026, 7, 24, 19, 0, tzinfo=timezone.utc)
    approval = valid_approval(plan, now)
    result = apply_operation_plan(
        api or FakeWriterAPI(),
        plan,
        approval,
        evidence,
        tmp_path / "cleaned_denylist.json",
        tmp_path / "apply_report",
        now=now,
    )
    return result


def test_apply_rejects_wrong_target_before_any_mutation(tmp_path):
    api = FakeWriterAPI()
    api.account_email = "wrong@example.com"
    with pytest.raises(WriterSafetyError, match="target account"):
        _apply(tmp_path, api)
    assert api.calls == [("user_email",)]


def test_apply_rejects_conflicting_denylist_before_provider_mutation(tmp_path):
    api = FakeWriterAPI()
    evidence = completed_evidence(tmp_path)
    plan = build_operation_plan(evidence)
    now = datetime(2026, 7, 24, 19, 0, tzinfo=timezone.utc)
    denylist = tmp_path / "cleaned_denylist.json"
    _write_json(denylist, [{"email": "different@example.com"}])
    with pytest.raises(WriterSafetyError, match="differs"):
        apply_operation_plan(
            api,
            plan,
            valid_approval(plan, now),
            evidence,
            denylist,
            tmp_path / "apply_report",
            now=now,
        )
    assert api.calls == []


def test_apply_rejects_duplicate_exact_resource_names(tmp_path):
    api = FakeWriterAPI()
    api.lists = [
        {"id": "one", "name": "TWY Marketing"},
        {"id": "two", "name": "TWY Marketing"},
    ]
    with pytest.raises(WriterSafetyError, match="duplicate.*TWY Marketing"):
        _apply(tmp_path, api)
    assert not any(call[0] == "create_list" for call in api.calls)


def test_apply_rejects_wrong_custom_field_type(tmp_path):
    api = FakeWriterAPI()
    api.fields.append({
        "id": "w1",
        "name": "twy_status",
        "field_type": "Date",
    })
    with pytest.raises(WriterSafetyError, match="twy_status.*type"):
        _apply(tmp_path, api)
    assert not any(call[0] == "upsert_contacts" for call in api.calls)


def test_apply_rejects_group_that_cannot_be_verified_by_returned_id(tmp_path):
    api = FakeWriterAPI()

    def wrong_group(group_id):
        api.calls.append(("suppression_group", group_id))
        return {
            "id": group_id,
            "name": "Wrong",
            "description": "Wrong",
            "is_default": True,
        }

    api.suppression_group = wrong_group
    with pytest.raises(WriterSafetyError, match="suppression group"):
        _apply(tmp_path, api)
    assert not any(call[0] == "add_group_suppressions" for call in api.calls)


def test_apply_orders_suppression_and_denylist_before_contact_upsert(tmp_path):
    api = FakeWriterAPI()
    evidence = completed_evidence(tmp_path)
    plan = build_operation_plan(evidence)
    now = datetime(2026, 7, 24, 19, 0, tzinfo=timezone.utc)
    denylist = tmp_path / "cleaned_denylist.json"
    result = apply_operation_plan(
        api,
        plan,
        valid_approval(plan, now),
        evidence,
        denylist,
        tmp_path / "apply_report",
        now=now,
    )
    operation_names = [call[0] for call in api.calls]
    assert operation_names.index("add_group_suppressions") < (
        operation_names.index("upsert_contacts")
    )
    assert operation_names.index("search_group_suppressions") < (
        operation_names.index("upsert_contacts")
    )
    assert denylist.stat().st_mode & 0o777 == 0o600
    assert json.loads(denylist.read_text())[0]["email"] == "bad@example.com"
    upsert_call = next(call for call in api.calls if call[0] == "upsert_contacts")
    assert set(upsert_call[1]) == {"list-1", "list-2"}
    stored = api.contacts["active@example.com"]
    assert stored["first_name"] == "Active"
    assert stored["custom_fields"]
    assert "twy_status" not in stored["custom_fields"]
    assert result["counts"] == plan["counts"]
    assert result["postconditions"] == {
        "cleaned_denylist_written": True,
        "contacts_verified": 1,
        "group_suppressions_verified": 1,
    }
    assert (tmp_path / "apply_report" / "COMPLETE").exists()


def test_apply_can_repeat_with_existing_resources_and_matching_denylist(tmp_path):
    api = FakeWriterAPI()
    evidence = completed_evidence(tmp_path)
    plan = build_operation_plan(evidence)
    now = datetime(2026, 7, 24, 19, 0, tzinfo=timezone.utc)
    approval = valid_approval(plan, now)
    denylist = tmp_path / "cleaned_denylist.json"
    apply_operation_plan(
        api,
        plan,
        approval,
        evidence,
        denylist,
        tmp_path / "apply_report_1",
        now=now,
    )
    api.calls.clear()

    result = apply_operation_plan(
        api,
        plan,
        approval,
        evidence,
        denylist,
        tmp_path / "apply_report_2",
        now=now,
    )

    assert not any(
        call[0] in {
            "create_list",
            "create_field_definition",
            "create_suppression_group",
        }
        for call in api.calls
    )
    assert result["postconditions"]["contacts_verified"] == 1
    assert (tmp_path / "apply_report_2" / "COMPLETE").exists()


def test_apply_rejects_reused_completed_report_before_provider_access(tmp_path):
    api = FakeWriterAPI()
    evidence = completed_evidence(tmp_path)
    plan = build_operation_plan(evidence)
    now = datetime(2026, 7, 24, 19, 0, tzinfo=timezone.utc)
    report = tmp_path / "apply_report"
    report.mkdir()
    (report / "COMPLETE").write_text("complete\n")

    with pytest.raises(WriterSafetyError, match="already exists"):
        apply_operation_plan(
            api,
            plan,
            valid_approval(plan, now),
            evidence,
            tmp_path / "cleaned_denylist.json",
            report,
            now=now,
        )
    assert api.calls == []


def test_apply_rejects_reused_incomplete_report_before_provider_access(
    tmp_path,
):
    api = FakeWriterAPI()
    evidence = completed_evidence(tmp_path)
    plan = build_operation_plan(evidence)
    now = datetime(2026, 7, 24, 19, 0, tzinfo=timezone.utc)
    report = tmp_path / "apply_report"
    report.mkdir()
    _write_json(report / "started.json", {
        "operation_digest": plan["operation_digest"],
    })

    with pytest.raises(WriterSafetyError, match="already exists"):
        apply_operation_plan(
            api,
            plan,
            valid_approval(plan, now),
            evidence,
            tmp_path / "cleaned_denylist.json",
            report,
            now=now,
        )
    assert api.calls == []


def test_apply_rejects_missing_suppression_postcondition_before_upsert(tmp_path):
    api = FakeWriterAPI()
    api.search_group_suppressions = lambda group_id, emails: set()
    with pytest.raises(WriterSafetyError, match="suppression postcondition"):
        _apply(tmp_path, api)
    assert not any(call[0] == "upsert_contacts" for call in api.calls)


def test_apply_rejects_failed_contact_job(tmp_path):
    api = FakeWriterAPI()

    def failed_job(job_id):
        api.calls.append(("wait_contact_job", job_id))
        return {
            "status": "completed",
            "results": {
                "requested_count": 1,
                "errored_count": 1,
            },
        }

    api.wait_contact_job = failed_job
    with pytest.raises(WriterSafetyError, match="contact import"):
        _apply(tmp_path, api)


def test_cli_apply_requires_every_approval_and_digest_input():
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["apply"])
    parsed = parser.parse_args([
        "apply",
        "--evidence-dir", "/private/evidence",
        "--operation-plan", "/private/plan.json",
        "--approval-file", "/private/approval.json",
        "--expected-plan-digest", "a" * 64,
        "--apply-id", "apply_20260724T190000Z",
    ])
    assert parsed.command == "apply"


def test_cli_plan_has_no_apply_or_send_side_effect_options():
    parser = build_parser()
    parsed = parser.parse_args([
        "plan",
        "--evidence-dir", "/private/evidence",
        "--operation-plan", "/private/plan.json",
    ])
    assert parsed.command == "plan"
    plan_parser = next(
        action
        for action in parser._subparsers._group_actions
    ).choices["plan"]
    options = {
        option
        for action in plan_parser._actions
        for option in action.option_strings
    }
    assert "--approval-file" not in options
    assert "--send" not in options


def test_cli_main_rejects_expected_digest_mismatch(tmp_path, capsys):
    evidence = completed_evidence(tmp_path)
    plan = build_operation_plan(evidence)
    operation_plan = tmp_path / "operation-plan.json"
    approval_file = tmp_path / "approval.json"
    _write_json(operation_plan, plan)
    _write_json(
        approval_file,
        valid_approval(
            plan,
            datetime(2026, 7, 24, 19, 0, tzinfo=timezone.utc),
        ),
    )

    result = main([
        "apply",
        "--evidence-dir", str(evidence),
        "--operation-plan", str(operation_plan),
        "--approval-file", str(approval_file),
        "--expected-plan-digest", "0" * 64,
        "--apply-id", "apply_20260724T190000Z",
    ])

    assert result == 3
    assert "expected plan digest" in capsys.readouterr().err


def test_cli_main_missing_key_fails_before_api_construction(
    tmp_path, monkeypatch, capsys
):
    evidence = completed_evidence(tmp_path)
    plan = build_operation_plan(evidence)
    operation_plan = tmp_path / "operation-plan.json"
    approval_file = tmp_path / "approval.json"
    _write_json(operation_plan, plan)
    _write_json(
        approval_file,
        valid_approval(
            plan,
            datetime(2026, 7, 24, 19, 0, tzinfo=timezone.utc),
        ),
    )
    monkeypatch.setattr("twy_paths.load_env", lambda: None)
    monkeypatch.delenv("SENDGRID_API_KEY", raising=False)

    class MustNotConstruct:
        def __init__(self, *_args, **_kwargs):
            raise AssertionError("SendGridAPI constructed without an API key")

    monkeypatch.setattr("sendgrid_api.SendGridAPI", MustNotConstruct)
    result = main([
        "apply",
        "--evidence-dir", str(evidence),
        "--operation-plan", str(operation_plan),
        "--approval-file", str(approval_file),
        "--expected-plan-digest", plan["operation_digest"],
        "--apply-id", "apply_20260724T190000Z",
    ])

    assert result == 2
    assert "missing required configuration" in capsys.readouterr().err


def test_writer_source_has_no_global_suppression_send_or_delete_endpoint():
    source = (
        __import__("pathlib").Path(__file__).parents[1]
        / "src"
        / "sendgrid_migration_writer.py"
    ).read_text()
    assert "/asm/suppressions/global" not in source
    assert "/mail/send" not in source
    assert "\"DELETE\"" not in source
