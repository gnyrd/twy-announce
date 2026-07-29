import json
import inspect
from datetime import datetime, timezone

import pytest

from sendgrid_campaigns import (
    SendGridCampaigns,
    SendGridRegistry,
)
from sendgrid_mailings import MailingPurpose, mailing_name


class FakeAPI:
    def __init__(self):
        self.single_sends = {}
        self.segments_by_id = {}
        self.created_sends = []
        self.created_segments = []
        self.updated_segments = []
        self.scheduled = []
        self.unscheduled = []
        self.deleted = []
        self.schedule_status = "scheduled"
        self.list_rows = []
        self.created_lists = []

    def create_single_send(self, payload):
        identifier = f"send{len(self.created_sends) + 1}"
        item = {
            "id": identifier,
            "name": payload["name"],
            "status": "draft",
            "send_to": payload["send_to"],
            "email_config": payload["email_config"],
        }
        self.single_sends[identifier] = item
        self.created_sends.append(payload)
        return item

    def get_single_send(self, identifier):
        return self.single_sends[identifier]

    def single_sends_by_name(self, name):
        return [
            {"id": item["id"], "name": item["name"]}
            for item in self.single_sends.values()
            if item["name"] == name
        ]

    def delete_single_send(self, identifier):
        self.deleted.append(identifier)
        self.single_sends.pop(identifier, None)

    def create_segment(self, **payload):
        identifier = f"segment{len(self.created_segments) + 1}"
        item = {
            "id": identifier,
            **payload,
            "status": {"query_validation": "VALID"},
        }
        self.segments_by_id[identifier] = item
        self.created_segments.append(payload)
        return item

    def segment(self, identifier):
        return self.segments_by_id[identifier]

    def update_segment(self, identifier, **payload):
        item = self.segments_by_id[identifier]
        item.update(payload)
        self.updated_segments.append((identifier, payload))
        return item

    def schedule_single_send(self, identifier, send_at):
        self.scheduled.append((identifier, send_at))
        self.single_sends[identifier]["status"] = self.schedule_status
        self.single_sends[identifier]["send_at"] = send_at
        return self.single_sends[identifier]

    def unschedule_single_send(self, identifier):
        self.unscheduled.append(identifier)
        self.single_sends[identifier]["status"] = "draft"
        return self.single_sends[identifier]

    def marketing_lists(self):
        return list(self.list_rows)

    def create_list(self, name):
        item = {"id": f"list{len(self.created_lists) + 1}", "name": name}
        self.created_lists.append(item)
        self.list_rows.append(item)
        return item

    def segments(self):
        return list(self.segments_by_id.values())




def _write_newsletter_template(root):
    newsletters = root / "newsletters"
    newsletters.mkdir(parents=True, exist_ok=True)
    (newsletters / "twy_newsletter_template.html").write_text(
        '<html><body style="background-color:#5d8399;">'
        '<div mc:edit="main_content"><p>Old content.</p></div>'
        '</body></html>'
    )

def _registry(path):
    path.write_text(json.dumps({
        "account_email": "admin@tiffanywoodyoga.com",
        "sender": {
            "id": 9423402,
            "email": "hello@tiffanywoodyoga.com",
        },
        "suppression_group": {
            "id": 35187,
            "name": "Email: Unsubscribed",
        },
        "lists": {
            "Email: Subscribed": {
                "id": "subscribed1",
            },
            "Member: Yoga Lifestyle": {
                "id": "lifestyle1",
            },
            "Yoga Habit: Interested: 2026_08": {
                "id": "interested1",
            },
            "Yoga Habit: Registered: 2026_08": {
                "id": "registered1",
            },
        },
    }))
    return SendGridRegistry.load(path)


def test_registry_rejects_old_provider_names(tmp_path):
    path = tmp_path / "registry.json"
    path.write_text(json.dumps({
        "account_email": "admin@tiffanywoodyoga.com",
        "sender": {"id": 1, "email": "hello@tiffanywoodyoga.com"},
        "suppression_group": {"id": 2, "name": "TWY Newsletters"},
        "lists": {},
    }))
    with pytest.raises(ValueError, match="unexpected unsubscribe group"):
        SendGridRegistry.load(path)


