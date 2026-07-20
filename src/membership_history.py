#!/usr/bin/env python3
"""TYL membership counts at a point in time. THE canonical way to answer
"how many members did we have on <date>".

Do NOT re-derive this. Do NOT classify billing cycle from amount paid: that is
how the 2026-07-20 chart came out wrong. historical_active_counts.active_at()
infers "Annual" from any payment > 3x the nominal price, which invented an
annual tier that does not exist in the purchase data (HM records
recurring_type='monthly' on all 1219 membership purchases) and mislabeled a
$545 monthly payment as annual.

TWO SOURCES, in priority order:

1. HM Active Subscriptions Report CSVs (announce/data/reports/
   active_subscriptions_*.csv) -- GROUND TRUTH, exact, no inference. Carries a
   real Billing Cycle + split_part column: split_part '1year' = annual,
   'month'/'1month' = monthly. Available from 2026-03-19 onward only.

2. marvy.db purchase window -- RECONSTRUCTION for dates before the CSVs exist,
   accurate to about +/-1. Monthly = distinct customers with an actual
   membership purchase in the preceding 31 days (every purchase carries
   recurring_type='monthly', so a monthly window is the real coverage rule).
   Annual = the genuine annual subscriptions whose recorded Created ->
   Subscription Active Until range covers the date.

   Known +/-1 sources, measured 2026-07-16 (27/28 set overlap vs HM):
     - a member mid-lapse still shows a purchase in the window (Hanford:
       canceled Jun 19, access until Jul 14, counted on Jul 16)
     - a member who paid outside the window is missed (Hotchkiss, annual)

Usage:
    membership_history.py                      # mid-July each year (the usual ask)
    membership_history.py 2025-07-16
    membership_history.py 2023-07-16 2024-07-16 2025-07-16
"""
import csv
import datetime
import glob
import os
import sqlite3
import sys

TYL_PRODUCT_ID = 52025
TYL_PRODUCT_NAME = "The Yoga Lifestyle Membership"
REPORTS_GLOB = "/root/twy/announce/data/reports/active_subscriptions_*.csv"
MARVY_DB = "/root/twy/data/marvy.db"
WINDOW_DAYS = 31
ANNUAL_SPLIT = "1year"


def _report_for(date):
    """The HM Active Subscriptions CSV for a date, or None if none exists."""
    stamp = date.strftime("%Y%m%d")
    hits = sorted(glob.glob(REPORTS_GLOB.replace("*", stamp + "*")))
    return hits[-1] if hits else None


def from_hm_report(path):
    """Exact counts from an HM report. Returns (monthly, annual, total)."""
    rows = [r for r in csv.DictReader(open(path))
            if r["Product Name"] == TYL_PRODUCT_NAME and r["Status"] == "Active"]
    annual = sum(1 for r in rows if r["split_part"] == ANNUAL_SPLIT)
    return len(rows) - annual, annual, len(rows)


def known_annuals():
    """Annual subscriptions with their date ranges, from the newest HM report.

    Only annuals still present in a report are visible; one that lapsed before
    2026-03-19 cannot be recovered from any available source.
    """
    reports = sorted(glob.glob(REPORTS_GLOB))
    if not reports:
        return []
    out = []
    for r in csv.DictReader(open(reports[-1])):
        if r["Product Name"] == TYL_PRODUCT_NAME and r["split_part"] == ANNUAL_SPLIT:
            out.append({
                "email": r["Email"].strip().lower(),
                "created": r["Created"][:10],
                "until": r["Subscription Active Until"][:10],
            })
    return out


def from_purchase_window(date, annuals):
    """Reconstruction for dates with no HM report. (monthly, annual, total)."""
    ann_emails = {a["email"] for a in annuals}
    annual = sum(1 for a in annuals if a["created"] <= date.strftime("%Y-%m-%d") <= a["until"])
    lo = (date - datetime.timedelta(days=WINDOW_DAYS)).strftime("%Y-%m-%d")
    hi = date.strftime("%Y-%m-%d") + "T23:59:59"
    conn = sqlite3.connect(MARVY_DB)
    rows = conn.execute(
        "SELECT DISTINCT customer_email FROM purchases "
        "WHERE product_id=? AND created>=? AND created<=?",
        (TYL_PRODUCT_ID, lo, hi),
    ).fetchall()
    conn.close()
    monthly = len({e.strip().lower() for (e,) in rows if e} - ann_emails)
    return monthly, annual, monthly + annual


def counts_for(date, annuals):
    """(monthly, annual, total, source) for a date, preferring HM ground truth."""
    report = _report_for(date)
    if report:
        m, a, t = from_hm_report(report)
        return m, a, t, "HM report (exact)"
    if date < datetime.datetime(2022, 8, 29):
        return None, None, None, "product did not exist (created 2022-08-29)"
    m, a, t = from_purchase_window(date, annuals)
    return m, a, t, "reconstructed (+/-1)"


def main(argv):
    if len(argv) > 1:
        dates = [datetime.datetime.strptime(a, "%Y-%m-%d") for a in argv[1:]]
    else:
        this_year = datetime.date.today().year
        dates = [datetime.datetime(y, 7, 16) for y in range(2022, this_year + 1)]
    annuals = known_annuals()
    print("%-12s %-13s %-12s %-7s %s" % ("Date", "TYL Monthly", "TYL Annual", "Total", "Source"))
    for d in dates:
        m, a, t, src = counts_for(d, annuals)
        if m is None:
            print("%-12s %-13s %-12s %-7s %s" % (d.strftime("%Y-%m-%d"), "-", "-", "-", src))
        else:
            print("%-12s %-13d %-12d %-7d %s" % (d.strftime("%Y-%m-%d"), m, a, t, src))
    if annuals:
        print()
        print("Annual subscriptions on record (both comped, $0 Paid):")
        for x in annuals:
            print("  %s  %s -> %s" % (x["email"], x["created"], x["until"]))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
