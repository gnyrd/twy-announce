"""Dynamic, exact audiences for a campaign email, resolved at launch.

These reproduce the live workflow's two query-built segments without approximation:
- interested_nonmember: the month's Interested list minus members (the follow-ups).
- opener_not_registered: contacts who opened a named parent send and are not on
  the Registered list (the gentle reminder), built off the parent's own send id.

The launcher resolves both through the same SendGridCampaigns.ensure_segment the
workflow uses, keyed by the email's section, so a campaign and the old workflow
name the same segment. An opener audience whose parent did not send this period
holds that one email, like a gate, and the rest still send.
"""
import os
import sys
from datetime import date, datetime, timezone

sys.path.insert(0, os.path.dirname(__file__))

from campaign_launch import CampaignLauncher, GateContext
from sendgrid_mailings import MailingPurpose
from test_campaign_launch import FakeAPI, FakeRegistry, SEGMENT_ID, _campaign


NOW = datetime(2026, 8, 18, 12, 0, tzinfo=timezone.utc)


class _FakeCampReg:
    def list_id(self, name):
        return "MEM"


class FakeCampaigns:
    """The SendGridCampaigns handle the launcher uses for dynamic audiences."""

    def __init__(self):
        self.registry = _FakeCampReg()
        self.lists = {}
        self.segments = []
        self._seq = 0

    def ensure_list(self, name):
        return self.lists.setdefault(name, f"L{len(self.lists) + 1}")

    def ensure_segment(self, *, purpose, year, month, query_dsl, parent_list_ids=None):
        self._seq += 1
        rec = {
            "id": f"dyn{self._seq}",
            "purpose": purpose,
            "query_dsl": query_dsl,
            "parent_list_ids": parent_list_ids,
        }
        self.segments.append(rec)
        return {"id": rec["id"], "name": f"{year}_{month:02d}: {purpose.value}"}


def _launcher(journey, tmp_path, api, campaigns, sections=None, gate=None):
    return CampaignLauncher(
        api=api,
        registry=FakeRegistry(),
        journey=journey,
        state_path=tmp_path / "campaign.json",
        now_fn=lambda: NOW,
        campaigns=campaigns,
        sections=sections,
        gate_context=gate or GateContext(class_date=date(2026, 9, 12)),
    )


def test_interested_nonmember_audience_resolves_at_launch(tmp_path):
    api, camp = FakeAPI(), FakeCampaigns()
    journey = _campaign(emails=[
        {"subject": "Follow up", "preheader": "", "body": "x",
         "interval_days": 0, "section": "ph1",
         "audience": {"dynamic": "interested_nonmember"}},
    ])
    sections = {"ph1": {"subject": "FU", "preheader": "", "body": "fu body"}}
    _launcher(journey, tmp_path, api, camp, sections=sections).launch(date(2026, 9, 12))
    assert len(camp.segments) == 1
    seg = camp.segments[0]
    assert seg["purpose"] == MailingPurpose.FOLLOW_UP_1
    assert seg["parent_list_ids"]  # interested list is the parent scope
    assert api.created_single_sends[0]["send_to"]["segment_ids"] == [seg["id"]]


def test_opener_not_registered_resolves_off_the_parent_send(tmp_path):
    api, camp = FakeAPI(), FakeCampaigns()
    journey = _campaign(emails=[
        {"subject": "Invite", "preheader": "", "body": "x", "interval_days": 0},
        {"subject": "Gentle", "preheader": "", "body": "y",
         "send_date": "2026-09-20", "section": "gentle_nudge",
         "audience": {"dynamic": "opener_not_registered", "of": 0}},
    ])
    sections = {"gentle_nudge": {"subject": "G", "preheader": "", "body": "gb"}}
    _launcher(journey, tmp_path, api, camp, sections=sections).launch(date(2026, 9, 12))
    parent_id = api.created_single_sends[0]["id"]
    seg = camp.segments[0]
    assert seg["purpose"] == MailingPurpose.GENTLE_REMINDER
    assert parent_id in seg["query_dsl"]  # the opener query embeds the parent send id
    assert api.created_single_sends[1]["send_to"]["segment_ids"] == [seg["id"]]


def test_opener_audience_holds_when_the_parent_did_not_send(tmp_path):
    api, camp = FakeAPI(), FakeCampaigns()
    journey = _campaign(emails=[
        {"subject": "Invite", "preheader": "", "body": "x",
         "interval_days": 0, "gate": "class_exists"},
        {"subject": "Gentle", "preheader": "", "body": "y",
         "send_date": "2026-09-20", "section": "gentle_nudge",
         "audience": {"dynamic": "opener_not_registered", "of": 0}},
    ])
    # The invitation is gated out (no class), so nothing sent; the gentle reminder
    # has no parent send to build openers from and must hold, not error.
    result = _launcher(
        journey, tmp_path, api, camp,
        sections={"gentle_nudge": {"subject": "G", "preheader": "", "body": "gb"}},
        gate=GateContext(class_exists=False, class_date=date(2026, 9, 12)),
    ).launch(date(2026, 9, 12))
    assert len(api.created_single_sends) == 0
    pending = [r for r in result["sends"] if r.get("audience_pending")]
    assert len(pending) == 1


def test_registered_audience_builds_a_segment_over_the_registered_list(tmp_path):
    api, camp = FakeAPI(), FakeCampaigns()
    journey = _campaign(emails=[
        {"subject": "Reminder", "preheader": "", "body": "x",
         "interval_days": 0, "section": "reminder",
         "audience": {"dynamic": "registered"}},
    ])
    sections = {"reminder": {"subject": "R", "preheader": "", "body": "rb"}}
    _launcher(journey, tmp_path, api, camp, sections=sections).launch(date(2026, 9, 12))
    assert len(camp.segments) == 1
    seg = camp.segments[0]
    assert seg["purpose"] == MailingPurpose.REGISTERED_REMINDER
    assert seg["parent_list_ids"]  # scoped to the registered list
    assert api.created_single_sends[0]["send_to"]["segment_ids"] == [seg["id"]]


def test_static_audience_needs_no_campaigns_handle(tmp_path):
    api = FakeAPI()
    journey = _campaign()  # two plain emails, campaign-default segment
    result = CampaignLauncher(
        api=api, registry=FakeRegistry(), journey=journey,
        state_path=tmp_path / "s.json", now_fn=lambda: NOW,
    ).launch(date(2026, 9, 12))
    assert len(api.created_single_sends) == 2
    assert api.created_single_sends[0]["send_to"]["segment_ids"] == [SEGMENT_ID]


def test_dynamic_audience_without_a_campaigns_handle_is_refused(tmp_path):
    import pytest
    from campaign_launch import CampaignLaunchError
    api = FakeAPI()
    journey = _campaign(emails=[
        {"subject": "FU", "preheader": "", "body": "x", "interval_days": 0,
         "section": "ph1", "audience": {"dynamic": "interested_nonmember"}},
    ])
    launcher = CampaignLauncher(
        api=api, registry=FakeRegistry(), journey=journey,
        state_path=tmp_path / "s.json", now_fn=lambda: NOW,
        gate_context=GateContext(class_date=date(2026, 9, 12)),
        sections={"ph1": {"subject": "FU", "preheader": "", "body": "fu body"}},
    )
    with pytest.raises(CampaignLaunchError):
        launcher.launch(date(2026, 9, 12))
