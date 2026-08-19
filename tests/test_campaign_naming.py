import pytest

from sendgrid_mailings import (
    campaign_label,
    campaign_non_opener_segment_name,
    campaign_resend_send_name,
    campaign_single_send_name,
)


def test_campaign_label_format():
    assert campaign_label("Transitions", 2026, 9) == "Campaign: Transitions: 2026_09"


def test_campaign_label_collapses_whitespace():
    assert campaign_label("  Fall   Reset ", 2026, 9) == (
        "Campaign: Fall Reset: 2026_09"
    )


def test_campaign_label_rejects_empty_name():
    with pytest.raises(ValueError, match="campaign name must not be empty"):
        campaign_label("   ", 2026, 9)


def test_campaign_label_rejects_hyphen():
    with pytest.raises(ValueError, match="prohibited punctuation"):
        campaign_label("Back-to-Routine", 2026, 9)


def test_campaign_label_rejects_bad_month():
    with pytest.raises(ValueError, match="month must be"):
        campaign_label("Transitions", 2026, 13)


def test_single_send_name_is_one_based_and_date_first():
    assert campaign_single_send_name(2026, 9, "Transitions", 0) == (
        "2026_09: Transitions: Email 1"
    )
    assert campaign_single_send_name(2026, 9, "Transitions", 2) == (
        "2026_09: Transitions: Email 3"
    )


def test_single_send_name_rejects_negative_index():
    with pytest.raises(ValueError, match="email index cannot be negative"):
        campaign_single_send_name(2026, 9, "Transitions", -1)


def test_single_send_name_rejects_hyphen():
    with pytest.raises(ValueError, match="prohibited punctuation"):
        campaign_single_send_name(2026, 9, "Back-to-Routine", 0)


def test_single_send_name_rejects_bad_month():
    with pytest.raises(ValueError, match="month must be"):
        campaign_single_send_name(2026, 0, "Transitions", 0)


def test_resend_send_name_extends_the_email_name():
    assert campaign_resend_send_name(2026, 9, "Transitions", 2) == (
        "2026_09: Transitions: Email 3: Resend"
    )


def test_non_opener_segment_name_extends_the_email_name():
    assert campaign_non_opener_segment_name(2026, 9, "Transitions", 2) == (
        "2026_09: Transitions: Email 3: Non Openers"
    )


def test_resend_names_reject_hyphen():
    with pytest.raises(ValueError, match="prohibited punctuation"):
        campaign_resend_send_name(2026, 9, "Back-to-Routine", 0)
    with pytest.raises(ValueError, match="prohibited punctuation"):
        campaign_non_opener_segment_name(2026, 9, "Back-to-Routine", 0)
