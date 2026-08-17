from collections import deque

import pytest
import requests

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
        queued = self.responses.popleft()
        if isinstance(queued, Exception):
            raise queued
        return queued


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


def test_delete_list_uses_exact_marketing_endpoint():
    api, fake = make_api(FakeResponse(204, None))

    api.delete_list("list-1")

    assert fake.calls[-1]["method"] == "DELETE"
    assert fake.calls[-1]["url"].endswith("/v3/marketing/lists/list-1")


def test_delete_single_send_uses_exact_marketing_endpoint():
    api, fake = make_api(FakeResponse(204, None))

    api.delete_single_send("single-send-1")

    assert fake.calls[-1]["method"] == "DELETE"
    assert fake.calls[-1]["url"].endswith(
        "/v3/marketing/singlesends/single-send-1"
    )


def test_single_sends_by_name_paginates_and_returns_every_exact_match():
    api, fake = make_api(
        FakeResponse(
            200,
            {
                "result": [
                    {"id": "send-1", "name": "2026_08: Yoga Lifestyle: Monthly"},
                    {"id": "other", "name": "Other"},
                ],
                "_metadata": {
                    "next": (
                        "https://api.sendgrid.com/v3/marketing/singlesends"
                        "?page_size=100&page_token=next"
                    )
                },
            },
        ),
        FakeResponse(
            200,
            {
                "result": [
                    {"id": "send-2", "name": "2026_08: Yoga Lifestyle: Monthly"},
                ],
                "_metadata": {},
            },
        ),
    )

    rows = api.single_sends_by_name("2026_08: Yoga Lifestyle: Monthly")

    assert [row["id"] for row in rows] == ["send-1", "send-2"]
    assert len(fake.calls) == 2
    assert fake.calls[1]["url"].endswith(
        "/v3/marketing/singlesends?page_size=100&page_token=next"
    )


def test_single_sends_by_name_rejects_pagination_loop():
    next_path = "/marketing/singlesends?page_size=100"
    api, _ = make_api(
        FakeResponse(
            200,
            {
                "result": [],
                "_metadata": {"next": next_path},
            },
        ),
    )

    with pytest.raises(SendGridAPIError, match="pagination loop"):
        api.single_sends_by_name("2026_08: Yoga Lifestyle: Monthly")


def test_marketing_lists_return_complete_inventory():
    api, fake = make_api(FakeResponse(200, {
        "result": [{"id": "list-1", "name": "TWY Marketing"}],
        "_metadata": {},
    }))
    assert api.marketing_lists() == [
        {"id": "list-1", "name": "TWY Marketing"}
    ]
    assert fake.calls[-1]["method"] == "GET"
    assert fake.calls[-1]["url"].endswith(
        "/v3/marketing/lists?page_size=1000"
    )


def test_upsert_contacts_returns_async_job_id():
    api, fake = make_api(FakeResponse(202, {"job_id": "job-1"}))
    job_id = api.upsert_contacts(["list-1"], [{"email": "a@example.com"}])
    assert job_id == "job-1"
    assert fake.calls[-1]["json"] == {
        "list_ids": ["list-1"],
        "contacts": [{"email": "a@example.com"}],
    }


def test_list_contacts_uses_complete_list_scoped_export():
    first_csv = (
        b'"EMAIL","FIRST_NAME","CONTACT_ID"\n'
        b'"a@example.com","A","contact-1"\n'
    )
    second_csv = (
        b'"EMAIL","FIRST_NAME","CONTACT_ID"\n'
        b'"b@example.com","B","contact-2"\n'
    )
    api, fake = make_api(
        FakeResponse(202, {"id": "export-1"}),
        FakeResponse(
            200,
            {
                "id": "export-1",
                "status": "ready",
                "contact_count": 2,
                "urls": [
                    "https://storage.example/export-1.csv",
                    "https://storage.example/export-2.csv",
                ],
            },
        ),
        FakeResponse(200, content=first_csv),
        FakeResponse(200, content=second_csv),
    )
    assert api.list_contacts("list-1") == [
        {"email": "a@example.com", "id": "contact-1"},
        {"email": "b@example.com", "id": "contact-2"},
    ]
    assert fake.calls[0]["url"].endswith("/v3/marketing/contacts/exports")
    assert fake.calls[0]["json"]["list_ids"] == ["list-1"]
    assert "headers" not in fake.calls[2]
    assert "headers" not in fake.calls[3]


