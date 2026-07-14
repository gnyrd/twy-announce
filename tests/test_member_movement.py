"""Tests for get_member_movement + format_movement_post (standalone joins/cancels post)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from daily_status_report import get_member_movement, format_movement_post

ACTIVE_HDR = "Billing Cycle,Created,Email,First Name,Last Name,Paid,Price,Product Name,Renewal Date,Status,Subscription Active Until,split_part"
CANCEL_HDR = "canceled_at,email,first_name,last_name,price,product_name,renewal_date,subscription_active_until"


def _write(dirp, name, header, rows):
    (dirp / name).write_text("\n".join([header] + rows) + "\n")


def test_join_and_cancel_detected(tmp_path):
    _write(tmp_path, "active_subscriptions_20260713T010000Z.csv", ACTIVE_HDR, [
        "Monthly,2026-01-01T00:00:00Z,old@x.com,Old,Member,99.0,99.0,The Yoga Lifestyle Membership,2026-08-01T00:00:00Z,Active,2026-08-01,1month",
    ])
    _write(tmp_path, "active_subscriptions_20260714T010000Z.csv", ACTIVE_HDR, [
        "Monthly,2026-01-01T00:00:00Z,old@x.com,Old,Member,99.0,99.0,The Yoga Lifestyle Membership,2026-08-01T00:00:00Z,Active,2026-08-01,1month",
        "Monthly,2026-07-12T10:00:00Z,new@x.com,Lynne,Love,99.0,99.0,The Yoga Lifestyle Membership,2026-08-12T00:00:00Z,Active,2026-08-12,1month",
    ])
    _write(tmp_path, "canceled_subscriptions_20260713T010000Z.csv", CANCEL_HDR, [])
    _write(tmp_path, "canceled_subscriptions_20260714T010000Z.csv", CANCEL_HDR, [
        "2026-06-19T15:59:49Z,gone@x.com,Michelle,Hanford,99.0,The Yoga Lifestyle Membership,2026-07-14T23:37:37Z,2026-07-14",
    ])
    joins, cancels = get_member_movement(tmp_path, db_path=tmp_path / "no.db")
    assert len(joins) == 1 and len(cancels) == 1
    assert "Lynne Love" in joins[0] and "signed up Jul 12" in joins[0]
    assert "Michelle Hanford" in cancels[0]
    assert "canceled Jun 19" in cancels[0] and "access until Jul 14" in cancels[0]
    msg = format_movement_post((joins, cancels))
    assert msg.splitlines()[0].startswith("*Joined*:")
    assert msg.splitlines()[1].startswith("*Canceled*:")


def test_no_movement_means_empty_post(tmp_path):
    row = "Monthly,2026-01-01T00:00:00Z,old@x.com,Old,Member,99.0,99.0,The Yoga Lifestyle Membership,2026-08-01T00:00:00Z,Active,2026-08-01,1month"
    _write(tmp_path, "active_subscriptions_20260713T010000Z.csv", ACTIVE_HDR, [row])
    _write(tmp_path, "active_subscriptions_20260714T010000Z.csv", ACTIVE_HDR, [row])
    _write(tmp_path, "canceled_subscriptions_20260713T010000Z.csv", CANCEL_HDR, [])
    _write(tmp_path, "canceled_subscriptions_20260714T010000Z.csv", CANCEL_HDR, [])
    movement = get_member_movement(tmp_path, db_path=tmp_path / "no.db")
    assert movement == ([], [])
    assert format_movement_post(movement) == ""


def test_single_snapshot_no_history(tmp_path):
    _write(tmp_path, "active_subscriptions_20260714T010000Z.csv", ACTIVE_HDR, [])
    joins, cancels = get_member_movement(tmp_path, db_path=tmp_path / "no.db")
    assert joins == [] and cancels == []


def test_ondemand_library_cancel_ignored(tmp_path):
    _write(tmp_path, "canceled_subscriptions_20260713T010000Z.csv", CANCEL_HDR, [])
    _write(tmp_path, "canceled_subscriptions_20260714T010000Z.csv", CANCEL_HDR, [
        "2026-07-13T00:00:00Z,b@x.com,Bundle,Orphan,0.0,The Yoga Lifestyle: On-demand Library,2026-08-01T00:00:00Z,2026-08-01",
    ])
    joins, cancels = get_member_movement(tmp_path, db_path=tmp_path / "no.db")
    assert cancels == []
