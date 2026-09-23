"""The monthly tick's selection gate and per-period launch, without the provider."""
import campaign_ticker as ct


def _campaign(**over):
    j = {
        "type": "campaign",
        "journey_id": "yoga_habit",
        "name": "Yoga Habit",
        "campaign_month": "2026_08",
        "run_date": "2026-08-01",
        "recurrence": "monthly",
        "active": True,
        "segment_id": "seg-1",
        "segment_name": "Audience: Non Members",
        "emails": [
            {"subject": "Invite", "body": "x", "interval_days": 0,
             "approved_at": "2026-08-19T00:00:00+00:00"},
        ],
    }
    j.update(over)
    return j


def test_a_campaign_on_monthly_and_fully_approved_is_due():
    assert ct.is_due(_campaign()) is True


def test_an_off_campaign_is_not_due():
    assert ct.is_due(_campaign(active=False)) is False


def test_a_one_time_campaign_is_not_due():
    assert ct.is_due(_campaign(recurrence="none")) is False


def test_a_partly_approved_campaign_is_not_due():
    j = _campaign(emails=[
        {"subject": "A", "body": "x", "interval_days": 0,
         "approved_at": "2026-08-19T00:00:00+00:00"},
        {"subject": "B", "body": "y", "interval_days": 7},
    ])
    assert ct.is_due(j) is False


def test_a_product_journey_is_never_due():
    assert ct.is_due({"marvelous_product_id": 52025, "active": True,
                      "emails": [{"approved_at": "t"}]}) is False


def test_period_journey_pins_the_month_without_touching_the_original():
    original = _campaign()
    pinned = ct.period_journey(original, 2026, 10)
    assert pinned["campaign_month"] == "2026_10"
    assert pinned["run_date"] == "2026-10-01"
    # the stored definition is unchanged
    assert original["campaign_month"] == "2026_08"


def test_only_due_campaigns_are_launched_and_get_the_pinned_period():
    seen = []

    def launch_one(pinned, year, month):
        seen.append((pinned["journey_id"], pinned["campaign_month"]))
        return {"scheduled": 1}

    journeys = [
        _campaign(journey_id="yoga_habit"),                 # due
        _campaign(journey_id="off_one", active=False),      # Off
        _campaign(journey_id="onetime", recurrence="none"), # one-time
    ]
    results = ct.launch_due_campaigns(journeys, 2026, 10, launch_one=launch_one)

    assert seen == [("yoga_habit", "2026_10")]
    assert results == [{"journey_id": "yoga_habit", "result": {"scheduled": 1}}]


def test_one_campaign_failing_does_not_stop_the_others():
    def launch_one(pinned, year, month):
        if pinned["journey_id"] == "boom":
            raise RuntimeError("provider down")
        return {"scheduled": 1}

    journeys = [
        _campaign(journey_id="boom"),
        _campaign(journey_id="fine"),
    ]
    results = ct.launch_due_campaigns(journeys, 2026, 10, launch_one=launch_one)

    ids = {r["journey_id"]: r for r in results}
    assert "provider down" in ids["boom"]["error"]
    assert ids["fine"]["result"] == {"scheduled": 1}


def test_nothing_due_launches_nothing():
    calls = []
    journeys = [_campaign(active=False), _campaign(recurrence="none")]
    ct.launch_due_campaigns(journeys, 2026, 10,
                            launch_one=lambda *a: calls.append(a))
    assert calls == []


# --- per-month approval (JP promised Tiff this 2026-09-23) -----------------
#
# A monthly-approval campaign's stamp is good only for the period it names.
# Tiff edits the copy for this month's class and ticks again; last month's
# approval can never send this month's mail.


def _monthly_approval(period, **over):
    return _campaign(
        approval="monthly",
        emails=[
            {"subject": "One", "body": "x", "interval_days": 0,
             "approved_at": "2026-09-23T17:39:24+00:00", "approved_for": period},
            {"subject": "Two", "body": "y", "interval_days": 0,
             "approved_at": "2026-09-23T17:40:23+00:00", "approved_for": period},
        ],
        **over,
    )


def test_approval_for_this_period_is_due():
    assert ct.is_due(_monthly_approval("2026_09"), "2026_09") is True


def test_last_months_approval_does_not_arm_this_month():
    """The whole point: she approved September's copy, October must wait."""
    assert ct.is_due(_monthly_approval("2026_09"), "2026_10") is False


def test_one_stale_approval_holds_the_whole_campaign():
    journey = _monthly_approval("2026_10")
    journey["emails"][1]["approved_for"] = "2026_09"
    assert ct.is_due(journey, "2026_10") is False


def test_a_monthly_approval_campaign_with_no_period_is_not_due():
    """Arming a live send on a missing argument is the wrong way to be wrong."""
    assert ct.is_due(_monthly_approval("2026_09")) is False


def test_an_approval_stamp_with_no_period_is_not_due():
    journey = _monthly_approval("2026_09")
    for email in journey["emails"]:
        email.pop("approved_for")
    assert ct.is_due(journey, "2026_09") is False


def test_approve_once_is_the_default_and_ignores_the_period():
    """Every campaign written before 2026-09-23 keeps carrying its approval
    forward, with or without a period argument."""
    journey = _campaign()
    assert ct.is_due(journey) is True
    assert ct.is_due(journey, "2026_09") is True
    assert ct.is_due(_campaign(approval="once"), "2027_01") is True


def test_an_unknown_approval_scope_holds_rather_than_guessing():
    """Falling back to once would send last month's copy to a live audience."""
    assert ct.is_due(_campaign(approval="whenever"), "2026_09") is False


def test_an_unapproved_email_still_holds_a_monthly_campaign():
    journey = _monthly_approval("2026_09")
    journey["emails"][0].pop("approved_at")
    assert ct.is_due(journey, "2026_09") is False
