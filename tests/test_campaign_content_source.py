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


def _launcher(emails, tmp_path, sections=None, api=None, approvals=None):
    return CampaignLauncher(
        api=api if api is not None else FakeAPI(),
        registry=FakeRegistry(),
        journey=_campaign(emails),
        state_path=tmp_path / "state.json",
        now_fn=lambda: NOW,
        gate_context=GateContext(class_date=date(2026, 9, 12)),
        sections=sections,
        # Tests supply drafts as already approved unless a test is about the
        # per-month approval hold itself.
        section_approvals=(
            approvals if approvals is not None
            else {key: True for key in (sections or {})}
        ),
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


def test_a_draft_with_an_unresolved_token_holds_rather_than_sending_it(tmp_path):
    """The campaign content path does not resolve tokens; a draft still carrying
    one must hold, never send a literal {CLASS_TITLE}."""
    sections = {"recording": {
        "subject": "Your {CLASS_TITLE} recording",
        "preheader": "",
        "body": "Thank you for joining {CLASS_TITLE}.",
    }}
    launcher = _launcher(
        [{"section": "recording", "subject": "S", "body": "B"}],
        tmp_path, sections=sections,
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


def test_unresolved_token_guard_is_imported_not_a_duplicate_literal():
    """campaign_launch's fail-closed guard must be the newsletter workflow's own
    UNRESOLVED_TOKEN, imported, never an independent re.compile literal that
    could silently drift out of sync with it (the two were separate literals
    until this test)."""
    import inspect
    import campaign_launch
    import sendgrid_newsletter_workflow

    source = inspect.getsource(campaign_launch)
    assert "re.compile" not in source
    assert (
        campaign_launch._UNRESOLVED_TOKEN
        is sendgrid_newsletter_workflow.UNRESOLVED_TOKEN
    )


def test_launch_holds_a_section_email_until_its_period_draft_is_approved(tmp_path):
    """Approval is per month (JP 2026-08-24): the same email that sends when
    its period draft is approved holds when it is not."""
    api = FakeAPI()
    sections = {"non_lifestyle": {
        "subject": "Invite", "preheader": "", "body": "Invite body.",
    }}
    launcher = _launcher(
        [{"section": "non_lifestyle", "subject": "S", "body": "B"}],
        tmp_path, sections=sections, api=api,
        approvals={"non_lifestyle": False},
    )
    result = launcher.launch(date(2026, 9, 1))
    pending = [r for r in result["sends"] if r.get("content_pending")]
    assert api.created_single_sends == []
    assert len(pending) == 1
    assert pending[0].get("skipped") is True


# -- a resend child with a draft of its own (JP 2026-09-30) -------------------
# The legacy Resend mailing sent the Non-Opener Resend draft. The cutover's
# default, a resend inherits its parent's copy, left that draft written,
# approved and unsent. A resend carrying `section` sends that draft instead.

RESEND_SECTIONS = {
    "non_lifestyle": {
        "subject": "Come to the free class",
        "preheader": "Saturday at 9",
        "body": "The invitation body.",
    },
    "non_opener": {
        "subject": "A different way in",
        "preheader": "Second look",
        "body": "The second-look body for non-openers.",
    },
}


def _invitation_with_resend(resend):
    return [{"section": "non_lifestyle", "subject": "STATIC", "body": "STATIC",
             "interval_days": 0, "resend": resend}]


def test_resend_child_sends_its_own_section_draft(tmp_path):
    api = FakeAPI()
    launcher = _launcher(
        _invitation_with_resend({"wait_days": 2, "section": "non_opener"}),
        tmp_path, sections=RESEND_SECTIONS, api=api,
    )
    launcher.launch(date(2026, 9, 12))
    parent, child = api.created_single_sends
    assert parent["email_config"]["subject"] == "Come to the free class"
    assert child["email_config"]["subject"] == "A different way in"
    # email_config carries rendered html and plain text, not the raw fields
    assert "second-look body for non-openers" in child["email_config"]["plain_content"]
    assert "The invitation body" not in child["email_config"]["plain_content"]


def test_resend_child_without_a_section_still_inherits_the_parent_draft(tmp_path):
    api = FakeAPI()
    launcher = _launcher(
        _invitation_with_resend({"wait_days": 2}),
        tmp_path, sections=RESEND_SECTIONS, api=api,
    )
    launcher.launch(date(2026, 9, 12))
    parent, child = api.created_single_sends
    assert child["email_config"]["subject"] == parent["email_config"]["subject"]
    assert child["email_config"]["plain_content"] == parent["email_config"]["plain_content"]


def test_resend_child_holds_on_its_own_unapproved_draft_and_the_parent_still_sends(tmp_path):
    api = FakeAPI()
    launcher = _launcher(
        _invitation_with_resend({"wait_days": 2, "section": "non_opener"}),
        tmp_path, sections=RESEND_SECTIONS, api=api,
        approvals={"non_lifestyle": True, "non_opener": False},
    )
    result = launcher.launch(date(2026, 9, 12))
    assert len(api.created_single_sends) == 1  # the invitation went, alone
    child = next(r for r in result["sends"] if r.get("resend_of") == 0)
    assert child["content_pending"] is True
    assert child["skipped"] is True
    assert child["section"] == "non_opener"


def test_resend_child_holds_when_its_own_draft_is_missing(tmp_path):
    api = FakeAPI()
    only_the_invitation = {"non_lifestyle": RESEND_SECTIONS["non_lifestyle"]}
    launcher = _launcher(
        _invitation_with_resend({"wait_days": 2, "section": "non_opener"}),
        tmp_path, sections=only_the_invitation, api=api,
    )
    result = launcher.launch(date(2026, 9, 12))
    assert len(api.created_single_sends) == 1
    child = next(r for r in result["sends"] if r.get("resend_of") == 0)
    assert child["content_pending"] is True


def test_a_held_resend_is_provisioned_on_a_later_run_once_its_draft_is_approved(tmp_path):
    api = FakeAPI()
    emails = _invitation_with_resend({"wait_days": 2, "section": "non_opener"})
    first = _launcher(emails, tmp_path, sections=RESEND_SECTIONS, api=api,
                      approvals={"non_lifestyle": True, "non_opener": False})
    first.launch(date(2026, 9, 12))
    assert len(api.created_single_sends) == 1
    later = _launcher(emails, tmp_path, sections=RESEND_SECTIONS, api=api)
    result = later.launch(date(2026, 9, 12))
    assert len(api.created_single_sends) == 2
    child = next(r for r in result["sends"] if r.get("resend_of") == 0)
    assert child["skipped"] is False
    assert api.created_single_sends[1]["email_config"]["subject"] == "A different way in"
