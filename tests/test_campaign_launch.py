import json
from datetime import date, datetime, timezone

import pytest

from campaign_launch import CampaignLauncher, CampaignLaunchError, GateContext
from sendgrid_mailings import INTERNAL_SEND_COPY


NOW = datetime(2026, 8, 18, 12, 0, tzinfo=timezone.utc)
SEGMENT_ID = "seg-abc-123"


class FakeRegistry:
    sender_id = 7
    suppression_group_id = 42

    def __init__(self):
        # SendGrid list ids are opaque alphanumerics with hyphens, never
        # underscores; mint stable ones per name so a caller can look them up.
        self._ids = {}

    def list_id(self, name):
        if name not in self._ids:
            self._ids[name] = f"list{len(self._ids) + 1}"
        return self._ids[name]


class FakeAPI:
    def __init__(self):
        self.single_sends = {}
        # The campaign's chosen segment already exists in SendGrid; the launcher
        # only confirms it, never creates it.
        self.segments_by_id = {
            SEGMENT_ID: {
                "id": SEGMENT_ID,
                "name": "2026_09: Yoga Habit: General Invitation",
            }
        }
        self.created_single_sends = []
        self.scheduled = []
        self.unscheduled = []
        self._seg_seq = 0
        self._ss_seq = 0

    # segments
    def segments(self):
        return [{"id": s["id"], "name": s["name"]} for s in self.segments_by_id.values()]

    def segment(self, segment_id):
        return dict(self.segments_by_id[str(segment_id)])

    def create_segment(self, *, name, query_dsl, parent_list_ids=None):
        self._seg_seq += 1
        seg = {
            "id": f"seg{self._seg_seq}",
            "name": name,
            "query_dsl": query_dsl,
            "parent_list_ids": list(parent_list_ids or []),
        }
        self.segments_by_id[seg["id"]] = seg
        return dict(seg)

    def update_segment(self, segment_id, *, name, query_dsl, parent_list_ids=None):
        seg = self.segments_by_id[str(segment_id)]
        seg.update({"name": name, "query_dsl": query_dsl,
                    "parent_list_ids": list(parent_list_ids or [])})
        return dict(seg)

    # single sends
    def create_single_send(self, payload):
        self._ss_seq += 1
        identifier = f"ss{self._ss_seq}"
        self.single_sends[identifier] = {
            "id": identifier,
            "name": payload["name"],
            "send_to": payload["send_to"],
            "email_config": payload["email_config"],
            "status": "draft",
            "send_at": None,
        }
        self.created_single_sends.append(dict(self.single_sends[identifier]))
        return {"id": identifier}

    def get_single_send(self, identifier):
        return dict(self.single_sends[str(identifier)])

    def schedule_single_send(self, identifier, send_at):
        ss = self.single_sends[str(identifier)]
        ss["status"] = "scheduled"
        ss["send_at"] = send_at
        self.scheduled.append((identifier, send_at))
        return dict(ss)

    def unschedule_single_send(self, identifier):
        ss = self.single_sends[str(identifier)]
        ss["status"] = "draft"
        ss["send_at"] = None
        self.unscheduled.append(identifier)
        return dict(ss)


def _campaign(**overrides):
    payload = {
        "version": 1,
        "type": "campaign",
        "journey_id": "transitions",
        "label": "Campaign: Transitions: 2026_09",
        "name": "Transitions",
        "run_date": "2026-09-12",
        "campaign_month": "2026_09",
        "segment_id": SEGMENT_ID,
        "segment_name": "2026_09: Yoga Habit: General Invitation",
        "active": True,
        "emails": [
            {"subject": "A gentle invitation", "preheader": "Come back",
             "body": "Come to class.", "interval_days": 0},
            {"subject": "One week in", "preheader": "",
             "body": "Keep going.", "interval_days": 7},
        ],
    }
    payload.update(overrides)
    return payload


def _launcher(tmp_path, journey=None, api=None, registry=None):
    return CampaignLauncher(
        api=api or FakeAPI(),
        registry=registry or FakeRegistry(),
        journey=journey or _campaign(),
        state_path=tmp_path / "campaign.json",
        now_fn=lambda: NOW,
    )


