#!/usr/bin/env python3
"""Yoga Habit class report, one week after each Habit class (TRIAL feature
habit_report, JP 2026-10-14).

Seven days after a Habit class, Friend posts in #updates a short summary of
how that class did next to the two before it, and JP forwards the post to
Tiffany as a direct message, the same delivery as the Kula Hello:

  - registered, attended live and claimed the recording, each also for
    non-members, and non-members who joined The Yoga Lifestyle since
  - the month's landing page visits and Register clicks
  - the month's Habit emails: delivered, opens, clicks
  - a few lines on why the numbers moved, written by Claude from those facts
    and the calendar around the class (JP: "the comment about Indigenous
    Peoples' Day is a good insight")

Members are read from the HM Active Subscriptions report on or before the
class date (the ground truth for who is a member). A recording claim is a
purchase of that month's "FREE CLASS: Yoga Habit <Month> <Year>" product.

Off (habit_report false in ops/contribution.toml): twy-run skips the job and
nothing is posted. State in <data_root>/habit_report/state.json keeps one
post and one forward per class.

Usage:
  habit_report.py              post when a Habit class was seven days ago
  habit_report.py --dry-run    print the message, post nothing, write nothing
  habit_report.py --event ID   report on that event now (with --dry-run to preview)
"""
import argparse
import csv
import json
import os
import re
import sqlite3
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent))

from twy_paths import (data_root, hm_subscriptions_dir, journey_path, load_env, marvy_db_path,
                       newsletter_path, sendgrid_event_db_path, social_growth_dir, twy_root)

MT = ZoneInfo("America/Denver")
MODEL = "claude-opus-5-5"
JOIN_WINDOW_DAYS = 21
REPORT_AFTER_DAYS = 7


# ---------------------------------------------------------------------------
# Facts
# ---------------------------------------------------------------------------
def habit_events(db):
    """[(event_id, name, MT date)] for every Habit class with registrations."""
    rows = db.execute("SELECT event_id, event_name, MIN(event_start_datetime) FROM attendance "
                      "WHERE event_name LIKE 'Habit:%' GROUP BY event_id ORDER BY 3").fetchall()
    return [(eid, name, datetime.fromisoformat(start.replace("Z", "+00:00")).astimezone(MT).date())
            for eid, name, start in rows]


def members_on(day, reports_dir=None):
    """Emails of Yoga Lifestyle members in the newest HM Active Subscriptions
    report dated on or before `day`; None when no report is that old."""
    from twy_platform.membership import is_member_row, report_date
    reports_dir = Path(reports_dir or hm_subscriptions_dir())
    stamp = day.strftime("%Y%m%d")
    hits = [p for p in sorted(reports_dir.glob("active_subscriptions_*.csv"))
            if p.name.split("_")[-1][:8] <= stamp]
    if not hits:
        return None
    asof = report_date(hits[-1])
    with open(hits[-1], newline="") as f:
        return {(r.get("Email") or "").strip().lower() for r in csv.DictReader(f)
                if is_member_row(r, asof) and "Lifestyle" in (r.get("Product Name") or "")}


