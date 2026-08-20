"""Per-period content for a recurring campaign: an email may draw its copy from
that month's newsletter draft instead of carrying static text.

A campaign email carrying a `section` resolves its subject, preheader and body
from the period's draft (injected as `sections`) at launch. When that draft is
not ready, the email holds this period like a gated one, and the rest of the
campaign still sends. An email with no section keeps its static copy, so one-off
campaigns (Transitions) are unchanged.
"""
import os
import sys
from datetime import date, datetime, timezone

sys.path.insert(0, os.path.dirname(__file__))

from campaign_launch import CampaignLauncher, GateContext
from test_campaign_launch import FakeAPI, FakeRegistry, SEGMENT_ID


NOW = datetime(2026, 8, 18, 12, 0, tzinfo=timezone.utc)


def _campaign(emails):
    return {
        "type": "campaign",
        "journey_id": "yoga_habit",
        "name": "Yoga Habit",
        "campaign_month": "2026_09",
        "segment_id": SEGMENT_ID,
        "emails": emails,
    }


def _launcher(emails, tmp_path, sections=None, api=None):
    return CampaignLauncher(
        api=api if api is not None else FakeAPI(),
        registry=FakeRegistry(),
        journey=_campaign(emails),
        state_path=tmp_path / "state.json",
        now_fn=lambda: NOW,
        gate_context=GateContext(class_date=date(2026, 9, 12)),
        sections=sections,
    )


def test_section_email_resolves_content_from_the_period_draft(tmp_path):
    sections = {"non_lifestyle": {
        "subject": "Come to the free class",
        "preheader": "Saturday at 9",
        "body": "The invitation body for this month.",
    }}
    launcher = _launcher(
        [{"section": "non_lifestyle", "subject": "STATIC", "body": "STATIC"}],
        tmp_path, sections=sections,
    )
    content = launcher._email_content(launcher.journey["emails"][0])
    assert content["subject"] == "Come to the free class"
    assert content["body"] == "The invitation body for this month."
    assert content["preheader"] == "Saturday at 9"


def test_email_without_a_section_keeps_its_static_copy(tmp_path):
    launcher = _launcher(
        [{"subject": "Typed here", "preheader": "ph", "body": "Static body."}],
        tmp_path, sections={},
    )
    content = launcher._email_content(launcher.journey["emails"][0])
    assert content == {"subject": "Typed here", "preheader": "ph", "body": "Static body."}


def test_section_email_with_no_draft_this_period_is_pending(tmp_path):
    launcher = _launcher(
        [{"section": "non_lifestyle", "subject": "STATIC", "body": "STATIC"}],
        tmp_path, sections={},
    )
    assert launcher._email_content(launcher.journey["emails"][0]) is None


def test_launch_sends_the_draft_copy_for_a_section_email(tmp_path):
    api = FakeAPI()
    sections = {"non_lifestyle": {
        "subject": "September invitation",
        "preheader": "join us",
        "body": "This months invitation copy.",
    }}
    launcher = _launcher(
        [{"section": "non_lifestyle", "subject": "STATIC", "body": "STATIC"}],
        tmp_path, sections=sections, api=api,
    )
    launcher.launch(date(2026, 9, 1))
    created = api.created_single_sends[0]
    assert created["email_config"]["subject"] == "September invitation"


def test_launch_holds_a_pending_section_email_and_sends_the_rest(tmp_path):
    api = FakeAPI()
    sections = {"non_lifestyle": {
        "subject": "Invite", "preheader": "", "body": "Invite body.",
    }}
    # Email 0 has its draft; email 1 points at a section with no draft this period.
    launcher = _launcher(
        [
            {"section": "non_lifestyle", "subject": "S", "body": "B"},
            {"section": "reminder", "subject": "S2", "body": "B2",
             "send_date": "2026-09-11"},
        ],
        tmp_path, sections=sections, api=api,
    )
    result = launcher.launch(date(2026, 9, 1))
    pending = [r for r in result["sends"] if r.get("content_pending")]
    assert len(api.created_single_sends) == 1  # only email 0 sent
    assert len(pending) == 1
    assert pending[0].get("skipped") is True


def test_resend_of_a_section_parent_inherits_the_draft_copy(tmp_path):
    api = FakeAPI()
    sections = {"non_lifestyle": {
        "subject": "Parent draft subject",
        "preheader": "ph",
        "body": "Parent draft body.",
    }}
    launcher = _launcher(
        [{"section": "non_lifestyle", "subject": "S", "body": "B",
          "resend": {"wait_days": 2}}],
        tmp_path, sections=sections, api=api,
    )
    launcher.launch(date(2026, 9, 1))
    names = [c["email_config"]["subject"] for c in api.created_single_sends]
    # Parent and its resend child both carry the parent draft's subject.
    assert names == ["Parent draft subject", "Parent draft subject"]


def test_campaign_sections_match_the_newsletter_section_keys():
    """The shared CAMPAIGN_SECTIONS vocabulary and the newsletter workflow's own
    SECTION_PURPOSES must name the same sections, or a campaign could tag an email
    with a section the draft store never fills."""
    from twy_platform.journeys import CAMPAIGN_SECTIONS
    from sendgrid_newsletter_workflow import SECTION_PURPOSES

    assert set(CAMPAIGN_SECTIONS) == set(SECTION_PURPOSES)
