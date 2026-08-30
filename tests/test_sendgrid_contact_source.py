import pytest

from sendgrid_contact_source import (
    DETAIL_FIELD,
    SOURCE_FIELD,
    SOURCE_HABIT_SIGNUP,
    SOURCE_HABIT_SYNC,
    UnknownContactSource,
    ensure_source_fields,
    existing_emails,
    normalize_detail,
    stamp_new_contacts,
)


class FakeAPI:
    def __init__(self, fields=None, existing=()):
        self.fields = list(fields or [])
        self.existing = {e.lower() for e in existing}
        self.created_fields = []
        self.lookups = []

    def field_definitions(self):
        return list(self.fields)

    def create_field_definition(self, name, field_type):
        identifier = f"id_{name}"
        self.created_fields.append((name, field_type))
        self.fields.append({"id": identifier, "name": name})
        return {"id": identifier, "name": name, "field_type": field_type}

    def contacts_by_emails(self, emails):
        self.lookups.append(list(emails))
        return {
            email: {"id": f"c_{email}"}
            for email in emails
            if email in self.existing
        }


def test_ensure_source_fields_creates_both_when_absent():
    api = FakeAPI()
    ids = ensure_source_fields(api)
    assert ids == {SOURCE_FIELD: "id_twy_source", DETAIL_FIELD: "id_twy_source_detail"}
    assert api.created_fields == [
        (SOURCE_FIELD, "Text"),
        (DETAIL_FIELD, "Text"),
    ]


def test_ensure_source_fields_reuses_existing_definitions():
    api = FakeAPI(
        fields=[
            {"id": "e1_T", "name": SOURCE_FIELD},
            {"id": "e2_T", "name": DETAIL_FIELD},
        ]
    )
    ids = ensure_source_fields(api)
    assert ids == {SOURCE_FIELD: "e1_T", DETAIL_FIELD: "e2_T"}
    assert api.created_fields == []


def test_stamp_applies_source_only_to_new_contacts():
    api = FakeAPI(existing=["known@example.com"])
    stamped = stamp_new_contacts(
        api,
        [{"email": "known@example.com"}, {"email": "new@example.com"}],
        source=SOURCE_HABIT_SIGNUP,
        detail="utm_source=ig",
    )
    known, new = stamped
    assert "custom_fields" not in known
    assert new["custom_fields"] == {
        "id_twy_source": SOURCE_HABIT_SIGNUP,
        "id_twy_source_detail": "utm_source=ig",
    }


def test_existing_contact_source_is_never_rewritten_by_a_later_sync():
    # The whole point: the daily Habit sync must not relabel a person who
    # originally arrived through the signup form.
    api = FakeAPI(existing=["person@example.com"])
    stamped = stamp_new_contacts(
        api,
        [{"email": "person@example.com"}],
        source=SOURCE_HABIT_SYNC,
    )
    assert stamped == [{"email": "person@example.com"}]


def test_stamp_preserves_other_contact_fields_and_custom_fields():
    api = FakeAPI()
    stamped = stamp_new_contacts(
        api,
        [
            {
                "email": "new@example.com",
                "first_name": "Tiff",
                "custom_fields": {"e9_T": "member"},
            }
        ],
        source=SOURCE_HABIT_SIGNUP,
    )
    contact = stamped[0]
    assert contact["first_name"] == "Tiff"
    assert contact["custom_fields"]["e9_T"] == "member"
    assert contact["custom_fields"]["id_twy_source"] == SOURCE_HABIT_SIGNUP


def test_stamp_does_not_mutate_the_caller_list():
    api = FakeAPI()
    original = [{"email": "new@example.com"}]
    stamp_new_contacts(api, original, source=SOURCE_HABIT_SIGNUP)
    assert original == [{"email": "new@example.com"}]


def test_detail_is_omitted_when_empty():
    api = FakeAPI()
    stamped = stamp_new_contacts(
        api, [{"email": "new@example.com"}], source=SOURCE_HABIT_SIGNUP
    )
    assert DETAIL_FIELD not in stamped[0]["custom_fields"]
    assert "id_twy_source_detail" not in stamped[0]["custom_fields"]


def test_unknown_source_is_refused():
    api = FakeAPI()
    with pytest.raises(UnknownContactSource):
        stamp_new_contacts(
            api, [{"email": "a@example.com"}], source="whatever_i_felt_like"
        )


def test_empty_contact_list_makes_no_api_calls():
    api = FakeAPI()
    assert stamp_new_contacts(api, [], source=SOURCE_HABIT_SIGNUP) == []
    assert api.lookups == []
    assert api.created_fields == []


def test_email_lookup_is_chunked_at_one_hundred():
    emails = [f"user{n}@example.com" for n in range(250)]
    api = FakeAPI()
    existing_emails(api, emails)
    assert [len(chunk) for chunk in api.lookups] == [100, 100, 50]


def test_email_lookup_deduplicates_and_lowercases():
    api = FakeAPI()
    found = existing_emails(
        api, ["A@example.com", "a@example.com", "", None]
    )
    assert api.lookups == [["a@example.com"]]
    assert found == set()


def test_normalize_detail_collapses_whitespace_and_truncates():
    assert normalize_detail("  utm_source=ig   utm_content=bio ") == (
        "utm_source=ig utm_content=bio"
    )
    assert len(normalize_detail("x" * 300)) == 100
    assert normalize_detail(None) == ""
