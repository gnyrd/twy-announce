#!/usr/bin/env python3
"""
Reconstruct active-subscription counts as of any target date.

Replaces the buggy `get_product_counts_ago` / `get_member_count_ago` logic in
daily_status_report.py. The old approach used a proxy that silently excluded
subscribers who churned between the target date and now, and it attributed
every historical data point's billing cycle to the customer's MOST RECENT
payment ever (not the payment that actually covered the target date).

This module asks the correct question: for each (customer, product) pair,
is there a recurring-type paid purchase P such that P covered the target
date? Coverage = P.created + 30d for monthly-amount payments, +365d for
annual-amount payments. Billing cycle for the count bucket is taken from
THAT covering purchase, not from any later payment.
"""

import sqlite3
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Optional

from twy_paths import marvy_db_path

# Coverage windows in days. Monthly uses 31 (max month length) to avoid
# spurious 1-day gaps at renewal time for subscribers paying on 31-day cycles.
# Annual uses 366 for the same reason (leap-year safety).
MONTHLY_COVERAGE_DAYS = 31
ANNUAL_COVERAGE_DAYS = 366

# Amount / nominal-price ratio threshold for classifying a purchase as Annual.
# Empirically, TYL Membership (price=$99) has monthly payments $5.75-$150 and
# annual payments $1000-$1200 with a clean gap. The Archive (price=$24) has
# monthly $24 and annual $222. A multiplier of 3 separates the two cleanly.
ANNUAL_PRICE_MULTIPLIER = 3.0

# Products excluded from membership accounting. This string is the TYL legacy
# library (distinct from the current 'The Yoga Lifestyle Membership' product).
EXCLUDED_PRODUCT_NAMES = {"The Yoga Lifestyle: On-demand Library"}


def _classify_billing_cycle(amount_paid: float, product_price: Optional[float]) -> str:
    """Return 'Annual' or 'Monthly' for a single purchase."""
    if product_price is None or product_price <= 0:
        return "Monthly"
    return "Annual" if amount_paid > product_price * ANNUAL_PRICE_MULTIPLIER else "Monthly"


def _coverage_days(cycle: str) -> int:
    return ANNUAL_COVERAGE_DAYS if cycle == "Annual" else MONTHLY_COVERAGE_DAYS