def class_stats(db, event_id, name, day, members, today):
    emails = {}
    for email, attended, created in db.execute(
            "SELECT lower(coalesce(nullif(a.student_email, ''), cu.email)), a.attended, a.registration_created "
            "FROM attendance a LEFT JOIN customers cu ON cu.id = a.customer_id WHERE a.event_id = ?", (event_id,)):
        if not email:
            continue
        prev = emails.get(email, (0, None))
        emails[email] = (max(prev[0], attended or 0), created or prev[1])
    members = members or set()
    nonmembers = {e for e in emails if e not in members}
    product = f"FREE CLASS: Yoga Habit {day:%B %Y}"
    claims = {r[0] for r in db.execute(
        "SELECT lower(customer_email) FROM purchases WHERE product_name = ?", (product,))}
    end = min(day + timedelta(days=JOIN_WINDOW_DAYS), today + timedelta(days=1))
    joined = []
    for email in sorted(nonmembers):
        row = db.execute("SELECT customer_name FROM purchases WHERE lower(customer_email) = ? AND "
                         "product_name LIKE '%Lifestyle%' AND amount_paid > 0 AND created >= ? AND created < ?",
                         (email, day.isoformat(), end.isoformat())).fetchone()
        if row:
            joined.append(row[0])
    def days_before(created):
        return (day - date.fromisoformat(created[:10])).days if created else None
    timing = [days_before(c) for _, c in emails.values()]
    return {
        "event_id": event_id, "name": name.replace("Habit: ", ""), "date": day.isoformat(),
        "weekday": f"{day:%A}",
        "registered": len(emails), "nonmember_registered": len(nonmembers),
        "attended": sum(a for a, _ in emails.values()),
        "nonmember_attended": sum(emails[e][0] for e in nonmembers),
        "claimed": len(claims), "nonmember_claimed": len(claims - members),
        "joined": joined, "joined_window_complete": today >= day + timedelta(days=JOIN_WINDOW_DAYS),
        "registered_4plus_days_before": sum(1 for d in timing if d is not None and d >= 4),
        "registered_last_3_days": sum(1 for d in timing if d is not None and 0 <= d <= 3),
        "registered_after_class": sum(1 for d in timing if d is not None and d < 0),
        "_registrants": set(emails), "_attendees": {e for e, (a, _) in emails.items() if a},
    }


def funnel(day, path=None):
    path = Path(path or social_growth_dir() / "habit_funnels.json")
    try:
        return json.loads(path.read_text())["months"].get(f"{day:%Y-%m}") or {}
    except (OSError, ValueError, KeyError):
        return {}


EMAIL_BEFORE_DAYS, EMAIL_AFTER_DAYS = 14, 3


def journey_names():
    """'Email N' of the Yoga Habit journey -> what it is ('Class recording')."""
    try:
        emails = json.loads(Path(journey_path("yoga_habit")).read_text())["emails"]
    except (OSError, ValueError, KeyError):
        return {}
    return {i + 1: re.sub(r"\s*\(.*\)$", "", e.get("subject") or "").strip() for i, e in enumerate(emails)}


def email_label(name, names=None):
    names = journey_names() if names is None else names
    m = re.match(r"Yoga Habit: Email (\d+)(.*)$", name)
    if m and names.get(int(m.group(1))):
        return names[int(m.group(1))] + m.group(2)
    return name
LIST_SEND = 300  # a send delivered to at least this many went to the list, not a small segment


def email_stats(day, store=None):
    """The emails around a Habit class: every send from two weeks before to
    three days after, delivered to someone, except the Yoga Lifestyle
    newsletter. Chosen by date because the names have changed over time
    (September's were 'Transitions'). Returns (emails, complete): complete is
    False when the window starts before the event store's first record
    (2026-09-09), so earlier months read 'n/a' rather than a false 0."""
    if store is None:
        secret = os.getenv("SENDGRID_EVENT_DIGEST_SECRET", "")
        if not secret:
            return [], False
        sys.path.insert(0, str(twy_root() / "yoga-habit"))
        from sendgrid_event_store import EventStore
        store = EventStore(sendgrid_event_db_path(), secret.encode("utf-8"))
    lo = datetime.combine(day - timedelta(days=EMAIL_BEFORE_DAYS), datetime.min.time(), MT)
    hi = datetime.combine(day + timedelta(days=EMAIL_AFTER_DAYS + 1), datetime.min.time(), MT)
    campaigns = store.campaigns()
    first_record = min((c["first_at"] for c in campaigns), default=None)
    out = []
    for c in campaigns:
        name = c.get("campaign_name") or ""
        sent = datetime.fromtimestamp(c["first_at"], timezone.utc).astimezone(MT)
        if not lo <= sent < hi or "Yoga Lifestyle" in name:
            continue
        m = store.campaign_metrics(c["campaign_id"])
        # Habit sends, or a big send to the list (September's Transitions
        # campaign); small unrelated ones such as an Integration reminder are out.
        if not m.delivered or ("Habit" not in name and m.delivered < LIST_SEND):
            continue
        out.append({"email": email_label(re.sub(r"^\d{4}_\d{2}:\s*", "", name)), "sent": f"{sent:%Y-%m-%d %H:%M}",
                    "delivered": m.delivered, "opens": m.unique_conventional_opens, "clicks": m.unique_clicks})
    complete = first_record is not None and first_record <= lo.timestamp()
    return sorted(out, key=lambda e: e["sent"]), complete


