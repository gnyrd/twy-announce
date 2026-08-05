from datetime import datetime, timezone
import stat

import pytest

import marvelous_memberships as memberships


def test_snapshot_writer_keeps_empty_canceled_report(tmp_path):
    path = memberships.write_report_snapshot(
        [],
        reports_dir=tmp_path,
        prefix="canceled_subscriptions",
        fields=memberships.CANCELED_FIELDS,
        now=datetime(2026, 8, 5, 7, 20, tzinfo=timezone.utc),
    )

    assert path.name == "canceled_subscriptions_20260805T072000Z.csv"
    assert path.read_text(encoding="utf-8") == ",".join(
        memberships.CANCELED_FIELDS
    ) + "\n"
    assert stat.S_IMODE(tmp_path.stat().st_mode) == 0o700
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_latest_snapshot_rejects_stale_data(tmp_path):
    memberships.write_report_snapshot(
        [{"Email": "member@example.com"}],
        reports_dir=tmp_path,
        prefix="active_subscriptions",
        fields=memberships.ACTIVE_FIELDS,
        now=datetime(2026, 8, 4, 7, 20, tzinfo=timezone.utc),
    )

    with pytest.raises(RuntimeError, match="stale"):
        memberships.latest_fresh_snapshot(
            reports_dir=tmp_path,
            prefix="active_subscriptions",
            now=datetime(2026, 8, 5, 13, 0, tzinfo=timezone.utc),
            max_age_hours=26,
        )


def test_membership_program_uses_canonical_products_only():
    assert memberships.membership_program(
        "The Yoga Lifestyle Membership"
    ) == "Yoga Lifestyle"
    assert memberships.membership_program("The Archive") == "Archive"
    assert memberships.membership_program(
        "The Yoga Lifestyle: On-demand Library"
    ) is None
