import json

import sendgrid_subscriber_data as subscriber_data


def test_snapshot_uses_locked_subscriber_list_and_provider_neutral_shape(
    tmp_path, monkeypatch,
):
    monkeypatch.setattr(
        subscriber_data,
        "subscribed_count",
        lambda api_key, *, list_id: 921 if list_id == "subscribed1" else None,
    )
    snapshot = subscriber_data.collect_snapshot(
        api_key="key",
        list_id="subscribed1",
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
        "subscriber_count": 921,
    }
    assert json.loads(path.read_text()) == snapshot
