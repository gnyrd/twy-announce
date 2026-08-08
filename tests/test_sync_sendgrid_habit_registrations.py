from datetime import date

import sync_sendgrid_habit_registrations as registration_sync


def test_registration_sync_uses_locked_registered_and_interested_lists(
    monkeypatch,
):
    ensured = []
    calls = []

    def fake_ensure(api, registry, name):
        ensured.append(name)
        if "Registered" in name:
            return "registered1"
        if "Interested" in name:
            return "interested1"
        return "subscribed1"

    def fake_sync(**kwargs):
        calls.append(kwargs)
        return {"desired": 2, "previous": 1, "removed": 0}

    monkeypatch.setattr(registration_sync, "ensure_list", fake_ensure)
    monkeypatch.setattr(registration_sync, "sync_exact_list", fake_sync)
    result = registration_sync.sync_event_lists(
        api=object(),
        registry=object(),
        event_date=date(2026, 8, 8),
        registrants=[
            {"email": "a@example.com", "first_name": "A"},
            {"email": "b@example.com", "last_name": "B"},
        ],
    )

    assert ensured == [
        "Yoga Habit: Interested: 2026_08",
        "Yoga Habit: Registered: 2026_08",
        "Email: Subscribed",
    ]
    assert calls[0]["destination_list_id"] == "registered1"
    assert calls[0]["additive_list_ids"] == ["interested1", "subscribed1"]
    assert result["registered_list_id"] == "registered1"


def test_registrants_are_added_to_the_subscriber_audience(monkeypatch):
    """Registering for a free Habit class puts a person in the mailing audience.

    Email: Subscribed is an audience list, not a consent record. Opt-out is
    enforced by the ASM suppression group Email: Unsubscribed (id 35187), which
    SendGrid applies at send time whatever list a contact sits on, so adding a
    registrant here cannot override anyone's unsubscribe.
    """
    calls = []

    def fake_ensure(api, registry, name):
        if "Registered" in name:
            return "registered1"
        if "Interested" in name:
            return "interested1"
        return "subscribed1"

    def fake_sync(**kwargs):
        calls.append(kwargs)
        return {"desired": 1, "previous": 0, "removed": 0}

    monkeypatch.setattr(registration_sync, "ensure_list", fake_ensure)
    monkeypatch.setattr(registration_sync, "sync_exact_list", fake_sync)
    registration_sync.sync_event_lists(
        api=object(),
        registry=object(),
        event_date=date(2026, 8, 8),
        registrants=[{"email": "new@example.com"}],
    )

    assert calls[0]["destination_list_id"] == "registered1"
    assert calls[0]["additive_list_ids"] == ["interested1", "subscribed1"]