def test_registry_records_new_period_list_by_immutable_id(tmp_path):
    registry = _registry(tmp_path / "registry.json")
    registry.register_list(
        "Yoga Habit: Interested: 2026_09",
        "interested2",
    )
    reloaded = SendGridRegistry.load(registry.path)
    assert reloaded.list_id(
        "Yoga Habit: Interested: 2026_09"
    ) == "interested2"



def test_create_draft_uses_twy_template_html_when_available(monkeypatch, tmp_path):
    monkeypatch.setenv("TWY_DATA_DIR", str(tmp_path))
    _write_newsletter_template(tmp_path)
    registry = _registry(tmp_path / "registry.json")
    api = FakeAPI()
    campaigns = SendGridCampaigns(
        api=api,
        registry=registry,
        state_path=tmp_path / "state.json",
    )

    campaigns.create_draft(
        purpose=MailingPurpose.MONTHLY,
        year=2026,
        month=8,
        subject="August",
        body_md="Template body",
        send_to={"list_ids": ["lifestyle1"], "all": False},
    )

    html = api.created_sends[0]["email_config"]["html_content"]
    assert html.startswith("<html>")
    assert "background-color:#5d8399" in html
    assert "Template body" in html
    assert "Old content." not in html


def test_create_draft_includes_preheader_in_rendered_html(monkeypatch, tmp_path):
    monkeypatch.setenv("TWY_DATA_DIR", str(tmp_path))
    _write_newsletter_template(tmp_path)
    registry = _registry(tmp_path / "registry.json")
    api = FakeAPI()
    campaigns = SendGridCampaigns(
        api=api,
        registry=registry,
        state_path=tmp_path / "state.json",
    )
    assert "preheader" in inspect.signature(campaigns.create_draft).parameters

    campaigns.create_draft(
        purpose=MailingPurpose.MONTHLY,
        year=2026,
        month=8,
        subject="August",
        body_md="Template body",
        preheader="A useful inbox preview",
        send_to={"list_ids": ["lifestyle1"], "all": False},
    )

    html = api.created_sends[0]["email_config"]["html_content"]
    assert "A useful inbox preview" in html
    assert "display:none" in html
    assert "A useful inbox preview" not in api.created_sends[0]["email_config"]["plain_content"]


def test_create_draft_reuses_matching_content_and_stops_after_changed_cleanup(
    tmp_path,
):
    registry = _registry(tmp_path / "registry.json")
    state_path = tmp_path / "state.json"
    api = FakeAPI()
    campaigns = SendGridCampaigns(
        api=api,
        registry=registry,
        state_path=state_path,
    )

    created = campaigns.create_draft(
        purpose=MailingPurpose.MONTHLY,
        year=2026,
        month=8,
        subject="August",
        body_md="First body",
        send_to={"list_ids": ["lifestyle1"], "all": False},
    )
    reused = campaigns.create_draft(
        purpose=MailingPurpose.MONTHLY,
        year=2026,
        month=8,
        subject="August",
        body_md="First body",
        send_to={"list_ids": ["lifestyle1"], "all": False},
    )
    with pytest.raises(ValueError, match="conflicting"):
        campaigns.create_draft(
            purpose=MailingPurpose.MONTHLY,
            year=2026,
            month=8,
            subject="Changed",
            body_md="Changed body",
            preheader="Changed preview",
            send_to={"list_ids": ["lifestyle1"], "all": False},
        )

    assert reused["id"] == created["id"]
    assert len(api.created_sends) == 1
    assert created["id"] in api.deleted
    assert api.created_sends[0]["email_config"]["editor"] == "design"
    assert api.created_sends[0]["email_config"]["sender_id"] == 9423402
    assert api.created_sends[0]["email_config"][
        "suppression_group_id"
    ] == 35187
    persisted = json.loads(state_path.read_text())
    assert persisted["single_sends"]["Monthly"]["id"] == created["id"]
    assert persisted["single_sends"]["Monthly"]["source_sha256"] != ""


