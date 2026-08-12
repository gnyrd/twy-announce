"""Tests for the annual-payment footnote in membership_history."""
import sqlite3

import membership_history as mh


def _db(tmp_path, rows):
    db = tmp_path / "marvy.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE purchases "
        "(customer_email TEXT, product_id INTEGER, amount_paid REAL, created TEXT)"
    )
    conn.executemany("INSERT INTO purchases VALUES (?,?,?,?)", rows)
    conn.commit()
    conn.close()
    return str(db)


def test_paying_annual_is_not_comped_despite_zero_enrollment_row(tmp_path):
    db = _db(tmp_path, [
        ("Payer@Example.com", mh.TYL_PRODUCT_ID, 0.0, "2023-05-01T00:00:00"),
        ("payer@example.com", mh.TYL_PRODUCT_ID, 1075.0, "2023-07-09T00:00:00"),
        ("payer@example.com", mh.TYL_PRODUCT_ID, 1075.0, "2024-07-23T00:00:00"),
        ("payer@example.com", 99999, 29.0, "2024-03-23T00:00:00"),
    ])
    annuals = [
        {"email": "payer@example.com", "created": "2023-05-01", "until": "2027-07-12"},
    ]
    [summary] = mh.annual_payment_summary(annuals, db_path=db)
    assert summary["comped"] is False
    assert summary["paid_total"] == 2150.0
    assert summary["paid_count"] == 2
    assert summary["first_paid"] == "2023-07-09"
    assert summary["latest_paid"] == "2024-07-23"


def test_member_with_only_zero_rows_is_comped(tmp_path):
    db = _db(tmp_path, [
        ("gift@example.com", mh.TYL_PRODUCT_ID, 0.0, "2024-01-01T00:00:00"),
        ("gift@example.com", mh.TYL_PRODUCT_ID, 0.0, "2025-01-01T00:00:00"),
    ])
    annuals = [
        {"email": "gift@example.com", "created": "2024-01-01", "until": "2027-01-01"},
    ]
    [summary] = mh.annual_payment_summary(annuals, db_path=db)
    assert summary["comped"] is True
    assert summary["paid_total"] == 0
    assert summary["paid_count"] == 0
    assert summary["first_paid"] is None
    assert summary["latest_paid"] is None


def test_a_cancelled_tyl_member_with_access_left_still_counts(tmp_path, monkeypatch):
    """JP 2026-08-12: currently has access, not currently renewing."""
    import membership_history as mh

    path = tmp_path / "active_subscriptions_20260812T072001Z.csv"
    path.write_text(
        "Billing Cycle,Created,Email,First Name,Last Name,Paid,Price,Product Name,Renewal Date,Status,Subscription Active Until,split_part\n"
        "Monthly,2026-07-12T00:00:00Z,a@x.com,A,A,99,99,The Yoga Lifestyle Membership,2026-08-12T00:00:00Z,Active,2026-08-15,1month\n"
        "Monthly,2025-08-11T00:00:00Z,lia@x.com,Lia,Spiegel,948,99,The Yoga Lifestyle Membership,2027-08-11T00:00:00Z,Canceled,2027-08-11,1year\n"
        "Monthly,2026-06-01T00:00:00Z,e@x.com,E,E,99,99,The Yoga Lifestyle Membership,2026-07-01T00:00:00Z,Canceled,2026-07-04,1month\n")
    monthly, annual, total = mh.from_hm_report(str(path))
    assert (monthly, annual, total) == (1, 1, 2)
