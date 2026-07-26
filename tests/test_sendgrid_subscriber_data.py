import json

import sendgrid_subscriber_data as subscriber_data


class FakeAPI:
    def list_contacts(self, list_id):
        assert list_id == "subscribed1"
        return [
            {"email": "a@example.com"},
            {"email": "b@example.com"},
        ]


class FakeRegistry:
    def list_id(self, name):
        assert name == "Email: Subscribed"
        return "subscribed1"


def test_snapshot_uses_locked_subscriber_list_and_provider_neutral_shape(
    tmp_path,
):
    snapshot = subscriber_data.collect_snapshot(
        api=FakeAPI(),
        registry=FakeRegistry(),
        captured_at="2026-07-25T12:00:00Z",
    )
    path = subscriber_data.save_snapshot(
        snapshot,
        date_string="2026-07-25",
        history_dir=tmp_path,
    )

    assert snapshot == {
        "captured_at": "2026-07-25T12:00:00Z",
        "list_name": "Email: Subscribed",
        "subscriber_count": 2,
    }
    assert json.loads(path.read_text()) == snapshot