# ---------------------------------------------------------------------------
# Message
# ---------------------------------------------------------------------------
# Short on purpose (JP 2026-10-14, "that is waaaaayyyyyy tooooo much"): the
# other facts still go to Claude for the two lines of analysis.
ROWS = [
    ("Page visits", "page_visits"),
    ("Registered", "registered"),
    ("  non-members", "nonmember_registered"),
    ("Came live", "attended"),
    ("  non-members", "nonmember_attended"),
    ("Took the recording", "claimed"),
]


def table(classes):
    head = [""] + [datetime.fromisoformat(c["date"]).strftime("%b %d") for c in classes]
    body = [[label] + ["n/a" if c.get(key) is None else str(c[key]) for c in classes] for label, key in ROWS]
    joined = ["Joined TYL"]
    for c in classes:
        n = str(len(c["joined"]))
        joined.append(n if c["joined_window_complete"] else n + " so far")
    body.append(joined)
    width = [max(len(r[i]) for r in [head] + body) for i in range(len(head))]
    fmt = lambda r: "  ".join(cell.ljust(width[i]) if i == 0 else cell.rjust(width[i]) for i, cell in enumerate(r))
    return "```\n" + "\n".join(fmt(r) for r in [head] + body) + "\n```"


WHY_SYSTEM = """You write the analysis part of a monthly report for Tiffany Wood, a yoga teacher, about her monthly free
Yoga Habit class. You get the numbers for her latest class and the two before it, the month's landing
page visits and email results, and the class dates. Say what most likely moved the numbers, the biggest
change first, using only the facts given plus the calendar around each class date (a holiday or long
weekend, a school break, a season), naming a holiday by its name (the October federal holiday is Indigenous Peoples' Day). "Claimed" means someone took the free class recording, which
registrants get for two weeks. When something is your read rather than a number, say so plainly
("my read is"). Compare with the earlier months throughout, using the numbers. A value of null means
the number was not recorded for that month (email records start 2026-09-09), not zero: never compare
against it. A month's list may be missing its first emails for the same reason, so compare an email only
with the same email in another month, never with a different one.

Write exactly three bullet lines starting with "\u2022 ", each one sentence of at most 25 words:
1. the biggest change and its most likely cause
2. the second thing worth knowing
3. one concrete thing to try for the next class, tied to the numbers. The next class date is
already set, given as next_habit_class: do not propose a different date, say what to watch for around it.
Read this_months_invitation_text before suggesting copy and do not suggest something it already says.

Plain words, no jargon, warm and direct. No headings. No dashes as punctuation, no semicolons, nothing
before the first bullet or after the last."""


def why(facts, client=None):
    """Claude's lines on why the numbers moved; '' when Claude cannot be reached."""
    try:
        if client is None:
            import anthropic
            client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
        resp = client.messages.create(model=MODEL, max_tokens=16000, system=WHY_SYSTEM,
                                      messages=[{"role": "user", "content": json.dumps(facts, indent=1)}])
        if getattr(resp, "stop_reason", None) == "refusal":
            return ""
        text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
    except Exception as exc:
        print(f"why: Claude not reached ({exc})", file=sys.stderr)
        return ""
    return clean(text)


def clean(text):
    """TWY copy carries no em or en dashes and no semicolons."""
    text = re.sub(r"\s*[\u2014\u2013]\s*", ", ", text)
    return text.replace(";", ",").strip()


def message(latest, classes, emails, why_text):
    lines = [f"*Yoga Habit, one week on: {latest['name']}*",
             table(classes)]
    if why_text:
        lines.append(why_text)
    return "\n\n".join(line if line.startswith("```") else clean(line) for line in lines)


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------
def next_class(after, path=None):
    """The next scheduled Habit date, already set in HeyMarvelous, or None."""
    path = Path(path or twy_root() / "announce" / "data" / "marvelous_events.json")
    try:
        events = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    days = sorted(datetime.fromisoformat(e["event_start_datetime"].replace("Z", "+00:00")).astimezone(MT).date()
                  for e in events if "Habit" in (e.get("event_name") or "") and e.get("event_start_datetime"))
    later = [d for d in days if d > after]
    return f"{later[0]:%A %Y-%m-%d}" if later else None


