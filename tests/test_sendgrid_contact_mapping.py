import pytest

from sendgrid_contact_mapping import (
    MappingError,
    SendGridSafetyState,
    SourceContact,
    map_contact,
    map_contacts,
)


def source(status, email=" Person@Example.COM ", tags=()):
    return SourceContact(
        email=email,
        status=status,
        tags=frozenset(tags),
        merge_fields={"FNAME": "Person", "LNAME": "Example"},
        last_changed="2026-07-24T00:00:00Z",
        source_id="mc-1",
    )


@pytest.mark.parametrize(
    ("status", "terminal"),
    [
        ("subscribed", "deliverable"),
        ("unsubscribed", "marketing_suppressed"),
        ("cleaned", "cleaned_denylist"),
        ("pending", "quarantine"),
        ("transactional", "quarantine"),
        ("archived", "archived_excluded"),
        ("new-status", "quarantine"),
    ],
)
def test_mailchimp_status_mapping_is_explicit(status, terminal):
    assert map_contact(source(status), SendGridSafetyState()).terminal_class == terminal


@pytest.mark.parametrize(
    "field",
    ["global_suppressed", "group_suppressed"],
)
def test_sendgrid_suppression_overrides_mailchimp_subscribed(field):
    state = SendGridSafetyState(**{field: True})
    mapped = map_contact(source("subscribed"), state)
    assert mapped.terminal_class == "marketing_suppressed"
    assert mapped.terminal_class != "deliverable"


@pytest.mark.parametrize(
    "field",
    ["bounced", "blocked", "invalid", "spam_reported"],
)
def test_sendgrid_hard_failure_overrides_mailchimp_subscribed(field):
    state = SendGridSafetyState(**{field: True})
    mapped = map_contact(source("subscribed"), state)
    assert mapped.terminal_class == "cleaned_denylist"
    assert mapped.terminal_class != "deliverable"


def test_contact_search_error_quarantines():
    mapped = map_contact(
        source("subscribed"),
        SendGridSafetyState(lookup_error="backend unavailable"),
    )
    assert mapped.terminal_class == "quarantine"


def test_archived_is_excluded_without_profile_or_lists():
    mapped = map_contact(source("archived"), SendGridSafetyState())
    assert mapped.terminal_class == "archived_excluded"
    assert mapped.proposed_lists == frozenset()
    assert mapped.custom_fields == {}
    assert mapped.reasons == ("mailchimp_archived",)


def test_email_is_normalized_and_provenance_preserved():
    mapped = map_contact(source("subscribed"), SendGridSafetyState())
    assert mapped.email == "person@example.com"
    assert mapped.provenance["mailchimp_status"] == "subscribed"
    assert mapped.provenance["mailchimp_source_id"] == "mc-1"


def test_duplicate_source_identity_fails_closed():
    with pytest.raises(MappingError, match="duplicate"):
        map_contacts(
            [source("subscribed"), source("unsubscribed")],
            {},
        )


def test_missing_sendgrid_safety_state_fails_closed():
    result = map_contacts([source("subscribed")], {})
    assert result[0].terminal_class == "quarantine"
    assert result[0].reasons == ("sendgrid_lookup_error",)


def test_each_unique_email_has_exactly_one_terminal_class():
    mapped = map_contacts(
        [
            source("subscribed", "a@example.com"),
            source("unsubscribed", "b@example.com"),
            source("cleaned", "c@example.com"),
        ],
        {},
    )
    assert len(mapped) == 3
    assert {item.email for item in mapped} == {
        "a@example.com",
        "b@example.com",
        "c@example.com",
    }


def test_required_lists_preserve_current_twy_semantics():
    mapped = map_contact(
        source(
            "subscribed",
            tags={
                "Membership - Yoga Lifestyle",
                "Status - Member",
                "Role - Admin",
                "New Subscriber YLS Membership",
                "Yoga Habit - 2026-07",
            },
        ),
        SendGridSafetyState(),
    )
    assert mapped.proposed_lists == frozenset({
        "TWY Marketing",
        "TWY Yoga Lifestyle",
        "TWY Welcome 3209",
        "TWY Yoga Habit 2026-07",
    })
    assert mapped.custom_fields["twy_status"] == "member"
    assert mapped.custom_fields["twy_role"] == "admin"