def test_list_contacts_rejects_incomplete_export():
    api, _ = make_api(
        FakeResponse(202, {"id": "export-1"}),
        FakeResponse(
            200,
            {
                "id": "export-1",
                "status": "ready",
                "contact_count": 2,
                "urls": ["https://storage.example/export.csv"],
            },
        ),
        FakeResponse(
            200,
            content=(
                b'"EMAIL","CONTACT_ID"\n'
                b'"a@example.com","contact-1"\n'
            ),
        ),
    )

    with pytest.raises(SendGridAPIError, match="expected 2, got 1"):
        api.list_contacts("list-1")


def test_list_contacts_rejects_export_without_required_columns():
    api, _ = make_api(
        FakeResponse(202, {"id": "export-1"}),
        FakeResponse(
            200,
            {
                "id": "export-1",
                "status": "ready",
                "contact_count": 1,
                "urls": ["https://storage.example/export.csv"],
            },
        ),
        FakeResponse(
            200,
            content=b'"EMAIL"\n"a@example.com"\n',
        ),
    )

    with pytest.raises(SendGridAPIError, match="required columns"):
        api.list_contacts("list-1")


def test_list_contacts_accepts_empty_export_without_download_urls():
    api, _ = make_api(
        FakeResponse(202, {"id": "export-1"}),
        FakeResponse(
            200,
            {
                "id": "export-1",
                "status": "ready",
                "contact_count": 0,
                "urls": [],
            },
        ),
    )

    assert api.list_contacts("list-1") == []


def test_list_contacts_accepts_ready_empty_export_without_export_count():
    api, fake = make_api(
        FakeResponse(202, {"id": "export-1"}),
        FakeResponse(
            200,
            {
                "id": "export-1",
                "status": "ready",
                "urls": [],
            },
        ),
        FakeResponse(
            200,
            {
                "id": "list-1",
                "name": "Yoga Habit: Registered: 2026_08",
                "contact_count": 0,
            },
        ),
    )

    assert api.list_contacts("list-1") == []
    assert fake.calls[-1]["method"] == "GET"
    assert fake.calls[-1]["url"].endswith(
        "/v3/marketing/lists/list-1"
    )


def test_list_contact_count_uses_authoritative_count_without_requiring_results():
    api, fake = make_api(FakeResponse(200, {
        "result": [{"email": f"user{index}@example.com"} for index in range(50)],
        "contact_count": 921,
    }))

    assert api.list_contact_count("list-1") == 921
    assert fake.calls[-1]["url"].endswith("/v3/marketing/contacts/search")
    assert fake.calls[-1]["json"] == {"query": "CONTAINS(list_ids, 'list-1')"}


def test_list_contact_count_rejects_missing_authoritative_count():
    api, _ = make_api(FakeResponse(200, {
        "result": [{"email": "a@example.com"}],
    }))

    with pytest.raises(SendGridAPIError, match="valid contact_count"):
        api.list_contact_count("list-1")


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


def test_get_design_uses_read_only_design_endpoint():
    api, fake = make_api(
        FakeResponse(
            200,
            {
                "id": "design-id",
                "subject": "Subject",
                "html_content": "<p>Body</p>",
            },
        )
    )

    assert api.get_design("design-id")["id"] == "design-id"
    assert fake.calls[-1]["method"] == "GET"
    assert fake.calls[-1]["url"].endswith("/v3/designs/design-id")


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


def test_user_email_requires_exact_email_payload():
    api, fake = make_api(FakeResponse(200, {"email": "Admin@TiffanyWoodYoga.com"}))
    assert api.user_email() == "admin@tiffanywoodyoga.com"
    assert fake.calls[-1]["method"] == "GET"
    assert fake.calls[-1]["url"].endswith("/v3/user/email")


def test_field_definition_inventory_and_creation():
    api, fake = make_api(
        FakeResponse(200, {
            "custom_fields": [{"id": "w1", "name": "twy_status", "field_type": "Text"}],
            "reserved_fields": [{"id": "_rf0_T", "name": "first_name", "field_type": "Text"}],
        }),
        FakeResponse(201, {
            "id": "w2",
            "name": "twy_role",
            "field_type": "Text",
        }),
    )
    assert [row["name"] for row in api.field_definitions()] == [
        "twy_status",
        "first_name",
    ]
    assert api.create_field_definition("twy_role", "Text")["id"] == "w2"
    assert fake.calls[-1]["method"] == "POST"
    assert fake.calls[-1]["url"].endswith("/v3/marketing/field_definitions")
    assert fake.calls[-1]["json"] == {
        "name": "twy_role",
        "field_type": "Text",
    }


