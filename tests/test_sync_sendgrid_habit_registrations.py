from datetime import date

import sync_sendgrid_habit_registrations as registration_sync


class _NoSuppressions:
    """api stub: nobody in this test has ever unsubscribed."""

    def search_group_suppressions(self, group_id, emails):
        return set()

    def remove_group_suppression(self, group_id, email):  # pragma: no cover
        raise AssertionError("nothing to renew")


class _Registry:
    suppression_group_id = 35187



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
        if "Attended" in name:
            return "attended1"
        return "subscribed1"

    def fake_sync(**kwargs):
        calls.append(kwargs)
        return {"desired": 2, "previous": 1, "removed": 0}

    monkeypatch.setattr(registration_sync, "ensure_list", fake_ensure)
    monkeypatch.setattr(registration_sync, "sync_exact_list", fake_sync)
    result = registration_sync.sync_event_lists(
        api=_NoSuppressions(),
        registry=_Registry(),
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
        "Yoga Habit: Attended: 2026_08",
    ]
    registered = [c for c in calls if c["destination_list_id"] == "registered1"]
    assert registered[0]["additive_list_ids"] == ["interested1", "subscribed1"]
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
        if "Attended" in name:
            return "attended1"
        return "subscribed1"

    def fake_sync(**kwargs):
        calls.append(kwargs)
        return {"desired": 1, "previous": 0, "removed": 0}

    monkeypatch.setattr(registration_sync, "ensure_list", fake_ensure)
    monkeypatch.setattr(registration_sync, "sync_exact_list", fake_sync)
    registration_sync.sync_event_lists(
        api=_NoSuppressions(),
        registry=_Registry(),
        event_date=date(2026, 8, 8),
        registrants=[{"email": "new@example.com"}],
    )

    registered = [c for c in calls if c["destination_list_id"] == "registered1"]
    assert registered[0]["additive_list_ids"] == ["interested1", "subscribed1"]


def test_attendees_get_their_own_list(monkeypatch):
    """Attending is a distinct fact from registering, so it gets its own list."""
    calls = []

    def fake_ensure(api, registry, name):
        return {
            'Registered': 'registered1',
            'Interested': 'interested1',
            'Attended': 'attended1',
        }.get(next((k for k in ('Registered', 'Interested', 'Attended') if k in name), ''), 'subscribed1')

    monkeypatch.setattr(registration_sync, 'ensure_list', fake_ensure)
    monkeypatch.setattr(registration_sync, 'sync_exact_list',
                        lambda **kw: calls.append(kw) or {'desired': 1, 'previous': 0, 'removed': 0})
    registration_sync.sync_event_lists(
        api=_NoSuppressions(), registry=_Registry(), event_date=date(2026, 8, 8),
        registrants=[
            {'email': 'went@example.com', 'attended': True},
            {'email': 'skipped@example.com', 'attended': False},
        ],
    )

    attended = [c for c in calls if c['destination_list_id'] == 'attended1']
    assert len(attended) == 1
    assert [c['email'] for c in attended[0]['desired_contacts']] == ['went@example.com']


def test_registering_clears_a_previous_unsubscribe(monkeypatch):
    """JP 2026-08-08: registering for a free class is an opt-in that supersedes
    any earlier opt-out, so the registrant comes out of the suppression group."""
    removed = []

    class FakeAPI:
        def search_group_suppressions(self, gid, emails):
            return {'optedout@example.com'} if not removed else set()

        def remove_group_suppression(self, gid, email):
            removed.append((gid, email))

    class FakeRegistry:
        suppression_group_id = 35187

    monkeypatch.setattr(registration_sync, 'ensure_list', lambda a, r, n: 'l1')
    monkeypatch.setattr(registration_sync, 'sync_exact_list',
                        lambda **kw: {'desired': 2, 'previous': 0, 'removed': 0})
    registration_sync.sync_event_lists(
        api=FakeAPI(), registry=FakeRegistry(), event_date=date(2026, 8, 8),
        registrants=[
            {'email': 'optedout@example.com'},
            {'email': 'fine@example.com'},
        ],
    )

    assert removed == [(35187, 'optedout@example.com')]


def test_registrants_carry_the_attended_flag():
    class FakeClient:
        def get_event(self, event_id):
            return {'registrations': [
                {'student_email': 'A@Example.com', 'attended': True},
                {'student_email': 'b@example.com', 'attended': False},
            ]}

    got = registration_sync.registrants_for_event(FakeClient(), 1)
    assert {c['email']: c['attended'] for c in got} == {
        'a@example.com': True, 'b@example.com': False}


def test_registrants_resolve_prospects_and_account_holders():
    """HM populates the two identity shapes exclusively, never both.

    A prospect typed their details into the registration form, so the top-level
    registrant fields carry them and there is no user_id. An existing student
    clicked register, so HM leaves those fields empty and the identity lives on
    the nested student object alone. Verified against event 1012621 on
    2026-08-08: 21 prospects with top-level values and no user_id, 5 account
    holders with user_id and empty top-level values, and zero disagreements
    between the two shapes. Both branches are load-bearing; dropping either
    silently loses a whole class of registrant.
    """
    class FakeClient:
        def get_event(self, event_id):
            return {'registrations': [
                {  # prospect: form values on the registration, no account
                    'student_email': 'Prospect@Example.com',
                    'student_first_name': 'Pro',
                    'student_last_name': 'Spect',
                    'user_id': None,
                    'attended': True,
                    'student': {'email': 'prospect@example.com',
                                'is_prospect': True},
                },
                {  # account holder: identity only on the nested student
                    'student_email': '',
                    'student_first_name': '',
                    'student_last_name': '',
                    'user_id': 411057,
                    'attended': False,
                    'student': {'email': 'Member@Example.com',
                                'first_name': 'Mem', 'last_name': 'Ber'},
                },
            ]}

    got = {c['email']: c for c in registration_sync.registrants_for_event(FakeClient(), 1)}
    assert set(got) == {'prospect@example.com', 'member@example.com'}
    assert got['prospect@example.com']['first_name'] == 'Pro'
    assert got['prospect@example.com']['attended'] is True
    assert got['member@example.com']['first_name'] == 'Mem'
    assert got['member@example.com']['attended'] is False
