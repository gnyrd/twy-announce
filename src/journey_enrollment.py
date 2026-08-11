#!/usr/bin/env python3
"""Journey enrollment: who is in a sequence, and which email they are owed next.

A journey starts by itself when someone buys the product it is bound to. The
product sync already tells a real acquisition apart from an automatic recurring
charge, so this module supplies the two things it does not have: the schedule a
new enrollment starts on, and the table that state survives in between runs.

One row per contact per journey, keyed on that pair, so a replayed purchase is a
no-op rather than a second sequence arriving in someone's inbox. Sending is the
drip runner's job: nothing here talks to a provider.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3

# A journey that goes Active today is for whoever buys next, not for the back
# catalogue. Normal running cannot reach back anyway, because the product sync
# only ever plans purchases it has not processed before. This bound is what
# stops a lost or rebuilt state file from turning into hundreds of unwanted
# emails on the next tick, so it is deliberately a fail-safe, not a policy.
DEFAULT_MAX_PURCHASE_AGE_DAYS = 7

SCHEMA = """
CREATE TABLE IF NOT EXISTS enrollments (
    journey_id      TEXT    NOT NULL,
    email           TEXT    NOT NULL,
    customer_id     TEXT    NOT NULL,
    product_id      TEXT    NOT NULL,
    purchase_id     TEXT    NOT NULL,
    enrolled_at     TEXT    NOT NULL,
    next_index      INTEGER NOT NULL,
    next_due_at     TEXT,
    terminal_reason TEXT,
    updated_at      TEXT    NOT NULL,
    PRIMARY KEY (journey_id, email)
);
CREATE INDEX IF NOT EXISTS enrollments_due
    ON enrollments (next_due_at)
    WHERE terminal_reason IS NULL;
"""


def _schema() -> str:
    """Both tables. The runner needs the ledger in the same store."""
    return SCHEMA + SENDS_SCHEMA


@dataclass(frozen=True)
class PlannedEnrollment:
    journey_id: str
    email: str
    customer_id: str
    product_id: str
    purchase_id: str
    enrolled_at: str
    next_index: int
    next_due_at: str


def connect(path) -> sqlite3.Connection:
    """Open the enrollment store, creating it and its schema when absent."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(target))
    connection.row_factory = sqlite3.Row
    ensure_schema(connection)
    return connection


def ensure_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(_schema())
    connection.commit()


def parse_timestamp(value) -> datetime:
    """Read a provider timestamp as an aware UTC datetime.

    Marvelous stamps purchases with a trailing Z, which fromisoformat rejects on
    older Pythons and which we normalize rather than depend on.
    """
    text = str(value or "").strip()
    if not text:
        raise ValueError("purchase has no created timestamp")
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    stamp = datetime.fromisoformat(text)
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp.astimezone(timezone.utc)


def plan_enrollment(
    journey: dict,
    *,
    email: str,
    customer_id: str,
    product_id: str,
    purchase_id: str,
    purchased_at: str,
    now: datetime,
    max_purchase_age_days: int = DEFAULT_MAX_PURCHASE_AGE_DAYS,
):
    """Return the enrollment this purchase starts, or None if it starts none.

    None covers the three ways a purchase legitimately enrolls nobody: the
    journey has no emails, the journey is not Active, or the purchase is older
    than the freshness bound.
    """
    from twy_platform.journeys import due_offsets

    if not journey.get("active"):
        return None
    offsets = due_offsets(journey)
    if not offsets:
        return None
    bought = parse_timestamp(purchased_at)
    if bought < now - timedelta(days=max_purchase_age_days):
        return None
    due = bought + timedelta(days=offsets[0])
    return PlannedEnrollment(
        journey_id=str(journey["journey_id"]),
        email=str(email).strip().lower(),
        customer_id=str(customer_id),
        product_id=str(product_id),
        purchase_id=str(purchase_id),
        enrolled_at=bought.isoformat(),
        next_index=0,
        next_due_at=due.isoformat(),
    )


def record_enrollments(connection: sqlite3.Connection, enrollments) -> int:
    """Insert new enrollments, ignoring any pair already enrolled.

    Returns how many rows were new. Ignoring rather than replacing is what makes
    a re-run safe: someone already three emails into a sequence must never be
    reset to the beginning by a replayed purchase.
    """
    rows = list(enrollments)
    if not rows:
        return 0
    stamp = datetime.now(timezone.utc).isoformat()
    inserted = 0
    for enrollment in rows:
        cursor = connection.execute(
            """
            INSERT OR IGNORE INTO enrollments (
                journey_id, email, customer_id, product_id, purchase_id,
                enrolled_at, next_index, next_due_at, terminal_reason, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, ?)
            """,
            (
                enrollment.journey_id,
                enrollment.email,
                enrollment.customer_id,
                enrollment.product_id,
                enrollment.purchase_id,
                enrollment.enrolled_at,
                enrollment.next_index,
                enrollment.next_due_at,
                stamp,
            ),
        )
        inserted += cursor.rowcount or 0
    connection.commit()
    return inserted