def test_launch_schedules_one_single_send_per_email(tmp_path):
    api = FakeAPI()
    launcher = _launcher(tmp_path, api=api)

    report = launcher.launch(date(2026, 9, 12))

    assert len(report["sends"]) == 2
    assert len(api.created_single_sends) == 2
    # September is MDT (UTC-6), so 09:49 MT is 15:49 UTC.
    assert api.scheduled[0][1] == "2026-09-12T15:49:00Z"
    assert api.scheduled[1][1] == "2026-09-19T15:49:00Z"
    names = [c["name"] for c in api.created_single_sends]
    assert names == ["2026_09: Transitions: Email 1", "2026_09: Transitions: Email 2"]
    assert all(s["skipped"] is False for s in report["sends"])


def test_launch_targets_the_chosen_segment_with_internal_copy(tmp_path):
    api = FakeAPI()
    reg = FakeRegistry()
    launcher = _launcher(tmp_path, api=api, registry=reg)

    launcher.launch(date(2026, 9, 12))

    # The campaign sends to the segment it was given, and creates none of its own.
    send_to = api.created_single_sends[0]["send_to"]
    assert send_to["segment_ids"] == [SEGMENT_ID]
    assert send_to["list_ids"] == [reg.list_id(INTERNAL_SEND_COPY)]
    assert send_to["all"] is False


OTHER_SEGMENT = "seg-registrants-9"


def _two_audience_campaign(**overrides):
    return _campaign(emails=[
        {"subject": "Invite", "preheader": "", "body": "Come to class.",
         "interval_days": 0,
         "audience": {"segment_id": SEGMENT_ID,
                      "segment_name": "2026_09: Yoga Habit: General Invitation"}},
        {"subject": "Reminder", "preheader": "", "body": "Class is tomorrow.",
         "interval_days": 7,
         "audience": {"segment_id": OTHER_SEGMENT,
                      "segment_name": "2026_09: Yoga Habit: Registered Reminder"}},
    ], **overrides)


def test_launch_sends_each_email_to_its_own_segment(tmp_path):
    api = FakeAPI()
    api.segments_by_id[OTHER_SEGMENT] = {
        "id": OTHER_SEGMENT,
        "name": "2026_09: Yoga Habit: Registered Reminder",
    }
    launcher = _launcher(tmp_path, api=api, journey=_two_audience_campaign())

    launcher.launch(date(2026, 9, 12))

    assert api.created_single_sends[0]["send_to"]["segment_ids"] == [SEGMENT_ID]
    assert api.created_single_sends[1]["send_to"]["segment_ids"] == [OTHER_SEGMENT]


def test_email_without_audience_falls_back_to_campaign_segment(tmp_path):
    api = FakeAPI()
    api.segments_by_id[OTHER_SEGMENT] = {
        "id": OTHER_SEGMENT, "name": "Registered",
    }
    journey = _campaign(emails=[
        {"subject": "Invite", "preheader": "", "body": "Come.", "interval_days": 0},
        {"subject": "Reminder", "preheader": "", "body": "Go.", "interval_days": 7,
         "audience": {"segment_id": OTHER_SEGMENT, "segment_name": "Registered"}},
    ])
    launcher = _launcher(tmp_path, api=api, journey=journey)

    launcher.launch(date(2026, 9, 12))

    assert api.created_single_sends[0]["send_to"]["segment_ids"] == [SEGMENT_ID]
    assert api.created_single_sends[1]["send_to"]["segment_ids"] == [OTHER_SEGMENT]


def test_launch_aborts_before_sending_if_an_email_segment_is_missing(tmp_path):
    api = FakeAPI()  # OTHER_SEGMENT deliberately not registered
    launcher = _launcher(tmp_path, api=api, journey=_two_audience_campaign())

    with pytest.raises(CampaignLaunchError, match="could not be read|was not found"):
        launcher.launch(date(2026, 9, 12))

    assert not api.scheduled
    assert not api.created_single_sends


