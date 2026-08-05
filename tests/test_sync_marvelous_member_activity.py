from datetime import datetime, timezone
import sqlite3

import pytest

import sync_marvelous_member_activity as activity


ACTIVE_OLD = {
    "Email": "old@example.com",
    "First Name": "Old",
    "Last Name": "Member",
    "Product Name": "The Yoga Lifestyle Membership",
    "Created": "2026-01-01T00:00:00Z",
    "Status": "Active",
}
ACTIVE_NEW = {
    "Email": "new@example.com",
    "First Name": "New",
    "Last Name": "Member",
    "Product Name": "The Yoga Lifestyle Membership",
    "Created": "2026-08-05T01:00:00Z",
    "Status": "Active",
}
CANCELED = {
    "email": "gone@example.com",
    "first_name": "Gone",
    "last_name": "Member",
    "product_name": "The Yoga Lifestyle Membership",
    "canceled_at": "2026-08-05T02:00:00Z",
    "subscription_active_until": "2026-09-05T02:00:00Z",
}


def purchase(
    purchase_id,
    created,
    *,
    subscription="sub_one",
    active=True,
    canceled=False,
    amount="80.00",
    product="The Yoga Lifestyle Membership",
):
    return activity.MembershipPurchase(
        purchase_id=str(purchase_id),
        email="renewed@example.com",
        name="Renewed Member",
        product_name=product,
        recurring_type="monthly",
        amount_paid=amount,
        created=created,
        subscription_id=subscription,
        is_active=active,
        is_canceled=canceled,
    )


def test_plan_detects_join_renewal_and_cancellation_once():
    first = purchase(10, "2026-07-05T01:00:00Z")
    renewal = purchase(11, "2026-08-05T01:00:00Z")
    state = activity.ActivityState(
        active_memberships=activity.active_memberships([ACTIVE_OLD]),
        processed_purchase_ids=frozenset({"10"}),
        processed_cancellation_keys=frozenset(),
    )

    events, next_state = activity.plan_activity(
        active_rows=[ACTIVE_OLD, ACTIVE_NEW],
        canceled_rows=[CANCELED],
        purchases=[first, renewal],
        state=state,
    )

    assert [event.kind for event in events] == ["Joined", "Renewed", "Canceled"]
    assert {event.key for event in events} == {
        "joined:new@example.com:Yoga Lifestyle:2026-08-05T01:00:00Z",
        "renewed:11",
        "canceled:gone@example.com:Yoga Lifestyle:2026-08-05T02:00:00Z",
    }

    repeated, repeated_state = activity.plan_activity(
        active_rows=[ACTIVE_OLD, ACTIVE_NEW],
        canceled_rows=[CANCELED],
        purchases=[first, renewal],
        state=next_state,
    )
    assert repeated == []
    assert repeated_state == next_state


def test_renewal_requires_paid_active_repeat_subscription_charge():
    first = purchase(1, "2026-07-01T00:00:00Z")
    rows = [
        first,
        purchase(2, "2026-08-01T00:00:00Z", amount="0"),
        purchase(3, "2026-08-02T00:00:00Z", active=False),
        purchase(4, "2026-08-03T00:00:00Z", canceled=True),
        purchase(
            5,
            "2026-08-04T00:00:00Z",
            product="The Yoga Lifestyle: On-demand Library",
        ),
    ]
    state = activity.ActivityState(
        active_memberships={},
        processed_purchase_ids=frozenset({"1"}),
        processed_cancellation_keys=frozenset(),
    )

    events, _ = activity.plan_activity(
        active_rows=[],
        canceled_rows=[],
        purchases=rows,
        state=state,
    )

    assert events == []


def test_first_charge_for_subscription_is_not_renewal():
    events, _ = activity.plan_activity(
        active_rows=[],
        canceled_rows=[],
        purchases=[purchase(1, "2026-08-05T00:00:00Z")],
        state=activity.ActivityState.empty(),
    )
    assert events == []


def test_state_advances_only_after_confirmed_slack_delivery(tmp_path, monkeypatch):
    state_path = tmp_path / "state.json"
    state = activity.ActivityState(
        active_memberships=activity.active_memberships([ACTIVE_OLD]),
        processed_purchase_ids=frozenset(),
        processed_cancellation_keys=frozenset(),
    )
    activity.save_state(state_path, state)
    monkeypatch.setattr(activity, "post_activity", lambda *args, **kwargs: False)

    with pytest.raises(RuntimeError, match="Slack delivery"):
        activity.apply_activity(
            active_rows=[ACTIVE_OLD, ACTIVE_NEW],
            canceled_rows=[],
            purchases=[],
            state_path=state_path,
            dry_run=False,
            channel="C123",
        )

    assert activity.load_state(state_path) == state


def test_format_activity_uses_locked_labels_and_no_dash_punctuation():
    event = activity.ActivityEvent(
        kind="Renewed",
        key="renewed:11",
        email="renewed@example.com",
        name="Renewed Member",
        program="Yoga Lifestyle",
        occurred_at="2026-08-05T01:00:00Z",
        amount="80.00",
    )

    text = activity.format_activity([event], customer_link=lambda email, name: name)

    assert text == "*Renewed*: Renewed Member (Yoga Lifestyle, $80, Aug 5)"
    assert "-" not in text
    assert "–" not in text
    assert "—" not in text


def test_database_freshness_is_required(tmp_path):
    database = tmp_path / "marvy.db"
    connection = sqlite3.connect(database)
    connection.execute(
        "CREATE TABLE sync_log (finished_at TEXT, tier TEXT)"
    )
    connection.execute(
        "CREATE TABLE purchases ("
        "id TEXT, customer_email TEXT, customer_name TEXT, product_name TEXT, "
        "recurring_type TEXT, amount_paid TEXT, created TEXT, "
        "is_stripe_subscription TEXT, is_active INTEGER, is_canceled INTEGER)"
    )
    connection.execute(
        "INSERT INTO sync_log VALUES (?, ?)",
        ("2026-08-04T07:13:00+00:00", "all"),
    )
    connection.commit()
    connection.close()

    with pytest.raises(RuntimeError, match="stale"):
        activity.load_membership_purchases(
            database,
            now=datetime(2026, 8, 5, 7, 20, tzinfo=timezone.utc),
            max_age_hours=2,
        )


def test_bootstrap_cutoff_replays_only_later_renewals():
    first = purchase(1, "2026-07-01T00:00:00Z")
    before = purchase(2, "2026-07-25T00:00:00Z")
    after = purchase(3, "2026-07-27T00:00:00Z")
    state = activity.bootstrap_state(
        baseline_active_rows=[ACTIVE_OLD],
        canceled_rows=[],
        purchases=[first, before, after],
        purchase_cutoff=datetime(2026, 7, 26, tzinfo=timezone.utc),
    )

    events, _ = activity.plan_activity(
        active_rows=[ACTIVE_OLD],
        canceled_rows=[],
        purchases=[first, before, after],
        state=state,
    )

    assert [event.key for event in events] == ["renewed:3"]
