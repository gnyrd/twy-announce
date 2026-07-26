import json
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
        self.scheduled = []
        self.unscheduled = []

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

    def schedule_single_send(self, identifier, send_at):
        self.scheduled.append((identifier, send_at))
        self.single_sends[identifier]["status"] = "scheduled"
        self.single_sends[identifier]["send_at"] = send_at
        return self.single_sends[identifier]

    def unschedule_single_send(self, identifier):
        self.unscheduled.append(identifier)
        self.single_sends[identifier]["status"] = "draft"
        return self.single_sends[identifier]


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


def test_create_draft_is_idempotent_and_preserves_existing_content(tmp_path):
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
        subject="Changed",
        body_md="Changed body",
        send_to={"list_ids": ["lifestyle1"], "all": False},
    )

    assert reused["id"] == created["id"]
    assert len(api.created_sends) == 1
    assert api.created_sends[0]["email_config"]["editor"] == "design"
    assert api.created_sends[0]["email_config"]["sender_id"] == 9423402
    assert api.created_sends[0]["email_config"][
        "suppression_group_id"
    ] == 35187
    persisted = json.loads(state_path.read_text())
    assert persisted["single_sends"]["Monthly"]["id"] == "send1"


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
