"""journey_enrollment: the schedule a purchase starts, and the state it leaves."""
import importlib
from datetime import datetime, timedelta, timezone

import pytest


def enrollment_module():
    return importlib.import_module("journey_enrollment")


NOW = datetime(2026, 8, 11, 18, 0, tzinfo=timezone.utc)


def journey(**overrides):
    payload = {
        "version": 1,
        "journey_id": "yoga_lifestyle_welcome_2024_05",
        "label": "Journey: Yoga Lifestyle: 2024_05",
        "marvelous_product_id": 52025,
        "active": True,
        "emails": [
            {"subject": "Welcome", "body": "Hi", "interval_days": 0},
            {"subject": "Day two", "body": "More", "interval_days": 1},
            {"subject": "Day five", "body": "Again", "interval_days": 3},
        ],
    }
    payload.update(overrides)
    return payload


def plan(module, journey_payload=None, **overrides):
    values = {
        "email": "Buyer@Example.com",
        "customer_id": "441",
        "product_id": "52025",
        "purchase_id": "9001",
        "purchased_at": "2026-08-11T17:45:00Z",
        "now": NOW,
    }
    values.update(overrides)
    return module.plan_enrollment(journey_payload or journey(), **values)


def test_the_first_email_is_due_when_they_bought(tmp_path):
    module = enrollment_module()

    enrollment = plan(module)

    assert enrollment.journey_id == "yoga_lifestyle_welcome_2024_05"
    assert enrollment.email == "buyer@example.com"
    assert enrollment.next_index == 0
    assert enrollment.enrolled_at == "2026-08-11T17:45:00+00:00"
    assert enrollment.next_due_at == enrollment.enrolled_at


def test_an_inactive_journey_starts_for_nobody():
    module = enrollment_module()

    assert plan(module, journey(active=False)) is None


def test_a_journey_with_no_emails_starts_for_nobody():
    module = enrollment_module()

    assert plan(module, journey(emails=[])) is None


def test_activating_a_journey_does_not_reach_the_back_catalogue():
    module = enrollment_module()
    old = (NOW - timedelta(days=90)).isoformat()

    assert plan(module, purchased_at=old) is None


def test_a_purchase_inside_the_freshness_bound_still_enrolls():
    module = enrollment_module()
    recent = (NOW - timedelta(days=2)).isoformat()

    assert plan(module, purchased_at=recent) is not None


def test_the_freshness_bound_is_adjustable_per_call():
    module = enrollment_module()
    old = (NOW - timedelta(days=30)).isoformat()

    assert plan(module, purchased_at=old, max_purchase_age_days=60) is not None


def test_a_nonzero_first_interval_still_schedules_correctly():
    module = enrollment_module()
    late = journey(
        emails=[{"subject": "S", "body": "B", "interval_days": 2}]
    )

    enrollment = plan(module, late)

    assert enrollment.enrolled_at == "2026-08-11T17:45:00+00:00"
    assert enrollment.next_due_at == "2026-08-13T17:45:00+00:00"


@pytest.mark.parametrize(
    "raw",
    [
        "2026-08-11T17:45:00Z",
        "2026-08-11T17:45:00+00:00",
        "2026-08-11T17:45:00",
        "2026-08-11T11:45:00-06:00",
    ],
)
def test_provider_timestamps_read_as_utc(raw):
    module = enrollment_module()

    assert module.parse_timestamp(raw) == datetime(
        2026, 8, 11, 17, 45, tzinfo=timezone.utc
    )


def test_a_missing_timestamp_is_refused_not_guessed():
    module = enrollment_module()

    with pytest.raises(ValueError):
        module.parse_timestamp("")


def test_connect_creates_the_store_and_its_schema(tmp_path):
    module = enrollment_module()
    path = tmp_path / "nested" / "journey_enrollments.db"

    connection = module.connect(path)
    try:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
    finally:
        connection.close()

    assert path.exists()
    assert "enrollments" in tables


def test_recording_an_enrollment_stores_who_and_what_they_are_owed(tmp_path):
    module = enrollment_module()
    connection = module.connect(tmp_path / "e.db")
    try:
        recorded = module.record_enrollments(connection, [plan(module)])
        row = module.enrollment_for(
            connection, "yoga_lifestyle_welcome_2024_05", "buyer@example.com"
        )
    finally:
        connection.close()

    assert recorded == 1
    assert row["customer_id"] == "441"
    assert row["product_id"] == "52025"
    assert row["purchase_id"] == "9001"
    assert row["next_index"] == 0
    assert row["terminal_reason"] is None


def test_a_replayed_purchase_does_not_start_a_second_sequence(tmp_path):
    module = enrollment_module()
    connection = module.connect(tmp_path / "e.db")
    try:
        first = module.record_enrollments(connection, [plan(module)])
        second = module.record_enrollments(connection, [plan(module)])
        rows = connection.execute("SELECT COUNT(*) FROM enrollments").fetchone()
    finally:
        connection.close()

    assert (first, second) == (1, 0)
    assert rows[0] == 1


def test_a_replay_never_rewinds_someone_mid_sequence(tmp_path):
    module = enrollment_module()
    connection = module.connect(tmp_path / "e.db")
    try:
        module.record_enrollments(connection, [plan(module)])
        connection.execute(
            "UPDATE enrollments SET next_index = 2, next_due_at = ?",
            ("2026-08-15T17:45:00+00:00",),
        )
        connection.commit()

        module.record_enrollments(connection, [plan(module)])
        row = module.enrollment_for(
            connection, "yoga_lifestyle_welcome_2024_05", "buyer@example.com"
        )
    finally:
        connection.close()

    assert row["next_index"] == 2
    assert row["next_due_at"] == "2026-08-15T17:45:00+00:00"


def test_recording_nothing_touches_nothing(tmp_path):
    module = enrollment_module()
    connection = module.connect(tmp_path / "e.db")
    try:
        assert module.record_enrollments(connection, []) == 0
    finally:
        connection.close()


def test_the_runner_queue_skips_anyone_who_stopped_early(tmp_path):
    module = enrollment_module()
    connection = module.connect(tmp_path / "e.db")
    try:
        module.record_enrollments(
            connection,
            [
                plan(module, email="late@example.com", purchase_id="1"),
                plan(module, email="early@example.com", purchase_id="2"),
                plan(module, email="gone@example.com", purchase_id="3"),
            ],
        )
        connection.execute(
            "UPDATE enrollments SET next_due_at = ? WHERE email = ?",
            ("2026-08-20T00:00:00+00:00", "late@example.com"),
        )
        connection.execute(
            "UPDATE enrollments SET terminal_reason = 'unsubscribed' "
            "WHERE email = ?",
            ("gone@example.com",),
        )
        connection.commit()

        queue = module.live_enrollments(connection)
    finally:
        connection.close()

    assert [row["email"] for row in queue] == [
        "early@example.com",
        "late@example.com",
    ]


def test_the_queue_can_be_read_one_journey_at_a_time(tmp_path):
    module = enrollment_module()
    connection = module.connect(tmp_path / "e.db")
    try:
        module.record_enrollments(
            connection,
            [
                plan(module),
                plan(
                    module,
                    journey(journey_id="members_favorites_2025"),
                    purchase_id="9002",
                ),
            ],
        )

        mine = module.live_enrollments(connection, "members_favorites_2025")
    finally:
        connection.close()

    assert [row["journey_id"] for row in mine] == ["members_favorites_2025"]
