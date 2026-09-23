import json
from datetime import date, datetime, time, timezone

import pytest

from campaign_launch import (
    CampaignLauncher,
    CampaignLaunchError,
    GateContext,
    resolve_class_tokens,
)
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


def _anchored_launcher(tmp_path, email, api=None, gate_context=None):
    journey = _campaign(recurrence="monthly", emails=[email])
    return CampaignLauncher(
        api=api or FakeAPI(), registry=FakeRegistry(), journey=journey,
        state_path=tmp_path / "campaign.json", now_fn=lambda: NOW,
        gate_context=gate_context,
    )


def test_a_first_weekday_anchor_resolves_to_the_first_weekday(tmp_path):
    api = FakeAPI()
    launcher = _anchored_launcher(tmp_path, api=api, email={
        "subject": "Monthly", "preheader": "", "body": "News.",
        "interval_days": 0, "anchor": "first_weekday", "offset_days": 0,
    })

    launcher.launch(date(2026, 9, 1))

    # Sept 1 2026 is a Tuesday, the first weekday of the month; 09:49 MDT = 15:49 UTC
    assert api.scheduled[0][1] == "2026-09-01T15:49:00Z"


def test_a_class_date_anchor_offsets_from_the_class_date(tmp_path):
    api = FakeAPI()
    launcher = _anchored_launcher(
        tmp_path, api=api,
        gate_context=GateContext(class_date=date(2026, 9, 12)),
        email={"subject": "Reminder", "preheader": "", "body": "Tomorrow.",
               "interval_days": 0, "anchor": "class_date", "offset_days": -1})

    launcher.launch(date(2026, 9, 1))

    # class Sep 12, offset -1 -> Sep 11; 09:49 MDT = 15:49 UTC
    assert api.scheduled[0][1] == "2026-09-11T15:49:00Z"


def test_a_class_anchored_email_needs_a_class_date(tmp_path):
    api = FakeAPI()
    launcher = _anchored_launcher(tmp_path, api=api, email={
        "subject": "Reminder", "preheader": "", "body": "x",
        "interval_days": 0, "anchor": "class_date", "offset_days": -1,
    })  # no gate_context, so no class date

    with pytest.raises(CampaignLaunchError, match="class date"):
        launcher.launch(date(2026, 9, 1))
    assert not api.scheduled


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


# --- a past email is skipped, never fatal (2026-09-23) ---------------------
#
# Until this change _validate_start raised when ANY email's send time had
# passed. The launch is idempotent and the tick re-runs it every day, so one
# sent email made every later run of that month fail, and took the unsent ones
# down with it: a missed run on email one's day meant email two was never
# scheduled either, though its own send was still days away.


def test_a_past_email_is_skipped_and_nothing_is_created_for_it(tmp_path):
    # now is 2026-08-18, so an August campaign has every send behind us.
    api = FakeAPI()
    launcher = _launcher(tmp_path, api=api, journey=_campaign(
        campaign_month="2026_08",
        label="Campaign: Transitions: 2026_08",
    ))

    report = launcher.launch(date(2026, 8, 1))

    assert all(row["skipped"] for row in report["sends"])
    assert all(row["past_due"] for row in report["sends"])
    # Nothing sends late: no Single Send is created for a moment already gone.
    assert api.created_single_sends == []


def test_a_past_email_does_not_stop_the_one_still_ahead(tmp_path):
    """The whole point. Email one has gone, email two is days away, and the
    daily tick must still schedule email two."""
    api = FakeAPI()
    journey = _campaign(emails=[
        {"subject": "Already gone", "preheader": "", "body": "A",
         "interval_days": 0},
        {"subject": "Still ahead", "preheader": "", "body": "B",
         "interval_days": 14},
    ])
    launcher = CampaignLauncher(
        api=api, registry=FakeRegistry(), journey=journey,
        state_path=tmp_path / "campaign.json",
        # Between the two: 2026-09-12 has gone, 2026-09-26 has not.
        now_fn=lambda: datetime(2026, 9, 15, 12, 0, tzinfo=timezone.utc),
    )

    report = launcher.launch(date(2026, 9, 12))

    assert report["sends"][0]["past_due"] is True
    assert report["sends"][1]["skipped"] is False
    assert [c["name"] for c in api.created_single_sends] == [
        "2026_09: Transitions: Email 2"]


def test_re_running_after_the_first_email_sent_does_not_raise(tmp_path):
    """The daily tick case: launch on time, then run again the next day. The
    second run must be a quiet no-op, not a failure that pages JP."""
    api = FakeAPI()
    journey = _campaign(emails=[
        {"subject": "One", "preheader": "", "body": "A", "interval_days": 0},
        {"subject": "Two", "preheader": "", "body": "B", "interval_days": 14},
    ])
    on_time = CampaignLauncher(
        api=api, registry=FakeRegistry(), journey=journey,
        state_path=tmp_path / "campaign.json", now_fn=lambda: NOW)
    on_time.launch(date(2026, 9, 12))
    created = len(api.created_single_sends)
    assert created == 2

    later = CampaignLauncher(
        api=api, registry=FakeRegistry(), journey=journey,
        state_path=tmp_path / "campaign.json",
        now_fn=lambda: datetime(2026, 9, 15, 12, 0, tzinfo=timezone.utc))
    report = later.launch(date(2026, 9, 12))

    assert all(row["skipped"] for row in report["sends"])
    assert len(api.created_single_sends) == created, "a re-run created a send"


