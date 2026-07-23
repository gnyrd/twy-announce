from collections import deque

import pytest

from sendgrid_api import (
    SendGridAPI,
    SendGridAPIError,
    SendGridJobFailed,
    SendGridJobTimeout,
)


class FakeResponse:
    def __init__(self, status_code, body=None, headers=None, content=None):
        self.status_code = status_code
        self._body = body
        self.headers = headers or {}
        self.text = "" if body is None else str(body)
        self.content = content if content is not None else self.text.encode()

    def json(self):
        return self._body


class FakeSession:
    def __init__(self, responses):
        self.responses = deque(responses)
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append({"method": method, "url": url, **kwargs})
        return self.responses.popleft()


def make_api(*responses):
    session = FakeSession(responses)
    api = SendGridAPI("SG.secret-proof-key", session=session, sleep_fn=lambda _: None)
    return api, session


def test_create_list_uses_marketing_endpoint():
    api, fake = make_api(FakeResponse(201, {"id": "list-1", "name": "Proof"}))
    assert api.create_list("Proof")["id"] == "list-1"
    assert fake.calls[-1]["method"] == "POST"
    assert fake.calls[-1]["url"].endswith("/v3/marketing/lists")
    assert fake.calls[-1]["json"] == {"name": "Proof"}


def test_upsert_contacts_returns_async_job_id():
    api, fake = make_api(FakeResponse(202, {"job_id": "job-1"}))
    job_id = api.upsert_contacts(["list-1"], [{"email": "a@example.com"}])
    assert job_id == "job-1"
    assert fake.calls[-1]["json"] == {
        "list_ids": ["list-1"],
        "contacts": [{"email": "a@example.com"}],
    }


def test_list_contacts_uses_sgql_and_returns_complete_result():
    api, fake = make_api(FakeResponse(200, {
        "result": [{"email": "a@example.com", "list_ids": ["list-1"]}],
        "contact_count": 1,
    }))
    assert api.list_contacts("list-1") == [
        {"email": "a@example.com", "list_ids": ["list-1"]}
    ]
    assert fake.calls[-1]["url"].endswith("/v3/marketing/contacts/search")
    assert fake.calls[-1]["json"] == {"query": "CONTAINS(list_ids, 'list-1')"}


def test_wait_contact_job_accepts_completed():
    api, _ = make_api(
        FakeResponse(200, {"status": "pending"}),
        FakeResponse(200, {"status": "completed", "job_id": "job-1"}),
    )
    assert api.wait_contact_job("job-1", timeout_s=1)["status"] == "completed"


def test_wait_contact_job_rejects_failed():
    api, _ = make_api(FakeResponse(200, {"status": "failed", "errors": ["bad"]}))
    with pytest.raises(SendGridJobFailed):
        api.wait_contact_job("job-1", timeout_s=1)


def test_wait_contact_job_times_out(monkeypatch):
    api, _ = make_api(FakeResponse(200, {"status": "pending"}))
    monkeypatch.setattr(api, "_request", lambda *args, **kwargs: {"status": "pending"})
    moments = iter((0.0, 2.0))
    monkeypatch.setattr("sendgrid_api.time.monotonic", lambda: next(moments))
    with pytest.raises(SendGridJobTimeout):
        api.wait_contact_job("job-1", timeout_s=1)


def test_add_and_retrieve_global_unsubscribe():
    api, fake = make_api(
        FakeResponse(201, None),
        FakeResponse(200, {"recipient_email": "unsub@example.invalid"}),
    )
    api.add_global_unsubscribes(["unsub@example.invalid"])
    assert fake.calls[0]["json"] == {"recipient_emails": ["unsub@example.invalid"]}
    assert api.get_global_unsubscribe("unsub@example.invalid") == {
        "recipient_email": "unsub@example.invalid"
    }


def test_empty_global_unsubscribe_lookup_is_absent():
    api, _ = make_api(FakeResponse(200, {}))
    assert api.get_global_unsubscribe("missing@example.invalid") is None


def test_missing_bounce_lookup_is_absent():
    api, _ = make_api(FakeResponse(404, {"errors": [{"message": "not found"}]}))
    assert api.get_bounce("cleaned@example.invalid") is None