def invitation_text(day):
    """The month's invitation as readers saw it, so suggestions build on it."""
    try:
        return newsletter_path(day.year, day.month, "non_lifestyle").read_text()[:2500]
    except OSError:
        return None


def state_path():
    return data_root() / "habit_report" / "state.json"


def load_state(path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {"posts": {}}


def save_state(path, state):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True))
    tmp.replace(path)


def build(db, event, today, client=None, store=None):
    events = habit_events(db)
    idx = [e[0] for e in events].index(event[0])
    chosen = events[max(0, idx - 2): idx + 1]
    earlier_regs = set()
    for eid, _, d in events[:max(0, idx - 2)]:
        earlier_regs |= {r[0] for r in db.execute(
            "SELECT lower(coalesce(nullif(a.student_email, ''), cu.email)) FROM attendance a "
            "LEFT JOIN customers cu ON cu.id = a.customer_id WHERE a.event_id = ?", (eid,))}
    classes, emails_by_class = [], []
    for eid, name, day in chosen:
        c = class_stats(db, eid, name, day, members_on(day), today)
        f = funnel(day)
        e, complete = email_stats(day, store)
        c["page_visits"], c["register_clicks"] = f.get("landing_page_visits"), f.get("registration_clicks")
        big = [x for x in e if x["delivered"] >= LIST_SEND]
        c["list_sends"] = len(big) if complete else None
        c["list_clicks"] = sum(x["clicks"] for x in big) if complete else None
        c["email_record_complete"] = complete
        c["returning_registrants"] = len(c["_registrants"] & earlier_regs)
        earlier_regs |= c["_registrants"]
        classes.append(c)
        emails_by_class.append(e)
    facts = {"classes": [{k: v for k, v in c.items() if not k.startswith("_")} for c in classes],
             "emails_by_class_month": {c["date"][:7]: e for c, e in zip(classes, emails_by_class)},
             "next_habit_class": next_class(event[2]),
             "this_months_invitation_text": invitation_text(event[2])}
    return message(classes[-1], classes, emails_by_class[-1], why(facts, client))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Yoga Habit report one week after the class")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--event", type=int)
    args = ap.parse_args(argv)
    load_env()
    import member_outreach as mo
    today = datetime.now(MT).date()
    db = sqlite3.connect(f"file:{marvy_db_path()}?mode=ro", uri=True)
    events = habit_events(db)
    if args.event:
        due = [e for e in events if e[0] == args.event]
    else:
        due = [e for e in events if e[2] == today - timedelta(days=REPORT_AFTER_DAYS)]
    if not due:
        print("no Habit class seven days ago")
        return 0
    event = due[-1]
    path = state_path()
    state = load_state(path)
    key = str(event[0])
    posted = state["posts"].get(key, {})
    if not args.dry_run and posted.get("forwarded_ts"):
        print(f"already posted and forwarded for {key}")
        return 0
    if args.dry_run or not posted.get("ts"):
        text = build(db, event, today)
        if args.dry_run:
            print(text)
            return 0
        ts = mo.post_message(os.getenv("SLACK_BOT_TOKEN"), mo.CHECKIN_CHANNEL, text)
        if not ts:
            return 1
        posted = state["posts"][key] = {"channel": mo.CHECKIN_CHANNEL, "ts": ts}
        save_state(path, state)
    dm_ts = mo.post_message(os.getenv("SLACK_JP_POST_TOKEN"), mo.TIFF_DM_CHANNEL,
                            mo.permalink(posted["channel"], posted["ts"]))
    if not dm_ts:
        return 1  # the next run forwards it, the post is never repeated
    posted["forwarded_ts"] = dm_ts
    save_state(path, state)
    print(f"posted {posted['ts']} and forwarded {dm_ts}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
