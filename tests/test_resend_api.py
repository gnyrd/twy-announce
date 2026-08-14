import pytest

from resend_api import ResendAPI, ResendAPIError, to_resend_payload


SENDGRID_PAYLOAD = {
    "personalizations": [{"to": [{"email": "member@example.com"}]}],
    "from": {"email": "hello@mail.tiffanywoodyoga.com", "name": "Tiffany Wood Yoga"},
    "reply_to": {"email": "hello@tiffanywoodyoga.com", "name": "Tiffany Wood Yoga"},
    "subject": "Welcome",
    "content": [
        {"type": "text/plain", "value": "plain body"},
        {"type": "text/html", "value": "<p>html body</p>"},
    ],
    "asm": {"group_id": 12345, "groups_to_display": [12345]},
    "mail_settings": {"footer": {"enable": False}},
    "tracking_settings": {"subscription_tracking": {"enable": False}},
    "custom_args": {"twy_campaign_id": "journey:yoga_lifestyle_welcome_2024_05:0"},
}

FROM = "Tiffany Wood Yoga <hello@mail.tiffanywoodyoga.com>"


def test_translation_keeps_the_parts_that_carry_meaning():
    body = to_resend_payload(SENDGRID_PAYLOAD, from_address=FROM)
    assert body["to"] == ["member@example.com"]
    assert body["subject"] == "Welcome"
    assert body["html"] == "<p>html body</p>"
    assert body["text"] == "plain body"
    assert body["from"] == FROM


def test_reply_to_stays_on_the_apex_so_replies_reach_google_workspace():
    body = to_resend_payload(SENDGRID_PAYLOAD, from_address=FROM)
    assert body["reply_to"] == "Tiffany Wood Yoga <hello@tiffanywoodyoga.com>"
    assert "mail.tiffanywoodyoga.com" not in body["reply_to"]


def test_campaign_id_survives_as_a_tag_with_colons_made_safe():
    """Resend tags take letters, numbers, underscore and dash only.

    The id is the ONLY way an open or bounce weeks later ties back to a
    journey and an email number, so it has to survive the move intact enough
    to read back.
    """
    body = to_resend_payload(SENDGRID_PAYLOAD, from_address=FROM)
    assert body["tags"] == [
        {
            "name": "twy_campaign_id",
            "value": "journey_yoga_lifestyle_welcome_2024_05_0",
        }
    ]


def test_sendgrid_only_blocks_are_dropped_rather_than_passed_through():
    body = to_resend_payload(SENDGRID_PAYLOAD, from_address=FROM)
    for key in ("asm", "mail_settings", "tracking_settings", "personalizations"):
        assert key not in body


def test_a_send_without_a_recipient_is_refused():
    payload = dict(SENDGRID_PAYLOAD, personalizations=[{"to": []}])
    with pytest.raises(ResendAPIError):
        to_resend_payload(payload, from_address=FROM)


def test_a_send_without_a_body_is_refused():
    payload = dict(SENDGRID_PAYLOAD, content=[])
    with pytest.raises(ResendAPIError):
        to_resend_payload(payload, from_address=FROM)


def test_sender_off_the_verified_domain_is_refused_at_construction():
    """Fail once at startup, not silently on every send."""
    with pytest.raises(ValueError):
        ResendAPI("re_test", from_address="Tiff <hello@tiffanywoodyoga.com>")


def test_sender_on_the_verified_domain_constructs():
    assert ResendAPI("re_test", from_address=FROM) is not None
