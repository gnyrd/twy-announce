"""sendgrid_contact_identity: the customer id field and its source of truth."""
import sqlite3

import pytest

from sendgrid_contact_identity import (
    IDENTITY_FIELD,
    custom_field_ids,
    customer_ids_by_email,
    ensure_identity_field,
    identity_field_id,
    plan_identity_stamps,
)


class FakeAPI:
    def __init__(self, fields=None):
        self.fields = list(fields or [])
        self.created = []

    def field_definitions(self):
        return list(self.fields)

    def create_field_definition(self, name, field_type):
        self.created.append((name, field_type))
        created = {"id": f"e{len(self.fields) + 1}_T", "name": name, "field_type": field_type}
        self.fields.append(created)
        return created


def test_identity_field_is_created_once_then_found():
    api = FakeAPI([{"id": "e3_T", "name": "twy_source", "field_type": "Text"}])

    assert identity_field_id(api) == ""
    first = ensure_identity_field(api)
    second = ensure_identity_field(api)

    assert first == second == "e2_T"
    assert api.created == [(IDENTITY_FIELD, "Text")]
    assert identity_field_id(api) == "e2_T"


def test_a_field_created_without_an_id_is_refused():
    class Broken(FakeAPI):
        def create_field_definition(self, name, field_type):
            return {}

    with pytest.raises(ValueError, match="without an ID"):
        ensure_identity_field(Broken())


def test_custom_field_ids_map_every_named_field():
    api = FakeAPI([
        {"id": "e3_T", "name": "twy_source"},
        {"id": "e4_T", "name": "twy_source_detail"},
        {"id": "", "name": "nameless"},
    ])
    assert custom_field_ids(api) == {"twy_source": "e3_T", "twy_source_detail": "e4_T"}


def _customers(path, rows):
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE customers (id INTEGER, email TEXT, first_name TEXT)")
    connection.executemany("INSERT INTO customers VALUES (?, ?, ?)", rows)
    connection.commit()
    connection.close()


def test_customer_ids_are_keyed_by_lowercase_email_as_strings(tmp_path):
    db = tmp_path / "marvy.db"
    _customers(db, [(441, "Buyer@Example.com", "B"), (7, "", "x"), (8, None, "y")])

    assert customer_ids_by_email(db) == {"buyer@example.com": "441"}


def test_customer_ids_open_the_database_read_only(tmp_path):
    db = tmp_path / "marvy.db"
    _customers(db, [(1, "a@example.com", "A")])
    before = db.read_bytes()

    customer_ids_by_email(db)

    assert db.read_bytes() == before


def _row(email, current=""):
    return {"email": email, "id": f"c_{email}", "fields": {IDENTITY_FIELD: current}}


def test_stamps_go_only_on_existing_contacts_hm_knows():
    listed = [_row("a@example.com"), _row("b@example.com", "7"), _row("c@example.com", "9"), _row("d@example.com")]
    ids = {"a@example.com": "1", "b@example.com": "7", "c@example.com": "3", "never@example.com": "5"}

    payloads, counts = plan_identity_stamps(listed, ids, "e5_T")

    assert payloads == [
        {"email": "a@example.com", "custom_fields": {"e5_T": "1"}},
        {"email": "c@example.com", "custom_fields": {"e5_T": "3"}},
    ]
    assert counts == {"matched": 3, "already": 1, "stamped": 1, "restamped": 1, "deferred": 0}
    assert all(p["email"] != "never@example.com" for p in payloads)


def test_an_id_another_contact_still_carries_is_left_for_the_rename_planner():
    # HeyMarvelous switched customer 441 from a@ to b@; a@ still carries the id.
    listed = [_row("a@example.com", "441"), _row("b@example.com")]

    payloads, counts = plan_identity_stamps(listed, {"b@example.com": "441"}, "e5_T")

    assert payloads == []
    assert counts == {"matched": 1, "already": 0, "stamped": 0, "restamped": 0, "deferred": 1}