def test_create_draft_adopts_verified_provider_send_after_local_state_loss(
    tmp_path,
):
    registry = _registry(tmp_path / "registry.json")
    api = FakeAPI()
    campaigns = SendGridCampaigns(
        api=api,
        registry=registry,
        state_path=tmp_path / "state.json",
    )
    existing = campaigns.create_draft(
        purpose=MailingPurpose.MONTHLY,
        year=2026,
        month=8,
        subject="August",
        body_md="First body",
        preheader="A useful preview",
        send_to={"list_ids": ["lifestyle1"], "all": False},
    )
    campaigns.state_path.unlink()
    api.created_sends.clear()

    adopted = campaigns.create_draft(
        purpose=MailingPurpose.MONTHLY,
        year=2026,
        month=8,
        subject="August",
        body_md="First body",
        preheader="A useful preview",
        send_to={"list_ids": ["lifestyle1"], "all": False},
    )

    assert adopted["id"] == existing["id"]
    assert api.created_sends == []
    persisted = json.loads(campaigns.state_path.read_text())
    assert persisted["single_sends"]["Monthly"]["id"] == existing["id"]
    assert persisted["single_sends"]["Monthly"]["verification_status"] == (
        "verified"
    )


def test_create_draft_deletes_duplicate_verified_drafts_before_adopting(
    tmp_path,
):
    registry = _registry(tmp_path / "registry.json")
    api = FakeAPI()
    campaigns = SendGridCampaigns(
        api=api,
        registry=registry,
        state_path=tmp_path / "state.json",
    )
    first = campaigns.create_draft(
        purpose=MailingPurpose.MONTHLY,
        year=2026,
        month=8,
        subject="August",
        body_md="First body",
        preheader="A useful preview",
        send_to={"list_ids": ["lifestyle1"], "all": False},
    )
    duplicate = dict(first)
    duplicate["id"] = "send2"
    api.single_sends["send2"] = duplicate
    campaigns.state_path.unlink()
    api.created_sends.clear()

    adopted = campaigns.create_draft(
        purpose=MailingPurpose.MONTHLY,
        year=2026,
        month=8,
        subject="August",
        body_md="First body",
        preheader="A useful preview",
        send_to={"list_ids": ["lifestyle1"], "all": False},
    )

    assert adopted["id"] == "send1"
    assert api.deleted == ["send2"]
    assert set(api.single_sends) == {"send1"}


def test_create_draft_cleans_mismatched_scheduled_send_and_stops(tmp_path):
    registry = _registry(tmp_path / "registry.json")
    api = FakeAPI()
    campaigns = SendGridCampaigns(
        api=api,
        registry=registry,
        state_path=tmp_path / "state.json",
    )
    name = mailing_name(2026, 8, MailingPurpose.MONTHLY)
    api.single_sends["stale"] = {
        "id": "stale",
        "name": name,
        "status": "scheduled",
        "send_at": "2026-08-03T15:39:00Z",
        "send_to": {"list_ids": ["wrong"], "segment_ids": [], "all": False},
        "email_config": {
            "subject": "Wrong",
            "html_content": "<p>Wrong</p>",
            "plain_content": "Wrong\n",
            "generate_plain_content": False,
            "editor": "design",
            "suppression_group_id": 35187,
            "sender_id": 9423402,
        },
    }

    with pytest.raises(ValueError, match="conflicting"):
        campaigns.create_draft(
            purpose=MailingPurpose.MONTHLY,
            year=2026,
            month=8,
            subject="August",
            body_md="First body",
            preheader="A useful preview",
            send_to={"list_ids": ["lifestyle1"], "all": False},
        )

    assert api.unscheduled == ["stale"]
    assert api.deleted == ["stale"]
    assert api.created_sends == []


