import json
from datetime import date

from sendgrid_campaigns import SendGridCampaigns, SendGridRegistry
from sendgrid_mailings import MailingPurpose, mailing_name
from sendgrid_newsletter_workflow import provision_drafts


class FakeAPI:
    def __init__(self):
        self.created_lists = []
        self.created_segments = []
        self.created_sends = []
        self.single_sends = {}
        self.segment_rows = {}

    def create_list(self, name):
        self.created_lists.append(name)
        return {"id": f"list{len(self.created_lists)}", "name": name}

    def create_segment(self, **payload):
        identifier = f"segment{len(self.created_segments) + 1}"
        item = {
            "id": identifier,
            **payload,
            "status": {"query_validation": "VALID"},
        }
        self.created_segments.append(payload)
        self.segment_rows[identifier] = item
        return item

    def segment(self, identifier):
        return self.segment_rows[identifier]

    def create_single_send(self, payload):
        identifier = f"send{len(self.created_sends) + 1}"
        item = {
            "id": identifier,
            "name": payload["name"],
            "status": "draft",
            "send_to": payload["send_to"],
            "email_config": payload["email_config"],
        }
        self.created_sends.append(payload)
        self.single_sends[identifier] = item
        return item

    def get_single_send(self, identifier):
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
            "Email: Subscribed": {"id": "subscribed1"},
            "Member: Yoga Lifestyle": {"id": "member1"},
        },
    }))
    return SendGridRegistry.load(path)


def _sections():
    return {
        "lifestyle": {"subject": "Monthly", "body": "Monthly body"},
        "non_lifestyle": {
            "subject": "Invitation",
            "body": "Invitation body",
        },
        "non_opener": {"subject": "Again", "body": "Resend body"},
        "gentle_nudge": {"subject": "A note", "body": "Gentle body"},
        "reminder": {"subject": "Tomorrow", "body": "Reminder body"},
        "ph1": {"subject": "Thank you", "body": "First follow up"},
        "ph2": {"subject": "One more", "body": "Second follow up"},
    }


def test_provision_creates_locked_lists_segments_and_all_seven_drafts(
    tmp_path,
):
    api = FakeAPI()
    registry = _registry(tmp_path / "registry.json")
    campaigns = SendGridCampaigns(
        api=api,
        registry=registry,
        state_path=tmp_path / "state.json",
    )
    result = provision_drafts(
        campaigns=campaigns,
        year=2026,
        month=8,
        class_date=date(2026, 8, 8),
        sections=_sections(),
    )

    assert api.created_lists == [
        "Yoga Habit: Interested: 2026_08",
        "Yoga Habit: Registered: 2026_08",
    ]
    assert set(result) == set(_sections())
    assert len(api.created_sends) == 7
    assert len(api.created_segments) == 5
    assert {
        payload["name"] for payload in api.created_sends
    } == {
        mailing_name(2026, 8, purpose)
        for purpose in MailingPurpose
    }

    monthly = next(
        payload for payload in api.created_sends
        if payload["name"].endswith(": Monthly")
    )
    registered = next(
        payload for payload in api.created_sends
        if payload["name"].endswith(": Registered Reminder")
    )
    assert monthly["send_to"]["list_ids"] == ["member1"]
    registered_list_id = SendGridRegistry.load(
        registry.path
    ).list_id("Yoga Habit: Registered: 2026_08")
    assert registered["send_to"]["list_ids"] == [registered_list_id]


def test_partial_registered_reminder_does_not_require_general_draft(tmp_path):
    api = FakeAPI()
    registry = _registry(tmp_path / "registry.json")
    campaigns = SendGridCampaigns(
        api=api,
        registry=registry,
        state_path=tmp_path / "state.json",
    )
    result = provision_drafts(
        campaigns=campaigns,
        year=2026,
        month=8,
        class_date=date(2026, 8, 8),
        sections={
            "reminder": {
                "subject": "Tomorrow",
                "body": "Reminder body",
            },
        },
    )
    assert set(result) == {"reminder"}


def test_monthly_only_does_not_require_a_habit_class(tmp_path):
    api = FakeAPI()
    registry = _registry(tmp_path / "registry.json")
    campaigns = SendGridCampaigns(
        api=api,
        registry=registry,
        state_path=tmp_path / "state.json",
    )

    result = provision_drafts(
        campaigns=campaigns,
        year=2026,
        month=8,
        class_date=None,
        sections={
            "lifestyle": {
                "subject": "Monthly",
                "body": "Monthly body",
            }
        },
    )

    assert set(result) == {"lifestyle"}
    assert api.created_lists == []
    assert not api.created_segments