def test_suppression_group_creation_resolution_and_group_specific_add():
    api, fake = make_api(
        FakeResponse(200, [{"id": 12, "name": "Existing"}]),
        FakeResponse(201, {
            "id": 42,
            "name": "TWY Newsletters",
            "description": "Tiffany Wood Yoga newsletters",
            "is_default": True,
        }),
        FakeResponse(200, {
            "id": 42,
            "name": "TWY Newsletters",
            "description": "Tiffany Wood Yoga newsletters",
            "is_default": True,
        }),
        FakeResponse(201, {"recipient_emails": ["unsub@example.com"]}),
    )
    assert api.suppression_groups()[0]["id"] == 12
    created = api.create_suppression_group(
        "TWY Newsletters",
        "Tiffany Wood Yoga newsletters",
        True,
    )
    assert created["id"] == 42
    assert api.suppression_group(42)["name"] == "TWY Newsletters"
    api.add_group_suppressions(42, ["unsub@example.com"])
    assert fake.calls[-1]["method"] == "POST"
    assert fake.calls[-1]["url"].endswith(
        "/v3/asm/groups/42/suppressions"
    )
    assert fake.calls[-1]["json"] == {
        "recipient_emails": ["unsub@example.com"]
    }
    assert all(
        "/asm/suppressions/global" not in call["url"]
        for call in fake.calls
    )


def test_search_group_suppressions_uses_exact_group_and_addresses():
    api, fake = make_api(FakeResponse(200, {
        "recipient_emails": ["a@example.com"],
    }))
    assert api.search_group_suppressions(
        42,
        ["a@example.com", "b@example.com"],
    ) == {"a@example.com"}
    assert fake.calls[-1]["url"].endswith(
        "/v3/asm/groups/42/suppressions/search"
    )
    assert fake.calls[-1]["json"] == {
        "recipient_emails": ["a@example.com", "b@example.com"]
    }


def test_remove_group_suppression_uses_exact_group_and_encoded_address():
    api, fake = make_api(FakeResponse(204, None))
    api.remove_group_suppression(42, "jpgan6+proof@gmail.com")
    assert fake.calls[-1]["method"] == "DELETE"
    assert fake.calls[-1]["url"].endswith(
        "/v3/asm/groups/42/suppressions/jpgan6%2Bproof%40gmail.com"
    )
    assert fake.calls[-1]["json"] is None


def test_contacts_by_emails_returns_only_contacts_and_raises_provider_errors():
    api, fake = make_api(FakeResponse(200, {
        "result": {
            "a@example.com": {
                "contact": {
                    "email": "a@example.com",
                    "list_ids": ["list-1"],
                },
            },
            "b@example.com": {"error": "contact not found"},
        },
    }))
    assert api.contacts_by_emails(
        ["a@example.com", "b@example.com"]
    ) == {
        "a@example.com": {
            "email": "a@example.com",
            "list_ids": ["list-1"],
        },
    }
    assert fake.calls[-1]["url"].endswith(
        "/v3/marketing/contacts/search/emails"
    )


def test_update_list_and_remove_exact_contacts():
    api, fake = make_api(
        FakeResponse(200, {"id": "list-1", "name": "Email: Subscribed"}),
        FakeResponse(202, {"job_id": "job-1"}),
    )
    updated = api.update_list("list-1", "Email: Subscribed")
    assert updated["name"] == "Email: Subscribed"
    assert fake.calls[0]["method"] == "PATCH"
    assert fake.calls[0]["url"].endswith("/v3/marketing/lists/list-1")
    assert fake.calls[0]["json"] == {"name": "Email: Subscribed"}

    job_id = api.remove_contacts_from_list(
        "list-1",
        ["contact-1", "contact-2"],
    )
    assert job_id == "job-1"
    assert fake.calls[1]["method"] == "DELETE"
    assert fake.calls[1]["params"] == {
        "contact_ids": "contact-1,contact-2",
    }


def test_update_suppression_group_uses_exact_asm_endpoint():
    api, fake = make_api(
        FakeResponse(
            200,
            {
                "id": 35187,
                "name": "Email: Unsubscribed",
                "is_default": True,
            },
        )
    )

    result = api.update_suppression_group(
        35187,
        name="Email: Unsubscribed",
        description="TWY email preferences",
        is_default=True,
    )

    assert result["name"] == "Email: Unsubscribed"
    assert fake.calls[0]["method"] == "PATCH"
    assert fake.calls[0]["url"].endswith("/v3/asm/groups/35187")
    assert fake.calls[0]["json"] == {
        "name": "Email: Unsubscribed",
        "description": "TWY email preferences",
        "is_default": True,
    }


