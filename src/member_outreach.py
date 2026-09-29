#!/usr/bin/env python3
"""Two nudges for Tiffany in #member-activity (JP 2026-09-28).

The quiet start. A new TYL member who has not come to a single live class in
their first 14 days gets one post, so Tiffany can say hello while it matters.
Members who come to classes early stay far more often: of the 2022 to 2025
starts, two or more live classes in the first week kept 80% for a year, none
kept 38% (vault reports/2026_09_28_membership_retention.md). The post waits
until the attendance data covers the member's whole first 14 days, so a class
taken on day 13 is never missed because the sync had not reached it yet.

The monthly check-ins. On the first run of each month (Mountain time), two
current TYL members are named for a personal check-in: one from members in their
first year, where people leave, and one from the longer-standing core, who
carry most of what TWY earns (28 of 87 members account for 80% of all paid
months). Within each group the least recently picked go first, so nobody in a
group is picked twice before everyone in it has been picked once. The pool is
read fresh every month, so it follows members as they join and leave, and a
member moves from the first-year group to the core on their first
anniversary. When one group is empty both picks come from the other. Members
in their first 30 days wait: the welcome week and the quiet-start post cover
them. The first run records its own month without picking, so the first pair
is named on the first run of the following month.

Both cover The Yoga Lifestyle Membership only. The Archive is ignored: Tiffany
wants to retire it (JP 2026-09-28).

Both are behind contribution.continued("member_outreach"). Off, nothing is
posted and nothing is recorded, exactly as before this existed.

Usage:
    python3 src/member_outreach.py            # post and record
    python3 src/member_outreach.py --dry-run  # print what would post, write nothing
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import sqlite3
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent))

FEATURE = "member_outreach"
DEFAULT_CHANNEL = "C0BH3142LNP"  # #member-activity, where the movement posts go
MT = ZoneInfo("America/Denver")
TYL_PRODUCT_ID = 52025
MEMBERSHIP_PRODUCT_IDS = (52025, 87290)  # TYL Membership, The Archive
TYL_PRODUCT_NAME = "The Yoga Lifestyle Membership"
QUIET_DAYS = 14
QUIET_LOOKBACK_DAYS = 45  # a start older than this is no longer news
NEWCOMER_DAYS = 30  # too new for a check-in
FIRST_YEAR_DAYS = 365
ATTENDANCE_MAX_AGE_HOURS = 36


@dataclass(frozen=True)
class Member:
    cid: int
    name: str
    start: date  # first record of any membership product
    tyl: bool  # a current TYL member, so live classes are part of it


def quiet_starts(
    members: list[Member],
    tyl_start: dict[int, date],
    attended: dict[int, list[date]],
    coverage_through: date | None,
    today: date,
    alerted: set[int],
) -> list[Member]:
    """Current TYL members whose first 14 days are over and fully covered by
    the attendance data, with no live class in them, and not posted before."""
    found = []
    for member in members:
        if not member.tyl or member.cid in alerted:
            continue
        start = tyl_start.get(member.cid)
        if start is None:
            continue
        end = start + timedelta(days=QUIET_DAYS)
        if end > today or (today - start).days > QUIET_LOOKBACK_DAYS:
            continue
        if coverage_through is None or coverage_through < end:
            continue
        if any(start <= day < end for day in attended.get(member.cid, ())):
            continue
        found.append((start, member.cid, member))
    return [member for _, _, member in sorted(found, key=lambda item: item[:2])]


def pick_checkins(
    members: list[Member],
    history: dict[str, list[int]],
    month: str,
    today: date,
) -> list[Member]:
    """Two TYL members for this month's personal check-ins, one from the first
    year and one from the core when both groups have someone eligible.
    Archive-only members are never picked."""
    last_picked: dict[int, str] = {}
    for picked_month, ids in history.items():
        for cid in ids:
            last_picked[cid] = max(last_picked.get(cid, ""), picked_month)

    def order(member: Member) -> tuple[str, str]:
        tiebreak = hashlib.sha256(f"{month}:{member.cid}".encode()).hexdigest()
        return (last_picked.get(member.cid, ""), tiebreak)

    eligible = [m for m in members if m.tyl and (today - m.start).days >= NEWCOMER_DAYS]
    first_year = sorted((m for m in eligible if (today - m.start).days < FIRST_YEAR_DAYS), key=order)
    core = sorted((m for m in eligible if (today - m.start).days >= FIRST_YEAR_DAYS), key=order)
    picks = [group[0] for group in (first_year, core) if group]
    for member in sorted(eligible, key=order):
        if len(picks) >= 2:
            break
        if member not in picks:
            picks.append(member)
    return picks[:2]


def _link(member: Member) -> str:
    return f"<https://app.heymarvelous.com/customers/{member.cid}|{member.name}>"


def quiet_message(quiet: list[Member], tyl_start: dict[int, date]) -> str:
    why = ("Members who come to a class early stay far more often, so this is a "
           "good moment for a personal hello.")
    if len(quiet) == 1:
        start = tyl_start[quiet[0].cid]
        return (f"*New member, no live class yet:* {_link(quiet[0])} joined "
                f"{start:%b} {start.day} and hasn't come to a live class in the "
                f"first two weeks. {why}")
    lines = ["*New members, no live class in their first two weeks:*"]
    for member in quiet:
        start = tyl_start[member.cid]
        lines.append(f"• {_link(member)}, joined {start:%b} {start.day}")
    lines.append(why)
    return "\n".join(lines)


def checkin_message(picks: list[Member], month_label: str) -> str:
    named = [f"{_link(m)} (member since {m.start:%b %Y})" for m in picks]
    who = " and ".join(named)
    head = "Check-ins" if len(picks) > 1 else "Check-in"
    return f"*{head} for {month_label}:* {who}. A short personal note from Tiffany, no agenda."


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------


def load_members(snapshot: Path, database: sqlite3.Connection) -> tuple[list[Member], dict[int, date], dict[str, int]]:
    """Current members from the HM active-subscriptions report, judged as of
    the report's own date (twy_platform.membership), with HM customer ids."""
    from twy_platform.membership import is_member_row, report_date

    as_of = report_date(snapshot)
    email_to_cid = {
        (email or "").strip().lower(): cid
        for cid, email in database.execute("SELECT id, email FROM customers")
        if email
    }
    starts: dict[int, date] = {}
    tyl_start: dict[int, date] = {}
    for cid, product_id, created in database.execute(
        f"SELECT customer_id, product_id, min(created) FROM purchases "
        f"WHERE product_id IN ({','.join('?' * len(MEMBERSHIP_PRODUCT_IDS))}) "
        f"GROUP BY customer_id, product_id",
        MEMBERSHIP_PRODUCT_IDS,
    ):
        day = date.fromisoformat(created[:10])
        starts[cid] = min(starts.get(cid, day), day)
        if product_id == TYL_PRODUCT_ID:
            tyl_start[cid] = day

    people: dict[int, dict] = {}
    with snapshot.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            if not is_member_row(row, as_of):
                continue
            cid = email_to_cid.get((row.get("Email") or "").strip().lower())
            if cid is None or cid not in starts:
                continue
            name = " ".join(p for p in ((row.get("First Name") or "").strip(),
                                        (row.get("Last Name") or "").strip()) if p)
            entry = people.setdefault(cid, {"name": name or str(cid), "tyl": False})
            if (row.get("Product Name") or "").strip() == TYL_PRODUCT_NAME:
                entry["tyl"] = True
    members = [Member(cid, p["name"], starts[cid], p["tyl"]) for cid, p in people.items()]
    return members, tyl_start, email_to_cid


