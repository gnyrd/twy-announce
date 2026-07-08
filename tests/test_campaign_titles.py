"""Byte-identity tests for the shared campaign-title builders (audit F08).

The MailChimp campaign title is the sole dedup key, so the builders must
reproduce the exact strings the previously hand-built f-strings emitted.
The expected literals below are hardcoded on purpose: they pin the byte
sequence, including the em-dash separators (U+2014, written as escapes to
keep this source ASCII). Never "fix" them to hyphens.
"""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
from mailchimp_campaigns import followup_campaign_title, monthly_campaign_title


def test_monthly_lifestyle():
    assert monthly_campaign_title(2026, 7, "Lifestyle") == "2026-07 \u2014 Lifestyle \u2014 Yoga Habit"


def test_monthly_non_lifestyle():
    assert monthly_campaign_title(2026, 7, "Non-Lifestyle") == "2026-07 \u2014 Non-Lifestyle \u2014 Yoga Habit"


def test_monthly_auto_segment_labels():
    assert monthly_campaign_title(2026, 7, "Non-Opener Resend") == "2026-07 \u2014 Non-Opener Resend \u2014 Yoga Habit"
    assert monthly_campaign_title(2026, 7, "Day-Before Reminder") == "2026-07 \u2014 Day-Before Reminder \u2014 Yoga Habit"
    assert monthly_campaign_title(2026, 7, "Gentle Nudge") == "2026-07 \u2014 Gentle Nudge \u2014 Yoga Habit"


def test_monthly_pads_single_digit_month():
    assert monthly_campaign_title(2026, 1, "Lifestyle") == "2026-01 \u2014 Lifestyle \u2014 Yoga Habit"


def test_followup_post_class_1():
    assert followup_campaign_title(2026, 7, "Post-Class 1") == "2026-07 \u2014 Yoga Habit \u2014 Post-Class 1"


def test_followup_post_class_2():
    assert followup_campaign_title(2026, 7, "Post-Class 2") == "2026-07 \u2014 Yoga Habit \u2014 Post-Class 2"


def test_followup_pads_single_digit_month():
    assert followup_campaign_title(2026, 1, "Post-Class 1") == "2026-01 \u2014 Yoga Habit \u2014 Post-Class 1"


def test_separators_are_em_dash_bytes():
    """The separator must encode to the UTF-8 em-dash bytes, exactly twice per title."""
    for title in (
        monthly_campaign_title(2026, 7, "Lifestyle"),
        followup_campaign_title(2026, 7, "Post-Class 1"),
    ):
        assert title.encode("utf-8").count(b"\xe2\x80\x94") == 2
