from collections import deque

import pytest

from migration_read_clients import (
    EndpointNotAllowed,
    ReadOnlyMailchimpAPI,
    ReadOnlySendGridAPI,
)


class FakeResponse:
    def __init__(self, status_code=200, body=None, headers=None):
        self.status_code = status_code
        self._body = body
        self.headers = headers or {}
        self.text = "" if body is None else str(body)

    def json(self):
        return self._body


class FakeSession:
    def __init__(self, *responses):
        self.responses = deque(responses)
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append({"method": method, "url": url, **kwargs})
        return self.responses.popleft()


def test_mailchimp_refuses_mutation_before_network_io():
    fake = FakeSession()
    api = ReadOnlyMailchimpAPI("us21", "secret", "aud", session=fake)
    with pytest.raises(EndpointNotAllowed):
        api._request("POST", "/lists/aud/members")
    assert fake.calls == []


def test_mailchimp_refuses_unknown_get_before_network_io():
    fake = FakeSession()
    api = ReadOnlyMailchimpAPI("us21", "secret", "aud", session=fake)
    with pytest.raises(EndpointNotAllowed):
        api._request("GET", "/campaigns")
    assert fake.calls == []


def test_mailchimp_paginates_one_status_exactly():
    fake = FakeSession(
        FakeResponse(body={
            "members": [{"email_address": "a@example.com", "status": "subscribed"}],
            "total_items": 2,
        }),
        FakeResponse(body={
            "members": [{"email_address": "b@example.com", "status": "subscribed"}],
            "total_items": 2,
        }),
    )
    api = ReadOnlyMailchimpAPI("us21", "secret", "aud", session=fake)
    members = api.members_for_status("subscribed", page_size=1)
    assert [member["email_address"] for member in members] == [
        "a@example.com",
        "b@example.com",
    ]
    assert [call["params"]["offset"] for call in fake.calls] == [0, 1]
    assert all(call["params"]["status"] == "subscribed" for call in fake.calls)


def test_mailchimp_rejects_status_mismatch():
    fake = FakeSession(FakeResponse(body={
        "members": [{"email_address": "a@example.com", "status": "cleaned"}],
        "total_items": 1,
    }))
    api = ReadOnlyMailchimpAPI("us21", "secret", "aud", session=fake)
    with pytest.raises(RuntimeError, match="status mismatch"):
        api.members_for_status("subscribed")


def test_mailchimp_inventory_captures_schema_targeting_and_welcome_journey():
    fake = FakeSession(
        FakeResponse(body={"id": "aud", "name": "TWY"}),
        FakeResponse(body={
            "merge_fields": [{"tag": "FNAME"}],
            "total_items": 1,
        }),
        FakeResponse(body={
            "segments": [{"id": 2964430, "name": "New Subscriber YLS Membership"}],
            "total_items": 1,
        }),
        FakeResponse(body={
            "steps": [{"step_type": "trigger-tag_added"}],
            "total_items": 1,
        }),
    )
    api = ReadOnlyMailchimpAPI("us21", "secret", "aud", session=fake)
    inventory = api.inventory(journey_id=3209)
    assert inventory == {
        "list": {"id": "aud", "name": "TWY"},
        "merge_fields": [{"tag": "FNAME"}],
        "segments": [{"id": 2964430, "name": "New Subscriber YLS Membership"}],
        "journey": {
            "id": 3209,
            "steps": {
                "steps": [{"step_type": "trigger-tag_added"}],
                "total_items": 1,
            },
        },
    }


def test_sendgrid_refuses_mutating_endpoint_before_network_io():
    fake = FakeSession()
    api = ReadOnlySendGridAPI("SG.secret", session=fake)
    with pytest.raises(EndpointNotAllowed):
        api._request("PUT", "/marketing/contacts")
    assert fake.calls == []


def test_sendgrid_allows_only_exact_read_post():
    fake = FakeSession()
    api = ReadOnlySendGridAPI("SG.secret", session=fake)
    with pytest.raises(EndpointNotAllowed):
        api._request("POST", "/marketing/contacts/search")
    assert fake.calls == []


def test_sendgrid_exact_email_lookup_batches_at_100_and_tracks_absence():
    emails = [f"person{i}@example.com" for i in range(101)]
    first_result = {
        email: {"contact": {"id": str(index), "email": email}}
        for index, email in enumerate(emails[:100])
    }
    first_result[emails[99]] = {"error": "contact not found"}
    fake = FakeSession(
        FakeResponse(body={"result": first_result}),
        FakeResponse(404, {"errors": [{"message": "not found"}]}),
    )
    api = ReadOnlySendGridAPI("SG.secret", session=fake)
    result = api.contacts_by_emails(emails)
    assert len(fake.calls[0]["json"]["emails"]) == 100
    assert len(fake.calls[1]["json"]["emails"]) == 1
    assert emails[0] in result.contacts
    assert emails[99] in result.absent
    assert emails[100] in result.absent


def test_sendgrid_non_absence_per_email_error_is_quarantined():
    fake = FakeSession(FakeResponse(body={
        "result": {"a@example.com": {"error": "backend unavailable"}},
    }))
    api = ReadOnlySendGridAPI("SG.secret", session=fake)
    result = api.contacts_by_emails(["a@example.com"])
    assert result.errors == {"a@example.com": "backend unavailable"}
    assert result.absent == frozenset()


def test_sendgrid_marketing_lists_follow_next_link():
    fake = FakeSession(
        FakeResponse(body={
            "result": [{"id": "one"}],
            "_metadata": {"next": "https://api.sendgrid.com/v3/marketing/lists?page_token=x"},
        }),
        FakeResponse(body={"result": [{"id": "two"}], "_metadata": {}}),
    )
    api = ReadOnlySendGridAPI("SG.secret", session=fake)
    assert [item["id"] for item in api.marketing_lists()] == ["one", "two"]


def test_api_errors_never_include_secret():
    fake = FakeSession(FakeResponse(403, {"error": "SG.secret"}))
    api = ReadOnlySendGridAPI("SG.secret", session=fake)
    with pytest.raises(RuntimeError) as exc:
        api.account()
    assert "SG.secret" not in str(exc.value)
    assert "[REDACTED]" in str(exc.value)


def test_audit_contains_method_path_status_but_no_request_body():
    fake = FakeSession(FakeResponse(body={"result": {}}))
    api = ReadOnlySendGridAPI("SG.secret", session=fake)
    api.contacts_by_emails(["private@example.com"])
    assert api.audit == (
        {"method": "POST", "path": "/marketing/contacts/search/emails", "status": 200},
    )
    assert "private@example.com" not in str(api.audit)