def enrollment_for(connection: sqlite3.Connection, journey_id: str, email: str):
    """One enrollment row as a dict, or None. For reporting and for tests."""
    row = connection.execute(
        "SELECT * FROM enrollments WHERE journey_id = ? AND email = ?",
        (str(journey_id), str(email).strip().lower()),
    ).fetchone()
    return dict(row) if row is not None else None


def live_enrollments(connection: sqlite3.Connection, journey_id: str | None = None):
    """Enrollments still mid-sequence, oldest due first. The runner's queue."""
    if journey_id is None:
        rows = connection.execute(
            "SELECT * FROM enrollments WHERE terminal_reason IS NULL "
            "ORDER BY next_due_at, journey_id, email"
        ).fetchall()
    else:
        rows = connection.execute(
            "SELECT * FROM enrollments WHERE terminal_reason IS NULL "
            "AND journey_id = ? ORDER BY next_due_at, email",
            (str(journey_id),),
        ).fetchall()
    return [dict(row) for row in rows]


SENDS_SCHEMA = """
CREATE TABLE IF NOT EXISTS sends (
    journey_id   TEXT    NOT NULL,
    email        TEXT    NOT NULL,
    email_index  INTEGER NOT NULL,
    status       TEXT    NOT NULL,
    subject      TEXT    NOT NULL,
    claimed_at   TEXT    NOT NULL,
    sent_at      TEXT,
    PRIMARY KEY (journey_id, email, email_index)
);
CREATE INDEX IF NOT EXISTS sends_by_time ON sends (claimed_at);
"""

CLAIMED = "claimed"
SENT = "sent"


def claim_send(connection, *, journey_id, email, email_index, subject):
    """Reserve one email for one person, or report that it is already taken.

    Two phases on purpose. The claim goes in before the provider call and flips
    to sent after it returns, so a crash in between leaves a row that says
    plainly that nobody knows whether it went out. That row then blocks a retry:
    a missing email is recoverable by hand, a duplicate in a member's inbox is
    not.
    """
    stamp = datetime.now(timezone.utc).isoformat()
    cursor = connection.execute(
        """
        INSERT OR IGNORE INTO sends (
            journey_id, email, email_index, status, subject, claimed_at, sent_at
        ) VALUES (?, ?, ?, ?, ?, ?, NULL)
        """,
        (
            str(journey_id),
            str(email).strip().lower(),
            int(email_index),
            CLAIMED,
            str(subject),
            stamp,
        ),
    )
    connection.commit()
    return bool(cursor.rowcount)


def mark_sent(connection, *, journey_id, email, email_index):
    """Record that the provider accepted it. Only ever called after it did."""
    connection.execute(
        "UPDATE sends SET status = ?, sent_at = ? "
        "WHERE journey_id = ? AND email = ? AND email_index = ?",
        (
            SENT,
            datetime.now(timezone.utc).isoformat(),
            str(journey_id),
            str(email).strip().lower(),
            int(email_index),
        ),
    )
    connection.commit()


def unresolved_sends(connection):
    """Claims that never became sends. Somebody has to look at these."""
    rows = connection.execute(
        "SELECT * FROM sends WHERE status = ? ORDER BY claimed_at",
        (CLAIMED,),
    ).fetchall()
    return [dict(row) for row in rows]


def sends_for(connection, journey_id):
    """Everything sent for one journey, oldest first. Reporting reads this."""
    rows = connection.execute(
        "SELECT * FROM sends WHERE journey_id = ? ORDER BY claimed_at, email",
        (str(journey_id),),
    ).fetchall()
    return [dict(row) for row in rows]


def advance(connection, *, journey_id, email, next_index, next_due_at):
    """Move somebody to the next email in their sequence."""
    connection.execute(
        "UPDATE enrollments SET next_index = ?, next_due_at = ?, updated_at = ? "
        "WHERE journey_id = ? AND email = ?",
        (
            int(next_index),
            str(next_due_at),
            datetime.now(timezone.utc).isoformat(),
            str(journey_id),
            str(email).strip().lower(),
        ),
    )
    connection.commit()


def finish(connection, *, journey_id, email, reason):
    """Stop somebody's sequence for good, recording why."""
    connection.execute(
        "UPDATE enrollments SET terminal_reason = ?, next_due_at = NULL, "
        "updated_at = ? WHERE journey_id = ? AND email = ?",
        (
            str(reason),
            datetime.now(timezone.utc).isoformat(),
            str(journey_id),
            str(email).strip().lower(),
        ),
    )
    connection.commit()


def due_enrollments(connection, *, now):
    """Everyone owed an email at this moment, oldest due first.

    Plain elapsed time, no sending window: whoever bought at 3:30am is somebody
    who was awake at 3:30am (JP 2026-08-11). Do not add a window here without
    asking him again.
    """
    rows = connection.execute(
        "SELECT * FROM enrollments WHERE terminal_reason IS NULL "
        "AND next_due_at IS NOT NULL AND next_due_at <= ? "
        "ORDER BY next_due_at, journey_id, email",
        (now.isoformat(),),
    ).fetchall()
    return [dict(row) for row in rows]
