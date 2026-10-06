#!/usr/bin/env python3
"""Two nudges for Tiffany in #member-activity (JP 2026-09-28).

The quiet start. A new TYL member who has not come to a single live class in
their first 14 days gets one post, so Tiffany can say hello while it matters.
Members who come to classes early stay far more often: of the 2022 to 2025
starts, two or more live classes in the first week kept 80% for a year, none
kept 38% (vault reports/2026_09_28_membership_retention.md). The post waits
until the attendance data covers the member's whole first 14 days, so a class
taken on day 13 is never missed because the sync had not reached it yet.

The check-in, every Monday afternoon. Each week one current TYL member is
named for a personal check-in, posted at 14:15 Mountain time on the Monday
(JP 2026-10-06: "make it weekly, one name each Monday around 2:15pm MT";
until then it was two names every other Monday, JP 2026-09-28: "we'll see if
Tiff can reach 1 person per week"). The weeks alternate between two groups,
counting from Monday 2026-09-28: members in their first year, where people
leave, and the longer-standing core, who carry most of what TWY earns (28 of
87 members account for 80% of all paid months). Within each group the least
recently picked go first, so nobody in a group is picked twice before everyone
in it has been picked once. The pool is read fresh every week, so it follows
members as they join and leave, and a member moves from the first-year group
to the core on their first anniversary. When the week's group has nobody
eligible the other group fills in. Members in their first 30 days wait: the
welcome week and the quiet-start post cover them. The post is due from 14:15
MT on the Monday, so the daily run earlier that day never posts it, and a
later run in the same week posts it if the Monday run failed. The post is an
invitation to Tiffany, the week's Kula hello (wording JP 2026-10-06), and the
member's name in it opens a new email to that member. Once it is in the
channel, its link is sent to Tiffany as a direct message from JP, the way the
twy-slack-update skill forwards an update (JP 2026-10-06: "it should post to
member activity and then be forwarded from me"). That send uses JP's
post-only user token, SLACK_JP_POST_TOKEN. A forward that fails is tried again
by every later run that week, and the channel post is never repeated.

Both cover The Yoga Lifestyle Membership only. The Archive is ignored: Tiffany
wants to retire it (JP 2026-09-28).

Both are behind the Labs switch, `maintenance` in ops/contribution.toml, with
no feature name of their own (JP 2026-09-28: "use whatever flag is associated
with Labs, it is part of maintenance"). The job asks it on every run: off,
nothing is posted and nothing is recorded, exactly as before this existed.

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
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent))

DEFAULT_CHANNEL = "C0BH3142LNP"  # #member-activity, where the movement posts go
TIFF_DM_CHANNEL = "D06RJ3K14JZ"  # JP's direct messages with Tiffany (U03EK5CN002)
WORKSPACE_URL = "https://tiffanywoodyogagroup.slack.com"
MT = ZoneInfo("America/Denver")
TYL_PRODUCT_ID = 52025
MEMBERSHIP_PRODUCT_IDS = (52025, 87290)  # TYL Membership, The Archive
TYL_PRODUCT_NAME = "The Yoga Lifestyle Membership"
QUIET_DAYS = 14
QUIET_LOOKBACK_DAYS = 45  # a start older than this is no longer news
NEWCOMER_DAYS = 30  # too new for a check-in
PERIOD_DAYS = 7
PERIOD_EPOCH = date(2026, 9, 28)  # a Monday; weeks start every Monday from here
CHECKIN_TIME = time(14, 15)  # Monday afternoon, Mountain time (JP 2026-10-06)
FIRST_YEAR_DAYS = 365
ATTENDANCE_MAX_AGE_HOURS = 36


@dataclass(frozen=True)
class Member:
    cid: int
    name: str
    start: date  # first record of any membership product
    tyl: bool  # a current TYL member, so live classes are part of it
    email: str = ""  # as on the HM report, for the check-in's email link


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


def period_start(day: date) -> date:
    """The Monday that starts the week `day` falls in."""
    return PERIOD_EPOCH + timedelta(days=PERIOD_DAYS * ((day - PERIOD_EPOCH).days // PERIOD_DAYS))


def checkin_due(now_mt: datetime) -> bool:
    """True from 14:15 Mountain time on the week's Monday until the week ends."""
    due = datetime.combine(period_start(now_mt.date()), CHECKIN_TIME, tzinfo=MT)
    return now_mt >= due


def pick_checkin(
    members: list[Member],
    history: dict[str, list[int]],
    period: str,
    today: date,
) -> Member | None:
    """One TYL member for this week's personal check-in. The weeks alternate
    between the first-year group and the core, first-year on the even weeks
    counted from PERIOD_EPOCH, and the other group fills in when the week's
    group has nobody eligible. Archive-only members are never picked.
    `history` maps each earlier week's Monday to the members it named."""
    last_picked: dict[int, str] = {}
    for picked_period, ids in history.items():
        for cid in ids:
            last_picked[cid] = max(last_picked.get(cid, ""), picked_period)

    def order(member: Member) -> tuple[str, str]:
        tiebreak = hashlib.sha256(f"{period}:{member.cid}".encode()).hexdigest()
        return (last_picked.get(member.cid, ""), tiebreak)

    eligible = [m for m in members if m.tyl and (today - m.start).days >= NEWCOMER_DAYS]
    first_year = sorted((m for m in eligible if (today - m.start).days < FIRST_YEAR_DAYS), key=order)
    core = sorted((m for m in eligible if (today - m.start).days >= FIRST_YEAR_DAYS), key=order)
    week = (date.fromisoformat(period) - PERIOD_EPOCH).days // PERIOD_DAYS
    for group in (first_year, core) if week % 2 == 0 else (core, first_year):
        if group:
            return group[0]
    return None


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