def test_create_draft_blocks_when_triggered_provider_send_mismatches(tmp_path):
    registry = _registry(tmp_path / "registry.json")
    api = FakeAPI()
    campaigns = SendGridCampaigns(
        api=api,
        registry=registry,
        state_path=tmp_path / "state.json",
    )
    name = mailing_name(2026, 8, MailingPurpose.MONTHLY)
    api.single_sends["sent"] = {
        "id": "sent",
        "name": name,
        "status": "triggered",
        "send_to": {"list_ids": ["wrong"], "segment_ids": [], "all": False},
        "email_config": {
            "subject": "Wrong",
            "html_content": "<p>Wrong</p>",
            "plain_content": "Wrong\n",
            "generate_plain_content": False,
            "editor": "design",
            "suppression_group_id": 35187,
            "sender_id": 9423402,
        },
    }

    with pytest.raises(ValueError, match="triggered"):
        campaigns.create_draft(
            purpose=MailingPurpose.MONTHLY,
            year=2026,
            month=8,
            subject="August",
            body_md="First body",
            preheader="A useful preview",
            send_to={"list_ids": ["lifestyle1"], "all": False},
        )

    assert api.deleted == []
    assert api.created_sends == []


def test_single_send_reverifies_recorded_provider_content(tmp_path):
    registry = _registry(tmp_path / "registry.json")
    api = FakeAPI()
    campaigns = SendGridCampaigns(
        api=api,
        registry=registry,
        state_path=tmp_path / "state.json",
    )
    created = campaigns.create_draft(
        purpose=MailingPurpose.MONTHLY,
        year=2026,
        month=8,
        subject="August",
        body_md="First body",
        preheader="A useful preview",
        send_to={"list_ids": ["lifestyle1"], "all": False},
    )
    api.single_sends[created["id"]]["email_config"]["subject"] = "Changed"

    with pytest.raises(ValueError, match="recorded Single Send"):
        campaigns.single_send(MailingPurpose.MONTHLY)


def test_ensure_list_adopts_unique_provider_list_after_local_state_loss(
    tmp_path,
):
    registry = _registry(tmp_path / "registry.json")
    api = FakeAPI()
    api.list_rows.append({
        "id": "existing-list",
        "name": "Yoga Habit: Interested: 2026_09",
    })
    campaigns = SendGridCampaigns(
        api=api,
        registry=registry,
        state_path=tmp_path / "state.json",
    )

    identifier = campaigns.ensure_list(
        "Yoga Habit: Interested: 2026_09"
    )

    assert identifier == "existing-list"
    assert api.created_lists == []
    assert SendGridRegistry.load(registry.path).list_id(
        "Yoga Habit: Interested: 2026_09"
    ) == "existing-list"


def test_ensure_segment_adopts_unique_provider_segment_after_state_loss(
    tmp_path,
):
    registry = _registry(tmp_path / "registry.json")
    api = FakeAPI()
    name = mailing_name(2026, 8, MailingPurpose.GENERAL_INVITATION)
    api.segments_by_id["existing-segment"] = {
        "id": "existing-segment",
        "name": name,
        "query_dsl": "SELECT contact_id FROM contact_data",
        "parent_list_ids": ["subscribed1"],
        "status": {"query_validation": "VALID"},
    }
    campaigns = SendGridCampaigns(
        api=api,
        registry=registry,
        state_path=tmp_path / "state.json",
    )

    segment = campaigns.ensure_segment(
        purpose=MailingPurpose.GENERAL_INVITATION,
        year=2026,
        month=8,
        query_dsl="SELECT contact_id FROM contact_data",
        parent_list_ids=["subscribed1"],
    )

    assert segment["id"] == "existing-segment"
    assert api.created_segments == []
    state = json.loads((tmp_path / "state.json").read_text())
    assert state["segments"]["General Invitation"]["id"] == "existing-segment"


def test_segment_is_idempotent_by_persisted_provider_id(tmp_path):
    registry = _registry(tmp_path / "registry.json")
    api = FakeAPI()
    campaigns = SendGridCampaigns(
        api=api,
        registry=registry,
        state_path=tmp_path / "state.json",
    )
    name = mailing_name(2026, 8, MailingPurpose.GENERAL_INVITATION)
    first = campaigns.ensure_segment(
        purpose=MailingPurpose.GENERAL_INVITATION,
        year=2026,
        month=8,
        query_dsl="SELECT contact_id, updated_at FROM contact_data",
        parent_list_ids=["subscribed1"],
    )
    second = campaigns.ensure_segment(
        purpose=MailingPurpose.GENERAL_INVITATION,
        year=2026,
        month=8,
        query_dsl="SELECT contact_id, updated_at FROM contact_data",
        parent_list_ids=["subscribed1"],
    )
    assert first["name"] == name
    assert second["id"] == first["id"]
    assert len(api.created_segments) == 1