def test_a_start_date_outside_the_campaign_month_is_still_refused(tmp_path):
    """The month check survives; only the past-send refusal went."""
    launcher = _launcher(tmp_path, journey=_campaign(
        campaign_month="2026_08",
        label="Campaign: Transitions: 2026_08",
    ))
    with pytest.raises(CampaignLaunchError, match="not in the campaign month"):
        launcher.launch(date(2026, 9, 1))


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


# --- class tokens in written-here copy (2026-09-22) ------------------------
#
# The Integration reminder is typed once into the campaign and names the class
# Tiff authored that month. These pin the two halves of that: the words a member
# reads, and the hold that keeps a literal {CLASS_TITLE} out of an inbox.


def _integration_context():
    return GateContext(
        class_exists=True,
        class_date=date(2026, 9, 26),
        class_title="Trust the Transition",
        class_time=time(9, 0),
        class_url="https://studio.tiffanywoodyoga.com/event/details/1036259",
        now=date(2026, 9, 24),
    )


def test_class_tokens_resolve_to_the_words_a_member_reads():
    body = (
        "Tomorrow, {CLASS_DATE}, at {CLASS_TIME} Mountain time, is this "
        "month's Integration class: {CLASS_TITLE}. {CLASS_URL}"
    )
    assert resolve_class_tokens(body, _integration_context()) == (
        "Tomorrow, Saturday, September 26, at 9:00 AM Mountain time, is this "
        "month's Integration class: Trust the Transition. "
        "https://studio.tiffanywoodyoga.com/event/details/1036259"
    )


def test_copy_with_no_class_token_is_returned_untouched():
    """Every campaign written before this field existed goes through this path,
    so text with no token must come back byte for byte, context or not."""
    assert resolve_class_tokens("Come to class.", None) == "Come to class."
    assert resolve_class_tokens("Come to class.", GateContext()) == "Come to class."


def test_a_token_with_no_fact_behind_it_holds_the_whole_email():
    """A member reading a literal {CLASS_TITLE} is the failure this guards. One
    missing fact holds the email rather than sending the rest of it."""
    assert resolve_class_tokens("Join {CLASS_TITLE}", GateContext()) is None
    assert resolve_class_tokens("At {CLASS_TIME}", GateContext()) is None
    assert resolve_class_tokens("On {CLASS_DATE}", GateContext()) is None
    assert resolve_class_tokens("Go to {CLASS_URL}", GateContext()) is None
    # A plan published to HeyMarvelous late has a title but no event page yet.
    partial = GateContext(class_title="Trust the Transition")
    assert resolve_class_tokens("{CLASS_TITLE} at {CLASS_URL}", partial) is None


def test_a_class_token_with_no_context_at_all_holds():
    assert resolve_class_tokens("Join {CLASS_TITLE}", None) is None


def test_launch_sends_an_email_whose_class_tokens_resolve(tmp_path):
    api = FakeAPI()
    journey = _campaign(emails=[{
        "subject": "Tomorrow morning: {CLASS_TITLE}",
        "preheader": "Integration class, Saturday at {CLASS_TIME} Mountain",
        "body": "Tomorrow, {CLASS_DATE}, at {CLASS_TIME} Mountain time.",
        "interval_days": 0,
    }])
    launcher = CampaignLauncher(
        api=api,
        registry=FakeRegistry(),
        journey=journey,
        state_path=tmp_path / "campaign.json",
        gate_context=_integration_context(),
        now_fn=lambda: NOW,
    )

    report = launcher.launch(date(2026, 9, 25))

    assert report["sends"][0]["skipped"] is False
    sent = api.created_single_sends[0]
    assert sent["name"] == "2026_09: Transitions: Email 1"
    assert "Tomorrow morning: Trust the Transition" in json.dumps(sent)
    assert "{CLASS_TITLE}" not in json.dumps(sent)


def test_launch_holds_an_email_whose_class_has_no_plan_yet(tmp_path):
    """No authored Integration plan means no title, no time and no page, so the
    email holds and nothing is created at the provider."""
    api = FakeAPI()
    journey = _campaign(emails=[{
        "subject": "Tomorrow morning: {CLASS_TITLE}",
        "preheader": "",
        "body": "See you then.",
        "interval_days": 0,
    }])
    launcher = CampaignLauncher(
        api=api,
        registry=FakeRegistry(),
        journey=journey,
        state_path=tmp_path / "campaign.json",
        gate_context=GateContext(now=date(2026, 9, 24)),
        now_fn=lambda: NOW,
    )

    report = launcher.launch(date(2026, 9, 25))

    assert report["sends"][0]["skipped"] is True
    assert report["sends"][0]["content_pending"] is True
    assert api.created_single_sends == []
