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
