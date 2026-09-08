"""sendgrid_contact_rename: a member keeps every list, their consent and their
place in a sequence when HeyMarvelous reports them under a new address."""
from datetime import datetime, timezone
import itertools
import json

import pytest

import journey_enrollment
from sendgrid_contact_identity import IDENTITY_FIELD
from sendgrid_contact_rename import (
    COMPLETED,
    MODE_CREATED,
    MODE_MERGED,
    STARTED,
    Rename,
    append_ledger,
    apply_rename,
    plan_renames,
    read_ledger,
)

NOW = datetime(2026, 9, 8, 1, 0, tzinfo=timezone.utc)
GROUP = 35187
FIELD_IDS = {"twy_source": "e3_T", "twy_source_detail": "e4_T"}
IDENTITY_ID = "e5_T"
NAMES_BY_ID = {"e3_T": "twy_source", "e4_T": "twy_source_detail", IDENTITY_ID: IDENTITY_FIELD}


def listed(email, customer_id="", contact_id=None):
    return {
        "email": email,
        "id": contact_id or f"c_{email}",
        "fields": {IDENTITY_FIELD: customer_id},
    }


# --- the planner ------------------------------------------------------------


def test_a_member_nobody_carries_yet_is_not_a_rename():
    plan = plan_renames({"new@example.com": "441"}, [listed("other@example.com", "7")])
    assert plan.renames == [] and plan.duplicates == [] and plan.conflicts == []


def test_the_same_address_under_the_same_id_is_nothing():
    plan = plan_renames({"a@example.com": "441"}, [listed("a@example.com", "441")])
    assert plan.renames == []


def test_a_known_id_under_a_new_address_is_a_rename():
    plan = plan_renames({"b@example.com": "441"}, [listed("a@example.com", "441")])
    assert plan.renames == [Rename("441", "a@example.com", "b@example.com", "c_a@example.com")]


def test_two_contacts_carrying_one_id_are_refused_not_guessed():
    rows = [listed("a@example.com", "441"), listed("c@example.com", "441")]
    plan = plan_renames({"b@example.com": "441"}, rows)
    assert plan.renames == []
    assert plan.duplicates == [("441", ["a@example.com", "c@example.com"])]


def test_an_address_already_carrying_another_id_is_a_conflict():
    plan = plan_renames({"b@example.com": "441"}, [listed("b@example.com", "7"), listed("a@example.com", "441")])
    assert plan.renames == []
    assert plan.conflicts == [("b@example.com", "7", "441")]


def test_a_started_rename_resumes_even_when_both_addresses_carry_the_id(tmp_path):
    started = {
        "event": STARTED, "customer_id": "441",
        "old_email": "a@example.com", "new_email": "b@example.com", "mode": MODE_CREATED,
    }
    rows = [listed("a@example.com", "441"), listed("b@example.com", "441")]

    plan = plan_renames({"b@example.com": "441"}, rows, [started])

    assert len(plan.renames) == 1
    assert plan.renames[0].resume == started
    assert plan.duplicates == []


def test_a_completed_rename_does_not_resume():
    lines = [
        {"event": STARTED, "customer_id": "441", "old_email": "a@example.com", "new_email": "b@example.com"},
        {"event": COMPLETED, "customer_id": "441", "old_email": "a@example.com", "new_email": "b@example.com"},
    ]
    rows = [listed("a@example.com", "441"), listed("b@example.com", "441")]
    plan = plan_renames({"b@example.com": "441"}, rows, lines)
    assert plan.renames == [] and plan.duplicates == [("441", ["a@example.com", "b@example.com"])]


def test_rows_without_an_id_are_invisible_to_the_planner():
    plan = plan_renames({"b@example.com": "441"}, [listed("a@example.com", "")])
    assert plan.renames == []


# --- the executor -----------------------------------------------------------