def test_plan_shows_each_email_target_segment(tmp_path):
    api = FakeAPI()
    api.segments_by_id[OTHER_SEGMENT] = {"id": OTHER_SEGMENT, "name": "Registered"}
    launcher = _launcher(tmp_path, api=api, journey=_two_audience_campaign())

    plan = launcher.plan(date(2026, 9, 12))

    assert plan["emails"][0]["segment_id"] == SEGMENT_ID
    assert plan["emails"][1]["segment_id"] == OTHER_SEGMENT


def _gated_campaign(gate, **overrides):
    return _campaign(emails=[
        {"subject": "Invite", "preheader": "", "body": "Come to class.",
         "interval_days": 0},
        {"subject": "Recording", "preheader": "", "body": "Here is the replay.",
         "interval_days": 1, "gate": gate},
    ], **overrides)


def _gated_launcher(tmp_path, gate, ctx, api=None):
    return CampaignLauncher(
        api=api or FakeAPI(), registry=FakeRegistry(),
        journey=_gated_campaign(gate), state_path=tmp_path / "campaign.json",
        now_fn=lambda: NOW, gate_context=ctx,
    )


def test_a_gated_message_is_skipped_when_its_gate_is_false(tmp_path):
    api = FakeAPI()
    launcher = _gated_launcher(tmp_path, "recording_ready",
                               GateContext(recording_ready=False), api=api)

    report = launcher.launch(date(2026, 9, 12))

    # only the ungated email 1 was created; the gated recording email was held
    assert len(api.created_single_sends) == 1
    assert api.created_single_sends[0]["name"].endswith("Email 1")
    gated = [s for s in report["sends"] if s.get("gated_out")]
    assert [g["gate"] for g in gated] == ["recording_ready"]


def test_a_gated_message_sends_when_its_gate_is_true(tmp_path):
    api = FakeAPI()
    launcher = _gated_launcher(tmp_path, "recording_ready",
                               GateContext(recording_ready=True), api=api)

    launcher.launch(date(2026, 9, 12))

    assert len(api.created_single_sends) == 2


def test_class_happened_gate_reads_the_class_date(tmp_path):
    api = FakeAPI()
    # class Sep 12, provisioning "now" Sep 10: the class has not happened, hold it
    launcher = _gated_launcher(
        tmp_path, "class_happened",
        GateContext(class_date=date(2026, 9, 12), now=date(2026, 9, 10)), api=api)

    report = launcher.launch(date(2026, 9, 12))

    assert len(api.created_single_sends) == 1
    assert any(s.get("gated_out") for s in report["sends"])


def test_a_gate_with_no_context_is_refused(tmp_path):
    api = FakeAPI()
    launcher = CampaignLauncher(
        api=api, registry=FakeRegistry(), journey=_gated_campaign("class_exists"),
        state_path=tmp_path / "campaign.json", now_fn=lambda: NOW,
    )  # no gate_context

    with pytest.raises(CampaignLaunchError, match="no gate context"):
        launcher.launch(date(2026, 9, 12))
    assert not api.scheduled
    assert not api.created_single_sends


def test_plan_shows_gated_messages(tmp_path):
    launcher = _gated_launcher(tmp_path, "recording_ready",
                               GateContext(recording_ready=False))

    plan = launcher.plan(date(2026, 9, 12))

    assert plan["emails"][1]["gate"] == "recording_ready"
    assert plan["emails"][1]["gated_out"] is True
    assert not plan["emails"][0].get("gated_out")


def _resend_campaign(wait_days=3, **resend_over):
    resend = {"wait_days": wait_days, **resend_over}
    return _campaign(emails=[
        {"subject": "Invite", "preheader": "Come back", "body": "Come to class.",
         "interval_days": 0, "resend": resend},
    ])


def _resend_launcher(tmp_path, journey, api=None, gate_context=None):
    return CampaignLauncher(
        api=api or FakeAPI(), registry=FakeRegistry(), journey=journey,
        state_path=tmp_path / "campaign.json", now_fn=lambda: NOW,
        gate_context=gate_context,
    )


