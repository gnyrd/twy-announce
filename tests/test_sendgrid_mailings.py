from datetime import date

import pytest

from sendgrid_mailings import (
    EMAIL_SUBSCRIBED,
    FORBIDDEN_NAME_CHARACTERS,
    MEMBER_YOGA_LIFESTYLE,
    MailingPurpose,
    habit_activity_name,
    general_invitation_query,
    mailing_name,
    mailing_schedule,
    non_opener_query,
    validate_sendgrid_name,
)


def test_locked_mailing_names_use_approved_shape():
    assert mailing_name(2026, 8, MailingPurpose.MONTHLY) == (
        "Yoga Lifestyle: 2026_08: Monthly"
    )
    assert mailing_name(2026, 8, MailingPurpose.GENERAL_INVITATION) == (
        "Yoga Habit: 2026_08: General Invitation"
    )
    assert mailing_name(2026, 8, MailingPurpose.FOLLOW_UP_2) == (
        "Yoga Habit: 2026_08: Follow Up 2"
    )
    assert EMAIL_SUBSCRIBED == "Email: Subscribed"
    assert MEMBER_YOGA_LIFESTYLE == "Member: Yoga Lifestyle"
    assert habit_activity_name(2026, 8, "Interested") == (
        "Yoga Habit: Interested: 2026_08"
    )
    assert habit_activity_name(2026, 8, "Registered") == (
        "Yoga Habit: Registered: 2026_08"
    )


@pytest.mark.parametrize("character", sorted(FORBIDDEN_NAME_CHARACTERS))
def test_locked_name_validator_rejects_every_forbidden_separator(character):
    with pytest.raises(ValueError, match="prohibited punctuation"):
        validate_sendgrid_name(f"Yoga Habit{character}2026_08")


def test_august_schedule_is_calculated_in_mountain_time():
    class_date = date(2026, 8, 8)
    assert mailing_schedule(
        2026, 8, MailingPurpose.MONTHLY, class_date
    ).isoformat() == "2026-08-03T15:39:00+00:00"
    assert mailing_schedule(
        2026, 8, MailingPurpose.GENERAL_INVITATION, class_date
    ).isoformat() == "2026-08-03T15:39:00+00:00"
    assert mailing_schedule(
        2026, 8, MailingPurpose.RESEND, class_date
    ).isoformat() == "2026-08-05T15:39:00+00:00"
    assert mailing_schedule(
        2026, 8, MailingPurpose.REGISTERED_REMINDER, class_date
    ).isoformat() == "2026-08-07T16:00:00+00:00"
    assert mailing_schedule(
        2026, 8, MailingPurpose.GENTLE_REMINDER, class_date
    ).isoformat() == "2026-08-07T23:00:00+00:00"
    assert mailing_schedule(
        2026, 8, MailingPurpose.FOLLOW_UP_1, class_date
    ).isoformat() == "2026-08-09T16:00:00+00:00"
    assert mailing_schedule(
        2026, 8, MailingPurpose.FOLLOW_UP_2, class_date
    ).isoformat() == "2026-08-15T16:00:00+00:00"


def test_monthly_schedule_does_not_require_a_habit_class():
    assert mailing_schedule(
        2026,
        8,
        MailingPurpose.MONTHLY,
        None,
    ).isoformat() == "2026-08-03T15:39:00+00:00"


def test_general_invitation_uses_subscriber_parent_and_excludes_members():
    query, parent_ids = general_invitation_query(
        subscribed_list_id="subscribed-id",
        member_list_id="member-id",
    )
    assert parent_ids == ["subscribed-id"]
    assert "NOT array_contains(list_ids, ['member-id'])" in query


def test_resend_is_exact_single_send_non_openers():
    query = non_opener_query("single-send-id")
    assert query.count("single-send-id") == 2
    assert "event_type = 'processed'" in query
    assert "event_type = 'open'" in query
    assert "NOT IN" in query