class FakeAPI:
    """Enough of SendGrid to watch the order and content of every write."""

    def __init__(self, contacts, *, group=(), global_unsubscribed=(), fail_at=None):
        self.contacts = {c["email"]: dict(c) for c in contacts}
        self.group = set(group)
        self.globals = set(global_unsubscribed)
        self.fail_at = fail_at
        self.calls = []

    def contacts_by_emails(self, emails):
        self.calls.append(("contacts_by_emails", list(emails)))
        return {e: dict(self.contacts[e]) for e in emails if e in self.contacts}

    def upsert_contacts(self, list_ids, contacts):
        self.calls.append(("upsert", list(list_ids), [dict(c) for c in contacts]))
        for contact in contacts:
            entry = self.contacts.setdefault(
                contact["email"],
                {"email": contact["email"], "id": f"c_{contact['email']}", "list_ids": [], "custom_fields": {}},
            )
            for name in ("first_name", "last_name"):
                if contact.get(name):
                    entry[name] = contact[name]
            entry["list_ids"] = sorted(set(entry.get("list_ids") or []) | set(list_ids))
            fields = dict(entry.get("custom_fields") or {})
            for field_id, value in (contact.get("custom_fields") or {}).items():
                fields[NAMES_BY_ID[field_id]] = value
            entry["custom_fields"] = fields
        return "job-1"

    def wait_contact_job(self, job_id, timeout_s=120):
        self.calls.append(("wait", job_id))
        return {"status": "completed"}

    def search_group_suppressions(self, group_id, emails):
        return {e for e in emails if e in self.group}

    def add_group_suppressions(self, group_id, emails):
        if self.fail_at == "consent":
            raise RuntimeError("SendGrid said no")
        self.calls.append(("group", group_id, list(emails)))
        self.group.update(emails)

    def get_global_unsubscribe(self, email):
        return {"email": email} if email in self.globals else None

    def add_global_unsubscribes(self, emails):
        self.calls.append(("global", list(emails)))
        self.globals.update(emails)

    def delete_contacts(self, contact_ids):
        self.calls.append(("delete", list(contact_ids)))
        for email, entry in list(self.contacts.items()):
            if entry["id"] in contact_ids:
                del self.contacts[email]
        return "del-1"


def old_contact(**overrides):
    contact = {
        "email": "a@example.com",
        "id": "c_a",
        "first_name": "Ann",
        "last_name": "Member",
        "list_ids": ["members", "subscribed", "habit"],
        "custom_fields": {"twy_source": "member_sync", "twy_source_detail": "Member: Yoga Lifestyle"},
    }
    contact.update(overrides)
    return contact


def run(api, tmp_path, *, rename=None, enrollments=None):
    # A clock that advances ten seconds per read, so a wait that never
    # succeeds times out instead of spinning.
    ticks = itertools.count(0, 10)
    return apply_rename(
        api,
        rename=rename or Rename("441", "a@example.com", "b@example.com", "c_a"),
        suppression_group_id=GROUP,
        enrollments=enrollments,
        ledger_path=tmp_path / "renames.jsonl",
        identity_field_id=IDENTITY_ID,
        field_ids=FIELD_IDS,
        now=NOW,
        sleep=lambda _: None,
        clock=lambda: float(next(ticks)),
    )


def test_the_new_address_gets_every_list_the_name_the_source_and_the_id(tmp_path):
    api = FakeAPI([old_contact()])

    result = run(api, tmp_path)

    upsert = next(call for call in api.calls if call[0] == "upsert")
    assert upsert[1] == ["habit", "members", "subscribed"]
    assert upsert[2] == [{
        "email": "b@example.com",
        "first_name": "Ann",
        "last_name": "Member",
        "custom_fields": {IDENTITY_ID: "441", "e3_T": "member_sync", "e4_T": "Member: Yoga Lifestyle"},
    }]
    assert result["mode"] == MODE_CREATED
    assert "a@example.com" not in api.contacts
    assert api.contacts["b@example.com"]["custom_fields"][IDENTITY_FIELD] == "441"


def test_delete_is_the_last_provider_write(tmp_path):
    api = FakeAPI([old_contact()], group={"a@example.com"})

    run(api, tmp_path)

    writes = [call[0] for call in api.calls if call[0] in ("upsert", "group", "global", "delete")]
    assert writes == ["upsert", "group", "delete"]
    assert api.calls[-1][0] == "contacts_by_emails"


def test_consent_follows_the_person_when_the_address_is_created(tmp_path):
    api = FakeAPI([old_contact()], group={"a@example.com"}, global_unsubscribed={"a@example.com"})

    result = run(api, tmp_path)

    assert "b@example.com" in api.group and "b@example.com" in api.globals
    assert result["consent"] == {"copied": True, "group": True, "global": True}


