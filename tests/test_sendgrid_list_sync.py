import json

from sendgrid_campaigns import SendGridRegistry
from sendgrid_list_sync import ensure_list, sync_exact_list


class FakeAPI:
    def __init__(self):
        self.lists = {
            "registered1": [
                {"id": "old1", "email": "old@example.com"},
                {"id": "keep1", "email": "keep@example.com"},
            ],
        }
        self.created = []
        self.upserts = []
        self.removals = []
        self.waits = []

    def create_list(self, name):
        self.created.append(name)
        return {"id": "newlist1", "name": name}

    def list_contacts(self, list_id):
        return list(self.lists.get(list_id, []))

    def upsert_contacts(self, list_ids, contacts):
        self.upserts.append((list_ids, contacts))
        return "upsert1"

    def remove_contacts_from_list(self, list_id, contact_ids):
        self.removals.append((list_id, contact_ids))
        return "remove1"

    def wait_contact_job(self, job_id, timeout_s=120):
        self.waits.append(job_id)
        return {"status": "completed"}


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
        "lists": {},
    }))
    return SendGridRegistry.load(path)


def test_ensure_list_creates_once_and_persists_provider_id(tmp_path):
    registry = _registry(tmp_path / "registry.json")
    api = FakeAPI()
    first = ensure_list(api, registry, "Yoga Habit: Registered: 2026_08")
    second = ensure_list(api, registry, "Yoga Habit: Registered: 2026_08")
    assert first == second == "newlist1"
    assert api.created == ["Yoga Habit: Registered: 2026_08"]


def test_exact_sync_adds_desired_and_removes_only_stale_members():
    api = FakeAPI()
    result = sync_exact_list(
        api=api,
        destination_list_id="registered1",
        desired_contacts=[
            {
                "email": "keep@example.com",
                "first_name": "Keep",
            },
            {
                "email": "new@example.com",
                "last_name": "New",
            },
        ],
        additive_list_ids=["interested1"],
    )
    assert api.upserts == [(
        ["registered1", "interested1"],
        [
            {"email": "keep@example.com", "first_name": "Keep"},
            {"email": "new@example.com", "last_name": "New"},
        ],
    )]
    assert api.removals == [("registered1", ["old1"])]
    assert api.waits == ["upsert1", "remove1"]
    assert result == {
        "desired": 2,
        "previous": 2,
        "removed": 1,
    }