def _parse_created(created_str: str) -> Optional[datetime]:
    """Parse a marvy purchase.created timestamp as a naive datetime."""
    if not created_str:
        return None
    head = created_str[:19]
    try:
        return datetime.strptime(head, "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return None


def active_at(target_date: datetime, db_path: Optional[str] = None) -> Dict[str, Dict[str, int]]:
    """
    Count subscriptions active at `target_date`, grouped by product + billing cycle.

    Returns: {product_name: {'Monthly': int, 'Annual': int}}

    A subscription (customer_id, product_id) is active at D iff there exists a
    purchase P for that pair where P.recurring_type is NOT NULL (i.e. the
    product is a subscription-type), P.amount_paid > 0, and
    P.created <= D < P.created + coverage_days(P).

    The billing-cycle bucket is taken from the most recent covering purchase,
    so a customer who renewed at Monthly for a while and then upgraded to
    Annual shows up as Monthly for dates covered by their monthly payments and
    Annual for dates covered by their annual payment.

    `target_date` should be a naive local datetime matching the format used by
    daily_status_report (e.g. `datetime.now() - timedelta(days=30)`).
    """
    db = db_path or str(marvy_db_path())
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row

    target_iso = target_date.strftime("%Y-%m-%dT%H:%M:%S")

    rows = conn.execute(
        """
        SELECT p.customer_id,
               p.product_id,
               pr.product_name,
               pr.price,
               p.amount_paid,
               p.created
        FROM purchases p
        JOIN products pr ON pr.id = p.product_id
        WHERE p.amount_paid > 0
          AND p.recurring_type IS NOT NULL
          AND substr(p.created, 1, 19) <= ?
        """,
        (target_iso,),
    ).fetchall()
    conn.close()

    # Exclude products by name (SQL LIKE would also work, but filter in Python
    # for clarity and to keep the exclude list in one place).
    target_naive = target_date.replace(tzinfo=None) if getattr(target_date, "tzinfo", None) else target_date

    # For each (customer, product), track the most recent covering purchase.
    # Key: (customer_id, product_id) -> (product_name, cycle, created_dt)
    covered: Dict[tuple, tuple] = {}
    for r in rows:
        if r["product_name"] in EXCLUDED_PRODUCT_NAMES:
            continue
        created = _parse_created(r["created"])
        if created is None:
            continue
        cycle = _classify_billing_cycle(r["amount_paid"], r["price"])
        coverage_end = created + timedelta(days=_coverage_days(cycle))
        if not (created <= target_naive < coverage_end):
            continue
        key = (r["customer_id"], r["product_id"])
        existing = covered.get(key)
        if existing is None or created > existing[2]:
            covered[key] = (r["product_name"], cycle, created)

    counts: Dict[str, Dict[str, int]] = defaultdict(lambda: {"Monthly": 0, "Annual": 0})
    for _, (name, cycle, _created) in covered.items():
        counts[name][cycle] += 1
    return dict(counts)


def active_count_at(target_date: datetime, db_path: Optional[str] = None) -> int:
    """Total count of active recurring subscriptions at target_date."""
    counts = active_at(target_date, db_path)
    return sum(c["Monthly"] + c["Annual"] for c in counts.values())


# ---------------------------------------------------------------------------
# Self-test / smoke: run as `python3 historical_active_counts.py`
# Compares:
#   - Current count via this module (active_at(now))  vs
#   - Current count via the live subscription_active flag in the subscriptions table
# plus prints the historical series at 0/1/7/30/90/365 days ago using this module.
# ---------------------------------------------------------------------------

def _self_test() -> int:
    """Return 0 on success, nonzero on unexpected drift."""
    from twy_paths import load_env
    load_env()

    now = datetime.now()
    print("=" * 70)
    print("historical_active_counts self-test")
    print("now (naive local): {0}".format(now.isoformat()))
    print("=" * 70)

    # 1. Current count via new module
    new_now = active_at(now)
    new_total_now = sum(c["Monthly"] + c["Annual"] for c in new_now.values())

    # 2. Current count via live flag (what the report uses for TODAY)
    db = str(marvy_db_path())
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT pr.product_name,
               CASE
                   WHEN last_pay.amount_paid > pr.price * 3 THEN 'Annual'
                   ELSE 'Monthly'
               END AS billing_cycle,
               COUNT(*) AS cnt
        FROM subscriptions s
        JOIN products pr ON pr.id = s.product_id
        JOIN (
            SELECT customer_id, product_id, amount_paid,
                   ROW_NUMBER() OVER (PARTITION BY customer_id, product_id ORDER BY created DESC) AS rn
            FROM purchases WHERE amount_paid > 0
        ) last_pay
          ON last_pay.customer_id = s.customer_id
         AND last_pay.product_id = s.product_id
         AND last_pay.rn = 1
        WHERE s.subscription_active = 1
          AND pr.product_name != 'The Yoga Lifestyle: On-demand Library'
        GROUP BY pr.product_name, billing_cycle
        """,
    ).fetchall()
    conn.close()
    live_now: Dict[str, Dict[str, int]] = defaultdict(lambda: {"Monthly": 0, "Annual": 0})
    for r in rows:
        live_now[r["product_name"]][r["billing_cycle"]] = r["cnt"]
    live_now = dict(live_now)
    live_total_now = sum(c["Monthly"] + c["Annual"] for c in live_now.values())

    print("\nCURRENT (today) — new module vs live subscription_active flag:")
    print("  {0:<42s} {1:>10s} {2:>10s}".format("product / cycle", "new", "live"))
    all_products = set(new_now.keys()) | set(live_now.keys())
    for name in sorted(all_products):
        for cycle in ("Monthly", "Annual"):
            a = new_now.get(name, {}).get(cycle, 0)
            b = live_now.get(name, {}).get(cycle, 0)
            flag = ""
            if a != b:
                flag = "  <-- DIFF"
            print("  {0:<42s} {1:>10d} {2:>10d}{3}".format("{0} / {1}".format(name[:30], cycle), a, b, flag))
    print("  {0:<42s} {1:>10d} {2:>10d}".format("TOTAL", new_total_now, live_total_now))

    # 3. Historical series via new module
    print("\nHISTORICAL series via new module (should be >= current for any D if churn happened):")
    print("  {0:<18s} {1:>10s}".format("as-of", "total"))
    for days_ago in (0, 1, 7, 30, 90, 365, 730):
        d = now - timedelta(days=days_ago)
        total = active_count_at(d)
        print("  {0:<18s} {1:>10d}".format(d.strftime("%Y-%m-%d") + " ({0}d)".format(days_ago), total))

    # Basic sanity: today's counts via new module should roughly match live
    # flag (not required to match exactly — marvy sync lag can introduce small
    # drift). Flag if |diff| > 5 as a loose sanity threshold.
    diff = abs(new_total_now - live_total_now)
    if diff > 5:
        print("\nWARNING: today's totals diverge by {0} ({1} new vs {2} live).".format(diff, new_total_now, live_total_now))
        return 1
    print("\nOK: today's totals within sanity band (|diff| = {0}).".format(diff))
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(_self_test())
