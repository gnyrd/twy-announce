"""The daily Slack report counts members the way JP defines them.

This file exists because nothing tested `counts_from_report` at all: the
numbers posted to Tiff's Slack every day had no pin on who they count.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from daily_status_report import counts_from_report

HEADER = "Billing Cycle,Created,Email,First Name,Last Name,Paid,Price,Product Name,Renewal Date,Status,Subscription Active Until,split_part"
ROW_ACTIVE = "Monthly,2026-07-12T00:00:00Z,a@x.com,A,A,99,99,The Yoga Lifestyle Membership,2026-08-12T00:00:00Z,Active,2026-08-15,1month"
ROW_LIA = "Other,2025-08-11T00:00:00Z,lia@x.com,Lia,Spiegel,222,24,The Archive,2027-08-11T00:00:00Z,Canceled,2027-08-11,1year"
ROW_LAPSED = "Monthly,2026-06-01T00:00:00Z,e@x.com,E,E,99,99,The Yoga Lifestyle Membership,2026-07-01T00:00:00Z,Canceled,2026-07-04,1month"


def write(tmp_path, rows, name="active_subscriptions_20260812T072001Z.csv"):
    path = tmp_path / name
    path.write_text(HEADER + "\n" + "\n".join(rows) + "\n")
    return path


def test_a_cancelled_subscriber_with_access_left_still_counts(tmp_path):
    """JP 2026-08-12: a member is someone who currently has access."""
    counts = counts_from_report(write(tmp_path, [ROW_ACTIVE, ROW_LIA, ROW_LAPSED]))
    assert counts["The Archive"]["Annual"] == 1
    assert counts["The Yoga Lifestyle Membership"]["Monthly"] == 1


def test_lapsed_access_is_not_counted(tmp_path):
    counts = counts_from_report(write(tmp_path, [ROW_LAPSED]))
    assert counts == {}


def test_a_historical_report_is_judged_as_of_its_own_date(tmp_path):
    """The same lapsed row counts in a report captured while it was live."""
    counts = counts_from_report(
        write(tmp_path, [ROW_LAPSED], name="active_subscriptions_20260615T072001Z.csv"))
    assert counts["The Yoga Lifestyle Membership"]["Monthly"] == 1
