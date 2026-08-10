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
