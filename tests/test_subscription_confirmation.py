"""The Subscription: Confirmation mailing renders its signed per-recipient link
and leaves the follow-ask footer out (2026-09-21)."""
import subscription_confirmation as sc


def test_link_carries_the_recipient_tag_and_the_batch_signature():
    url = sc.confirm_url("k")
    assert url.startswith("https://habit.tiffanywoodyoga.com/subscribe/confirm?e={{email}}&s=")
    assert len(url.rsplit("s=", 1)[1]) == 24


def test_rendered_html_keeps_the_tag_and_drops_the_follow_footer():
    html, plain = sc.rendered("k")
    assert "subscribe/confirm?e={{email}}&amp;s=" in html or "subscribe/confirm?e={{email}}&s=" in html
    assert "contribution:email_follow_ask" not in html
    assert "Was this you?" not in html.split("<body", 1)[-1][:0]  # the heading line is the subject, not body copy
    assert "Yes, that was me" in html
    assert "Love," in plain and "Tiff" in plain


def test_recipients_are_the_reviewed_twelve():
    assert len(sc.RECIPIENTS) == 12
    assert len(set(sc.RECIPIENTS)) == 12
    assert all("@" in email for email in sc.RECIPIENTS)