def _slack_text(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def checkin_message(pick: Member, today: date) -> str:
    """The week's Kula hello, an invitation to Tiffany (wording JP
    2026-10-06). The bold name opens a new email to the member. The post
    never knows a member's pronouns, so it uses the first name instead."""
    name = _slack_text(pick.name)
    first = _slack_text(pick.name.split()[0]) if pick.name.split() else name
    since = f"{pick.start:%B}" if pick.start.year == today.year else f"{pick.start:%B %Y}"
    if pick.email:
        link = f"*<mailto:{quote(pick.email, safe='@.+-_')}|{name}>*"
    else:
        link = f"*{_link(pick)}*"
    if (today - pick.start).days < FIRST_YEAR_DAYS:
        words = 'Nothing formal. Just a few words to say "I see you, and I\'m glad you\'re here."'
    else:
        words = ("A few personal words can go a long way toward reminding someone "
                 "that they're part of this Kula.")
    return (f"*This week's Kula hello* :yellow_heart:\n"
            f"{name} has been practicing with you since {since}.\n\n"
            f"If you have a moment this week, send {link} a little hello. {words}\n\n"
            f"Click {first}'s name and an email will open, ready for you.")


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
            entry = people.setdefault(cid, {"name": name or str(cid), "tyl": False,
                                            "email": (row.get("Email") or "").strip()})
            if (row.get("Product Name") or "").strip() == TYL_PRODUCT_NAME:
                entry["tyl"] = True
    members = [Member(cid, p["name"], starts[cid], p["tyl"], p["email"]) for cid, p in people.items()]
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
        return {"version": 1, "quiet_alerted": {}, "checkins": {}, "checkin_posts": {}}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("version") != 1:
        raise ValueError("unsupported member outreach state")
    payload.setdefault("quiet_alerted", {})
    payload.setdefault("checkins", {})
    payload.setdefault("checkin_posts", {})
    return payload


def permalink(channel: str, ts: str) -> str:
    return f"{WORKSPACE_URL}/archives/{channel}/p{ts.replace('.', '')}"


def post_message(token: str | None, channel: str, text: str) -> str | None:
    """chat.postMessage. The message's ts, or None when it did not land.
    Never raises, so a Slack outage fails the run (exit 1) and nothing else."""
    if not token:
        print(f"slack post skipped [{channel}]: no token", file=sys.stderr)
        return None
    import requests

    try:
        body = requests.post(
            "https://slack.com/api/chat.postMessage",
            headers={"Authorization": f"Bearer {token}",
                     "Content-Type": "application/json; charset=utf-8"},
            data=json.dumps({"channel": channel, "text": text}).encode("utf-8"),
            timeout=15,
        ).json()
    except Exception as exc:
        print(f"slack post error [{channel}]: {exc}", file=sys.stderr)
        return None
    if not body.get("ok"):
        print(f"slack post rejected [{channel}]: {body.get('error')}", file=sys.stderr)
        return None
    return body.get("ts")


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
    if not continued():
        print(json.dumps({"skipped": "contribution: the maintenance switch is off"}))
        return 0

    from marvelous_memberships import latest_fresh_snapshot
    from twy_platform.slack import slack

    now = datetime.now(timezone.utc)
    now_mt = now.astimezone(MT)
    today = now_mt.date()
    period = period_start(today)
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
    if checkin_due(now_mt) and period.isoformat() not in state["checkins"]:
        history = {p: [int(c) for c in ids] for p, ids in state["checkins"].items()}
        pick = pick_checkin(members, history, period.isoformat(), today)
        if pick:
            posts.append(("checkin", [pick], checkin_message(pick, today)))
        else:
            state["checkins"][period.isoformat()] = []

    failed = False
    dm_channel = os.getenv("SLACK_JP_TIFF_DM", TIFF_DM_CHANNEL)
    for kind, people, text in posts:
        if args.dry_run:
            print("--- would post to %s ---\n%s" % (channel, text))
            if kind == "checkin":
                print("--- then send its link to %s as JP ---" % dm_channel)
            continue
        if kind == "quiet":
            if not slack(text, channel=channel):
                failed = True
                continue
            for member in people:
                state["quiet_alerted"][str(member.cid)] = today.isoformat()
            summary["quiet"] = [m.cid for m in people]
        else:
            ts = post_message(os.getenv("TWY_REPORTER_BOT_TOKEN"), channel, text)
            if not ts:
                failed = True
                continue
            state["checkins"][period.isoformat()] = [m.cid for m in people]
            state["checkin_posts"][period.isoformat()] = {"channel": channel, "ts": ts}
            summary["checkins"] = [m.cid for m in people]

    # This week's post goes on to Tiffany as a DM from JP, once.
    posted = state["checkin_posts"].get(period.isoformat())
    if posted and not posted.get("forwarded_ts") and not args.dry_run:
        link = permalink(posted["channel"], posted["ts"])
        dm_ts = post_message(os.getenv("SLACK_JP_POST_TOKEN"), dm_channel, link)
        if dm_ts:
            posted["forwarded_ts"] = dm_ts
            summary["forwarded"] = link
        else:
            failed = True

    if not args.dry_run:
        save_state(state_path, state)
    else:
        summary["dry_run"] = [kind for kind, _, _ in posts]
    print(json.dumps(summary, indent=2, sort_keys=True, default=str))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