def test_segment_updates_when_persisted_query_changes(tmp_path):
    registry = _registry(tmp_path / "registry.json")
    api = FakeAPI()
    state_path = tmp_path / "state.json"
    campaigns = SendGridCampaigns(
        api=api,
        registry=registry,
        state_path=state_path,
    )
    first = campaigns.ensure_segment(
        purpose=MailingPurpose.RESEND,
        year=2026,
        month=8,
        query_dsl="old single send id",
    )

    updated = campaigns.ensure_segment(
        purpose=MailingPurpose.RESEND,
        year=2026,
        month=8,
        query_dsl="new single send id",
    )

    assert updated["id"] == first["id"]
    assert updated["query_dsl"] == "new single send id"
    assert len(api.created_segments) == 1
    assert api.updated_segments == [
        (
            "segment1",
            {
                "name": mailing_name(2026, 8, MailingPurpose.RESEND),
                "query_dsl": "new single send id",
                "parent_list_ids": None,
            },
        )
    ]
    persisted = json.loads(state_path.read_text())
    assert persisted["segments"]["Resend"]["id"] == "segment1"
    assert persisted["segments"]["Resend"]["query_sha256"] != ""


def test_schedule_refuses_past_and_reschedules_exact_time(tmp_path):
    registry = _registry(tmp_path / "registry.json")
    api = FakeAPI()
    campaigns = SendGridCampaigns(
        api=api,
        registry=registry,
        state_path=tmp_path / "state.json",
        now_fn=lambda: datetime(2026, 8, 1, tzinfo=timezone.utc),
    )
    send = campaigns.create_draft(
        purpose=MailingPurpose.MONTHLY,
        year=2026,
        month=8,
        subject="August",
        body_md="Body",
        send_to={"list_ids": ["lifestyle1"], "all": False},
    )
    with pytest.raises(ValueError, match="past"):
        campaigns.schedule(
            MailingPurpose.MONTHLY,
            datetime(2026, 7, 31, tzinfo=timezone.utc),
        )

    target = datetime(2026, 8, 3, 15, 39, tzinfo=timezone.utc)
    campaigns.schedule(MailingPurpose.MONTHLY, target)
    assert api.scheduled == [("send1", "2026-08-03T15:39:00Z")]

    api.single_sends[send["id"]]["send_at"] = "2026-08-03T15:00:00Z"
    campaigns.schedule(MailingPurpose.MONTHLY, target)
    assert api.unscheduled == ["send1"]
    assert api.scheduled[-1] == ("send1", "2026-08-03T15:39:00Z")


def test_schedule_verifies_provider_state_and_cleans_failed_send(tmp_path):
    registry = _registry(tmp_path / "registry.json")
    api = FakeAPI()
    campaigns = SendGridCampaigns(
        api=api,
        registry=registry,
        state_path=tmp_path / "state.json",
        now_fn=lambda: datetime(2026, 8, 1, tzinfo=timezone.utc),
    )
    campaigns.create_draft(
        purpose=MailingPurpose.MONTHLY,
        year=2026,
        month=8,
        subject="August",
        body_md="Body",
        send_to={"list_ids": ["lifestyle1"], "all": False},
    )
    api.schedule_status = "draft"

    with pytest.raises(ValueError, match="schedule verification"):
        campaigns.schedule(
            MailingPurpose.MONTHLY,
            datetime(2026, 8, 3, 15, 39, tzinfo=timezone.utc),
        )

    assert api.deleted == ["send1"]


def test_expected_purposes_are_persisted_and_accumulate(tmp_path):
    registry = _registry(tmp_path / "registry.json")
    campaigns = SendGridCampaigns(
        api=FakeAPI(),
        registry=registry,
        state_path=tmp_path / "state.json",
    )

    campaigns.set_expected_purposes([MailingPurpose.MONTHLY])
    campaigns.set_expected_purposes([MailingPurpose.GENERAL_INVITATION])

    assert campaigns.expected_purposes() == [
        MailingPurpose.GENERAL_INVITATION,
        MailingPurpose.MONTHLY,
    ]
