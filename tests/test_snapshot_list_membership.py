import json

import pytest

from snapshot_list_membership import (
    build_snapshot,
    export_rows,
    membership_delta,
    write_snapshot,
)


class FakeAPI:
    def __init__(self, contacts, *, contact_count=None, fields=None):
        self.contacts = contacts
        self.contact_count = (
            len(contacts) if contact_count is None else contact_count
        )
        self.fields = (
            fields
            if fields is not None
            else [
                {"id": "e1_T", "name": "twy_source"},
                {"id": "e2_T", "name": "twy_source_detail"},
            ]
        )
        self.lookups = []

    def start_contact_export(self, list_ids):
        assert list_ids is None
        return {"id": "export-1"}

    def wait_contact_export(self, export_id, timeout_s=600):
        return {"urls": ["u1"], "contact_count": self.contact_count}

    def download_contact_export(self, url):
        header = "EMAIL,CREATED_AT,CONTACT_ID\n"
        body = "".join(
            f"{c['email']},{c['created_at']},{c['id']}\n" for c in self.contacts
        )
        return (header + body).encode("utf-8")

    def field_definitions(self):
        return list(self.fields)

    def contacts_by_emails(self, emails):
        self.lookups.append(list(emails))
        return {
            c["email"]: {
                "id": c["id"],
                "created_at": c["created_at"],
                "list_ids": c.get("list_ids", []),
                "custom_fields": c.get("custom_fields", {}),
            }
            for c in self.contacts
            if c["email"] in emails
        }

    def marketing_lists(self):
        return [
            {"id": "L1", "name": "Email: Subscribed"},
            {"id": "L2", "name": "Yoga Habit: Registered: 2026_08"},
        ]


def contact(n, **overrides):
    values = {
        "id": f"c{n}",
        "email": f"p{n}@example.com",
        "created_at": "2026-08-01T00:00:00Z",
        "list_ids": ["L1"],
        "custom_fields": {},
    }
    values.update(overrides)
    return values


def test_snapshot_records_lists_source_and_creation_per_contact():
    api = FakeAPI(
        [
            contact(
                1,
                list_ids=["L2", "L1"],
                custom_fields={
                    "e1_T": "habit_signup",
                    "e2_T": "utm_source=ig",
                },
            )
        ]
    )

    snapshot = build_snapshot(
        api, captured_at="2026-08-30T12:00:00+00:00", snapshot_date="2026-08-30"
    )

    assert snapshot["contact_count"] == 1
    assert snapshot["contacts"] == [
        {
            "id": "c1",
            "created_at": "2026-08-01T00:00:00Z",
            "list_ids": ["L1", "L2"],
            "source": "habit_signup",
            "source_detail": "utm_source=ig",
        }
    ]
    assert snapshot["lists"]["L1"] == "Email: Subscribed"


def test_snapshot_stores_no_email_addresses():
    api = FakeAPI([contact(1)])
    snapshot = build_snapshot(
        api, captured_at="2026-08-30T12:00:00+00:00", snapshot_date="2026-08-30"
    )
    assert "example.com" not in json.dumps(snapshot)


def test_a_truncated_export_is_refused_rather_than_recorded_as_departures():
    api = FakeAPI([contact(1)], contact_count=900)
    with pytest.raises(ValueError, match="incomplete"):
        export_rows(api)


def test_lookups_are_chunked_at_one_hundred():
    api = FakeAPI([contact(n) for n in range(250)])
    build_snapshot(
        api, captured_at="2026-08-30T12:00:00+00:00", snapshot_date="2026-08-30"
    )
    assert [len(chunk) for chunk in api.lookups] == [100, 100, 50]


def test_contact_missing_the_source_fields_records_no_source():
    api = FakeAPI([contact(1)], fields=[])
    snapshot = build_snapshot(
        api, captured_at="2026-08-30T12:00:00+00:00", snapshot_date="2026-08-30"
    )
    assert "source" not in snapshot["contacts"][0]


def snapshot_of(date, contacts):
    return {"date": date, "contacts": contacts}


def test_delta_separates_a_new_person_from_a_list_refiling():
    # The Aug 9 case: the count rose, but only one of the two was a new human.
    previous = snapshot_of(
        "2026-08-08",
        [{"id": "old", "list_ids": []}],
    )
    current = snapshot_of(
        "2026-08-09",
        [
            {"id": "old", "list_ids": ["L1"]},
            {"id": "fresh", "list_ids": ["L1"], "source": "habit_signup"},
        ],
    )

    delta = membership_delta(previous, current)

    assert delta["created"] == ["fresh"]
    assert delta["list_added"] == {"L1": ["old"]}
    assert delta["created_by_source"] == {"habit_signup": 1}


def test_delta_reports_removals_and_deletions():
    previous = snapshot_of(
        "2026-08-08",
        [{"id": "a", "list_ids": ["L1", "L2"]}, {"id": "gone", "list_ids": []}],
    )
    current = snapshot_of("2026-08-09", [{"id": "a", "list_ids": ["L1"]}])

    delta = membership_delta(previous, current)

    assert delta["list_removed"] == {"L2": ["a"]}
    assert delta["deleted"] == ["gone"]
    assert delta["created"] == []


def test_an_unstamped_new_contact_counts_as_unattributed():
    delta = membership_delta(
        snapshot_of("2026-08-08", []),
        snapshot_of("2026-08-09", [{"id": "mystery", "list_ids": ["L1"]}]),
    )
    assert delta["created_by_source"] == {"unattributed": 1}


def test_an_unchanged_day_produces_an_empty_delta():
    same = [{"id": "a", "list_ids": ["L1"]}]
    delta = membership_delta(
        snapshot_of("2026-08-08", same), snapshot_of("2026-08-09", same)
    )
    assert delta["created"] == []
    assert delta["deleted"] == []
    assert delta["list_added"] == {}
    assert delta["list_removed"] == {}


def test_write_snapshot_names_the_file_for_its_date(tmp_path):
    api = FakeAPI([contact(1)])
    snapshot = build_snapshot(
        api, captured_at="2026-08-30T12:00:00+00:00", snapshot_date="2026-08-30"
    )
    path = write_snapshot(snapshot, directory=tmp_path)
    assert path.name == "2026-08-30.json"
    assert json.loads(path.read_text())["contact_count"] == 1