def test_a_pre_existing_address_keeps_its_own_consent_and_names(tmp_path):
    api = FakeAPI(
        [
            old_contact(),
            {"email": "b@example.com", "id": "c_b", "first_name": "Annie", "list_ids": ["subscribed"], "custom_fields": {"twy_source": "newsletter_signup"}},
        ],
        group={"a@example.com"},
    )

    result = run(api, tmp_path)

    assert result["mode"] == MODE_MERGED
    assert "b@example.com" not in api.group
    merged = api.contacts["b@example.com"]
    assert merged["first_name"] == "Annie" and merged["last_name"] == "Member"
    assert merged["custom_fields"]["twy_source"] == "newsletter_signup"
    assert merged["custom_fields"]["twy_source_detail"] == "Member: Yoga Lifestyle"
    assert merged["list_ids"] == ["habit", "members", "subscribed"]


def test_a_welcome_sequence_in_progress_continues_at_the_new_address(tmp_path):
    connection = journey_enrollment.connect(tmp_path / "journeys.db")
    journey_enrollment.record_enrollments(connection, [journey_enrollment.PlannedEnrollment(
        journey_id="yl", email="a@example.com", customer_id="441", product_id="52025",
        purchase_id="1", enrolled_at="2026-09-01T00:00:00+00:00", next_index=2,
        next_due_at="2026-09-09T00:00:00+00:00",
    )])
    api = FakeAPI([old_contact()])

    result = run(api, tmp_path, enrollments=connection)

    assert result["enrollments"] == {"enrollments_moved": 1, "sends_moved": 0, "kept_existing": 0}
    assert journey_enrollment.enrollment_for(connection, "yl", "b@example.com")["next_index"] == 2
    assert journey_enrollment.enrollment_for(connection, "yl", "a@example.com") is None


def test_the_ledger_opens_before_any_write_and_closes_after_the_delete(tmp_path):
    api = FakeAPI([old_contact()])

    run(api, tmp_path)

    lines = read_ledger(tmp_path / "renames.jsonl")
    assert [line["event"] for line in lines] == [STARTED, COMPLETED]
    assert lines[0]["mode"] == MODE_CREATED
    assert lines[0]["old_list_ids"] == ["habit", "members", "subscribed"]
    assert lines[1]["old_deleted"] is True
    assert lines[1]["new_contact_id"] == "c_b@example.com"
    assert lines[1]["list_ids"] == ["habit", "members", "subscribed"]


def test_a_failure_before_the_delete_leaves_the_old_contact_and_only_started(tmp_path):
    api = FakeAPI([old_contact()], group={"a@example.com"}, fail_at="consent")

    with pytest.raises(RuntimeError, match="said no"):
        run(api, tmp_path)

    assert "a@example.com" in api.contacts
    assert [line["event"] for line in read_ledger(tmp_path / "renames.jsonl")] == [STARTED]


def test_a_resumed_rename_keeps_its_first_mode_and_adds_no_second_started(tmp_path):
    api = FakeAPI([old_contact()], group={"a@example.com"}, fail_at="consent")
    with pytest.raises(RuntimeError):
        run(api, tmp_path)
    api.fail_at = None
    started = read_ledger(tmp_path / "renames.jsonl")[0]

    result = run(api, tmp_path, rename=Rename("441", "a@example.com", "b@example.com", "c_a", resume=started))

    assert result["mode"] == MODE_CREATED
    assert "b@example.com" in api.group
    assert [line["event"] for line in read_ledger(tmp_path / "renames.jsonl")] == [STARTED, COMPLETED]


def test_a_vanished_old_contact_is_skipped_not_invented(tmp_path):
    api = FakeAPI([])
    assert run(api, tmp_path) == {"skipped": "old_missing"}
    assert not (tmp_path / "renames.jsonl").exists()


def test_a_new_address_that_never_reads_back_stops_before_the_delete(tmp_path):
    class Silent(FakeAPI):
        def contacts_by_emails(self, emails):
            found = super().contacts_by_emails(emails)
            found.pop("b@example.com", None)
            return found

    api = Silent([old_contact()])
    with pytest.raises(RuntimeError, match="did not read back"):
        run(api, tmp_path)
    assert "a@example.com" in api.contacts
    assert not any(call[0] == "delete" for call in api.calls)


def test_ledger_lines_round_trip_as_json(tmp_path):
    path = tmp_path / "renames.jsonl"
    append_ledger(path, {"event": STARTED, "customer_id": "1"})
    append_ledger(path, {"event": COMPLETED, "customer_id": "1"})
    assert [json.loads(line)["event"] for line in path.read_text().splitlines()] == [STARTED, COMPLETED]
    assert read_ledger(path)[1]["event"] == COMPLETED