def test_segment_v2_lifecycle_uses_documented_endpoints():
    api, fake = make_api(
        FakeResponse(200, {"results": [{"id": "segment-1"}]}),
        FakeResponse(201, {"id": "segment-2", "status": {}}),
        FakeResponse(200, {"id": "segment-2", "contacts_count": 4}),
        FakeResponse(202, {"id": "segment-2", "status": {}}),
    )
    assert api.segments()[0]["id"] == "segment-1"
    created = api.create_segment(
        name="2026_08: Yoga Habit: General Invitation",
        query_dsl="SELECT contact_id, updated_at FROM contact_data",
        parent_list_ids=["list-1"],
    )
    assert created["id"] == "segment-2"
    assert fake.calls[1]["json"]["parent_list_ids"] == ["list-1"]
    assert api.segment("segment-2")["contacts_count"] == 4
    api.refresh_segment("segment-2")
    assert fake.calls[3]["method"] == "POST"
    assert fake.calls[3]["url"].endswith(
        "/v3/marketing/segments/2.0/segment-2/refresh"
    )


def test_single_send_update_and_unschedule():
    api, fake = make_api(
        FakeResponse(200, {"id": "send-1", "status": "draft"}),
        FakeResponse(200, {"id": "send-1", "status": "draft"}),
    )
    updated = api.update_single_send(
        "send-1",
        {"send_to": {"list_ids": ["list-1"], "all": False}},
    )
    assert updated["status"] == "draft"
    assert fake.calls[0]["method"] == "PATCH"
    assert fake.calls[0]["url"].endswith(
        "/v3/marketing/singlesends/send-1"
    )
    unscheduled = api.unschedule_single_send("send-1")
    assert unscheduled["status"] == "draft"
    assert fake.calls[1]["method"] == "DELETE"
    assert fake.calls[1]["url"].endswith(
        "/v3/marketing/singlesends/send-1/schedule"
    )


def test_send_mail_posts_direct_content_to_mail_send_endpoint():
    payload = {
        "personalizations": [
            {"to": [{"email": "tiffany@tiffanywoodyoga.com"}]}
        ],
        "from": {"email": "hello@tiffanywoodyoga.com", "name": "Tiffany Wood Yoga"},
        "subject": "Test - August",
        "content": [
            {"type": "text/plain", "value": "Plain body\n"},
            {"type": "text/html", "value": "<p>HTML body</p>"},
        ],
    }
    api, fake = make_api(FakeResponse(202, None))
    assert hasattr(api, "send_mail")

    api.send_mail(payload)

    assert fake.calls[-1]["method"] == "POST"
    assert fake.calls[-1]["url"].endswith("/v3/mail/send")
    assert fake.calls[-1]["json"] == payload


def test_a_read_that_times_out_once_is_retried_and_succeeds():
    """The 2026-08-17 07:00 alert: api.sendgrid.com did not answer in 30s.

    The retry loop already covered 429 and 5xx, but a Timeout is raised by
    the session rather than returned, so it escaped the loop and failed the
    whole scheduler run over a blip that was gone 15 minutes later.
    """
    api, fake = make_api(
        requests.exceptions.ReadTimeout("read timed out"),
        FakeResponse(200, {"email": "admin@example.invalid"}),
    )
    assert api.user_email() == "admin@example.invalid"
    assert len(fake.calls) == 2


def test_a_read_that_never_answers_still_fails_the_job():
    """A real outage must not be swallowed into a silent success."""
    api, fake = make_api(*[requests.exceptions.ReadTimeout("read timed out")] * 4)
    with pytest.raises(requests.exceptions.ReadTimeout):
        api.user_email()
    assert len(fake.calls) == 4


def test_a_write_that_times_out_is_never_repeated():
    """A lost reply to a POST does not mean the write was lost.

    Repeating it can trigger a Single Send twice, so the timeout is raised
    on the first attempt and a human decides.
    """
    api, fake = make_api(
        requests.exceptions.ReadTimeout("read timed out"),
        FakeResponse(201, {"id": "list-1"}),
    )
    with pytest.raises(requests.exceptions.ReadTimeout):
        api.create_list("Proof")
    assert len(fake.calls) == 1


def test_a_connection_error_on_a_read_is_retried_with_backoff():
    slept = []
    session = FakeSession(
        [
            requests.exceptions.ConnectionError("connection reset"),
            FakeResponse(200, {"email": "admin@example.invalid"}),
        ]
    )
    api = SendGridAPI("SG.secret-proof-key", session=session, sleep_fn=slept.append)
    assert api.user_email() == "admin@example.invalid"
    assert slept == [1.0]