def test_a_resend_child_is_created_after_its_parent(tmp_path):
    api = FakeAPI()
    launcher = _resend_launcher(tmp_path, _resend_campaign(wait_days=3), api=api)

    launcher.launch(date(2026, 9, 12))

    assert len(api.created_single_sends) == 2
    names = [c["name"] for c in api.created_single_sends]
    assert names == [
        "2026_09: Transitions: Email 1",
        "2026_09: Transitions: Email 1: Resend",
    ]
    # the child sends 3 days after the parent: Sep 12 -> Sep 15, 09:49 MDT = 15:49 UTC
    assert api.scheduled[-1][1] == "2026-09-15T15:49:00Z"


def test_resend_targets_a_non_opener_segment_of_its_parent(tmp_path):
    api = FakeAPI()
    launcher = _resend_launcher(tmp_path, _resend_campaign(wait_days=3), api=api)

    launcher.launch(date(2026, 9, 12))

    parent_id = api.created_single_sends[0]["id"]
    child_segments = api.created_single_sends[1]["send_to"]["segment_ids"]
    assert len(child_segments) == 1
    segment = api.segments_by_id[child_segments[0]]
    assert segment["name"] == "2026_09: Transitions: Email 1: Non Openers"
    assert parent_id in segment["query_dsl"]  # non_opener_query embeds the send id


def test_resend_child_inherits_parent_copy_by_default(tmp_path):
    api = FakeAPI()
    launcher = _resend_launcher(tmp_path, _resend_campaign(wait_days=3), api=api)

    launcher.launch(date(2026, 9, 12))

    parent, child = api.created_single_sends
    assert child["email_config"]["subject"] == parent["email_config"]["subject"]


def test_resend_child_can_override_its_subject(tmp_path):
    api = FakeAPI()
    launcher = _resend_launcher(
        tmp_path, _resend_campaign(wait_days=3, subject="One more nudge"), api=api)

    launcher.launch(date(2026, 9, 12))

    assert api.created_single_sends[1]["email_config"]["subject"] == "One more nudge"


def test_resend_is_skipped_when_the_parent_is_gated_out(tmp_path):
    api = FakeAPI()
    journey = _campaign(emails=[
        {"subject": "Invite", "preheader": "", "body": "Come.", "interval_days": 0,
         "gate": "class_exists", "resend": {"wait_days": 3}},
    ])
    launcher = _resend_launcher(
        tmp_path, journey, api=api, gate_context=GateContext(class_exists=False))

    launcher.launch(date(2026, 9, 12))

    assert len(api.created_single_sends) == 0  # parent held, so no child either


def test_relaunching_does_not_duplicate_the_resend(tmp_path):
    api = FakeAPI()
    launcher = _resend_launcher(tmp_path, _resend_campaign(wait_days=3), api=api)

    launcher.launch(date(2026, 9, 12))
    launcher.launch(date(2026, 9, 12))

    assert len(api.created_single_sends) == 2  # not four


def test_launch_fails_clearly_when_the_segment_is_gone(tmp_path):
    api = FakeAPI()
    launcher = _launcher(
        tmp_path, api=api,
        journey=_campaign(segment_id="seg-does-not-exist"),
    )
    with pytest.raises(CampaignLaunchError, match="could not be read|was not found"):
        launcher.launch(date(2026, 9, 12))
    assert not api.scheduled


def test_launch_is_idempotent(tmp_path):
    api = FakeAPI()
    launcher = _launcher(tmp_path, api=api)

    launcher.launch(date(2026, 9, 12))
    report = launcher.launch(date(2026, 9, 12))

    assert len(api.created_single_sends) == 2  # not four
    assert all(s["skipped"] is True for s in report["sends"])


def test_start_date_must_be_in_the_campaign_month(tmp_path):
    launcher = _launcher(tmp_path)
    with pytest.raises(CampaignLaunchError, match="not in the campaign month"):
        launcher.launch(date(2026, 10, 1))


def test_refuses_a_start_date_in_the_past(tmp_path):
    # now is 2026-08-18; a September campaign started 2026-09-12 is future,
    # but a campaign month in the past has every send in the past.
    launcher = _launcher(tmp_path, journey=_campaign(
        campaign_month="2026_08",
        label="Campaign: Transitions: 2026_08",
    ))
    with pytest.raises(CampaignLaunchError, match="in the past"):
        launcher.launch(date(2026, 8, 1))