def load_attendance(database: sqlite3.Connection, email_to_cid: dict[str, int],
                    known: set[int]) -> tuple[dict[int, list[date]], date | None, datetime | None]:
    """Live classes attended per member, the newest class the data reaches,
    and when the attendance was last synced."""
    attended: dict[int, list[date]] = defaultdict(list)
    newest = None
    for cid, email, when in database.execute(
        "SELECT customer_id, lower(student_email), event_start_datetime FROM attendance WHERE attended = 1"
    ):
        day = date.fromisoformat(when[:10])
        newest = day if newest is None or day > newest else newest
        key = cid if cid in known else email_to_cid.get(email or "")
        if key is not None:
            attended[key].append(day)
    synced = database.execute("SELECT max(synced_at) FROM attendance").fetchone()[0]
    synced_at = datetime.fromisoformat(synced.replace("Z", "+00:00")) if synced else None
    return attended, newest, synced_at


def load_state(path: Path) -> dict:
    if not path.exists():
        return {"version": 1, "quiet_alerted": {}, "checkins": {}}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("version") != 1:
        raise ValueError("unsupported member outreach state")
    payload.setdefault("quiet_alerted", {})
    payload.setdefault("checkins", {})
    return payload


def save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true",
                        help="print what would post; post nothing, record nothing")
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    from twy_paths import data_root, hm_subscriptions_dir, load_env, marvy_db_path
    from twy_platform.contribution import continued

    load_env()
    if not continued(FEATURE):
        print(json.dumps({"skipped": f"contribution: {FEATURE} is off"}))
        return 0

    from marvelous_memberships import latest_fresh_snapshot
    from twy_platform.slack import slack

    now = datetime.now(timezone.utc)
    today = now.astimezone(MT).date()
    month = today.strftime("%Y-%m")
    snapshot = latest_fresh_snapshot(reports_dir=hm_subscriptions_dir(),
                                     prefix="active_subscriptions", now=now)
    database = sqlite3.connect(f"file:{marvy_db_path()}?mode=ro", uri=True)
    try:
        members, tyl_start, email_to_cid = load_members(snapshot, database)
        attended, newest, synced_at = load_attendance(
            database, email_to_cid, {m.cid for m in members})
    finally:
        database.close()

    state_path = data_root() / "member_outreach" / "state.json"
    first_run = not state_path.exists()
    state = load_state(state_path)
    channel = os.getenv("SLACK_MOVEMENT_CHANNEL", DEFAULT_CHANNEL)
    summary = {"members": len(members), "snapshot": snapshot.name, "quiet": [], "checkins": None}

    fresh = synced_at is not None and (now - synced_at) <= timedelta(hours=ATTENDANCE_MAX_AGE_HOURS)
    quiet = []
    if fresh:
        alerted = {int(cid) for cid in state["quiet_alerted"]}
        quiet = quiet_starts(members, tyl_start, attended, newest, today, alerted)
    else:
        summary["quiet_skipped"] = f"attendance last synced {synced_at}, older than {ATTENDANCE_MAX_AGE_HOURS}h"

    posts = []
    if quiet:
        posts.append(("quiet", quiet, quiet_message(quiet, tyl_start)))
    if month not in state["checkins"]:
        if first_run:
            state["checkins"][month] = []  # the first pair is named next month
        else:
            history = {m: [int(c) for c in ids] for m, ids in state["checkins"].items()}
            picks = pick_checkins(members, history, month, today)
            if picks:
                posts.append(("checkins", picks, checkin_message(picks, today.strftime("%B"))))
            else:
                state["checkins"][month] = []

    failed = False
    for kind, people, text in posts:
        if args.dry_run:
            print("--- would post to %s ---\n%s" % (channel, text))
            continue
        if not slack(text, channel=channel):
            failed = True
            continue
        if kind == "quiet":
            for member in people:
                state["quiet_alerted"][str(member.cid)] = today.isoformat()
            summary["quiet"] = [m.cid for m in people]
        else:
            state["checkins"][month] = [m.cid for m in people]
            summary["checkins"] = [m.cid for m in people]

    if not args.dry_run:
        save_state(state_path, state)
    else:
        summary["dry_run"] = [kind for kind, _, _ in posts]
    print(json.dumps(summary, indent=2, sort_keys=True, default=str))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