def test_create_and_schedule_single_send():
    payload = {
        "name": "Proof Immediate",
        "send_to": {"list_ids": ["list-1"], "all": False},
        "email_config": {
            "subject": "Proof",
            "html_content": "<p>Proof</p>",
            "plain_content": "Proof\n",
            "generate_plain_content": False,
            "editor": "design",
            "suppression_group_id": 42,
            "sender_id": 7,
        },
    }
    api, fake = make_api(
        FakeResponse(201, {"id": "ss-1", "status": "draft"}),
        FakeResponse(201, {"status": "scheduled", "send_at": "now"}),
    )
    assert api.create_single_send(payload)["id"] == "ss-1"
    scheduled = api.schedule_single_send("ss-1", "now")
    assert scheduled["status"] == "scheduled"
    assert fake.calls[-1]["method"] == "PUT"
    assert fake.calls[-1]["url"].endswith("/v3/marketing/singlesends/ss-1/schedule")
    assert fake.calls[-1]["json"] == {"send_at": "now"}


def test_find_single_send_by_exact_name_follows_pages():
    api, _ = make_api(
        FakeResponse(200, {
            "result": [{"id": "other", "name": "Other"}],
            "_metadata": {"next": "https://api.sendgrid.com/v3/marketing/singlesends?page_token=next"},
        }),
        FakeResponse(200, {
            "result": [{"id": "ss-1", "name": "Proof Immediate"}],
            "_metadata": {},
        }),
    )
    assert api.find_single_send_by_name("Proof Immediate")["id"] == "ss-1"


def test_find_single_send_handles_live_empty_null_result():
    api, _ = make_api(FakeResponse(200, {
        "result": None,
        "_metadata": {"count": 0},
    }))
    assert api.find_single_send_by_name("Proof Immediate") is None


def test_contact_export_start_and_ready_status():
    api, fake = make_api(
        FakeResponse(202, {"id": "export-1"}),
        FakeResponse(200, {"id": "export-1", "status": "ready", "urls": ["https://signed"]}),
    )
    started = api.start_contact_export(["list-1"])
    assert started["id"] == "export-1"
    assert fake.calls[0]["json"] == {
        "list_ids": ["list-1"],
        "file_type": "csv",
        "notifications": {"email": False},
    }
    assert api.wait_contact_export("export-1", timeout_s=1)["status"] == "ready"


def test_account_wide_contact_export_omits_list_filter():
    api, fake = make_api(FakeResponse(202, {"id": "export-all"}))
    assert api.start_contact_export(None)["id"] == "export-all"
    assert fake.calls[0]["json"] == {
        "file_type": "csv",
        "notifications": {"email": False},
    }


def test_contact_export_download_omits_sendgrid_authorization():
    api, fake = make_api(FakeResponse(
        200,
        content=b"email\nproof@example.com\n",
    ))
    payload = api.download_contact_export(
        "https://storage.example/export.csv?X-Amz-Signature=secret"
    )
    assert payload == b"email\nproof@example.com\n"
    assert "headers" not in fake.calls[0]


def test_single_send_stats_are_absent_until_sendgrid_materializes_them():
    api, _ = make_api(FakeResponse(404, None))
    assert api.single_send_stats("ss-1", "2026-07-23") is None


def test_429_retries_using_retry_after():
    slept = []
    session = FakeSession([
        FakeResponse(429, {"errors": []}, {"Retry-After": "3"}),
        FakeResponse(200, {"ok": True}),
    ])
    api = SendGridAPI("SG.secret", session=session, sleep_fn=slept.append)
    assert api._request("GET", "/user/account") == {"ok": True}
    assert slept == [3.0]


def test_api_error_never_exposes_key():
    api, _ = make_api(FakeResponse(400, {"message": "SG.secret-proof-key rejected"}))
    with pytest.raises(SendGridAPIError) as caught:
        api._request("GET", "/bad")
    assert "SG.secret-proof-key" not in str(caught.value)
    assert "[REDACTED]" in str(caught.value)