def test_relaunch_on_a_new_date_is_refused_until_unscheduled(tmp_path):
    api = FakeAPI()
    launcher = _launcher(tmp_path, api=api)
    launcher.launch(date(2026, 9, 12))

    with pytest.raises(CampaignLaunchError, match="already launched"):
        launcher.launch(date(2026, 9, 19))

    pulled = launcher.unschedule()
    assert len(pulled) == 2
    assert launcher.launch(date(2026, 9, 19))["sends"]  # now allowed


def test_plan_touches_no_provider(tmp_path):
    api = FakeAPI()
    launcher = _launcher(tmp_path, api=api)

    plan = launcher.plan(date(2026, 9, 12))

    assert plan["count"] == 2
    assert plan["emails"][0]["send_at"] == "2026-09-12T15:49:00Z"
    assert plan["emails"][1]["name"] == "2026_09: Transitions: Email 2"
    assert not api.created_single_sends
    assert not api.scheduled
    assert not (tmp_path / "campaign.json").exists()


def test_a_pinned_send_date_overrides_the_wait(tmp_path):
    api = FakeAPI()
    journey = _campaign()
    journey["emails"][1]["send_date"] = "2026-09-20"  # instead of the 7 day wait
    launcher = _launcher(tmp_path, api=api, journey=journey)

    launcher.launch(date(2026, 9, 12))

    # Email 2 lands on its pinned date, not Sep 19. Sept is MDT, 09:49 = 15:49 UTC.
    assert api.scheduled[1][1] == "2026-09-20T15:49:00Z"


def test_a_later_wait_accrues_from_a_pinned_date(tmp_path):
    api = FakeAPI()
    journey = _campaign()
    journey["emails"] = journey["emails"] + [
        {"subject": "Third", "preheader": "", "body": "x", "interval_days": 3},
    ]
    journey["emails"][1]["send_date"] = "2026-09-20"
    launcher = _launcher(tmp_path, api=api, journey=journey)

    launcher.launch(date(2026, 9, 12))

    # Email 3 is 3 days after email 2's pinned Sep 20, so Sep 23.
    assert api.scheduled[2][1] == "2026-09-23T15:49:00Z"


def test_a_backward_pinned_date_is_refused(tmp_path):
    journey = _campaign()
    journey["emails"][1]["send_date"] = "2026-09-05"  # before the Sep 12 start
    launcher = _launcher(tmp_path, journey=journey)
    with pytest.raises(CampaignLaunchError, match="before email"):
        launcher.launch(date(2026, 9, 12))


def test_a_product_journey_is_not_launchable(tmp_path):
    with pytest.raises(CampaignLaunchError, match="not a campaign"):
        CampaignLauncher(
            api=FakeAPI(),
            registry=FakeRegistry(),
            journey={"journey_id": "welcome", "marvelous_product_id": 52025,
                     "emails": []},
            state_path=tmp_path / "campaign.json",
            now_fn=lambda: NOW,
        )


def test_prohibited_punctuation_in_a_body_stops_the_launch(tmp_path):
    api = FakeAPI()
    launcher = _launcher(tmp_path, api=api, journey=_campaign(
        emails=[{"subject": "Hi", "preheader": "",
                 "body": "Come to class — today.", "interval_days": 0}],
    ))
    with pytest.raises(CampaignLaunchError, match="prohibited punctuation"):
        launcher.launch(date(2026, 9, 12))
    assert not api.scheduled


def test_state_records_scheduled_sends(tmp_path):
    api = FakeAPI()
    launcher = _launcher(tmp_path, api=api)
    launcher.launch(date(2026, 9, 12))

    state = json.loads((tmp_path / "campaign.json").read_text())
    assert state["start_date"] == "2026-09-12"
    assert set(state["sends"]) == {"0", "1"}
    assert state["sends"]["0"]["status"] == "scheduled"
    assert state["segment"]["id"] == SEGMENT_ID
