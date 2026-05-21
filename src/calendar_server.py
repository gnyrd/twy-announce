"""
TWY classes ICS feed.

Publishes upcoming HM events as a subscribable iCalendar feed.
Routes:
  GET /            -> branded subscribe page
  GET /classes.ics -> RFC 5545 VCALENDAR of upcoming non-cancelled events

Read-only against /root/twy/data/marvy.db via twy_paths.marvy_db_path().
Served on 127.0.0.1:5012; nginx vhost proxies calendar.tiffanywoodyoga.com.
"""

import re
import sqlite3
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from flask import Flask, Response
from twy_classplan.plans import load_plans_for_month
from twy_paths import marvy_db_path

app = Flask(__name__)

TZ_MT = ZoneInfo("America/Denver")
ICS_TZID = "America/Denver"
CAL_NAME = "Tiffany Wood Yoga - Classes"
CAL_DESC = "Upcoming live classes with Tiffany Wood."
UID_HOST = "tiffanywoodyoga.com"
PRODID = "-//Tiffany Wood Yoga//Classes Feed v1//EN"

LOGO_URL = "https://mcusercontent.com/a6369901d6f0c448fbcc61e6e/images/504db4b6-e9bc-18e5-3142-9a9bcbbbe892.png"
WEBCAL_URL = "webcal://calendar.tiffanywoodyoga.com/classes.ics"
HTTPS_URL = "https://calendar.tiffanywoodyoga.com/classes.ics"
# Per-event detail page on Tiff's HM studio (custom domain). Same base used by
# /root/twy/announce/scripts/send_class_email_reminders.py (MARVELOUS_JOIN_BASE_URL).
STUDIO_EVENT_URL = "https://studio.tiffanywoodyoga.com/event/details"


def _esc(text):
    if text is None:
        return ""
    return (
        text.replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\n", "\\n")
        .replace("\r", "")
    )


def _fold(line):
    if len(line) <= 75:
        return line
    out = [line[:75]]
    rest = line[75:]
    while rest:
        out.append(" " + rest[:74])
        rest = rest[74:]
    return "\r\n".join(out)


def _utc_to_ics(iso_z):
    dt = datetime.fromisoformat(iso_z.replace("Z", "+00:00"))
    return dt.strftime("%Y%m%dT%H%M%SZ")


DATE_KEY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _iter_published_plans():
    """Only classes with published class plans are published on the class calendars.

    Yield (date_str, plan) for plans that are BOTH:
      (a) live on Marvelous: `marvelous_event_id` is set, AND
      (b) finalized by Tiff:  `published` is True.

    Both gates are required. Each gate alone has known false positives:

    - `mev set` alone includes auto-generated placeholders. The hm_placeholder_topup
      and create_next_habit_event scripts create HM events for upcoming classes and
      patch only the mev onto the plan, leaving published=False until Tiff fills in
      real content. Those placeholders carry titles like "Build the Center - Yoga
      Habit" and should NOT appear on the public calendar feed.

    - `published=True` alone can include plans whose publish-to-HM step never
      completed (sync error, ambiguous candidates). Example: 2026-02-28
      "Radiant Balance" — published=True, mev empty. No real class on HM,
      shouldn't be on the feed.

    Also skips draft `* copy.json` siblings (keys not matching YYYY-MM-DD) and
    de-dupes if two plans share an mev (first-seen wins).
    """
    today = datetime.now(timezone.utc).date()
    months = sorted({(d.year, d.month) for d in (today, today + timedelta(days=31), today + timedelta(days=62))})
    seen = set()
    for year, month in months:
        for date_str, plan in sorted(load_plans_for_month(year, month).items()):
            if not DATE_KEY_RE.match(date_str):
                continue
            if plan.get("published") is not True:
                continue
            mev = plan.get("marvelous_event_id")
            if not mev:
                continue
            try:
                mev_int = int(mev)
            except (TypeError, ValueError):
                continue
            if mev_int in seen:
                continue
            seen.add(mev_int)
            yield date_str, plan


def _build_event_index():
    """Index marvy.db events by id (id -> row)."""
    conn = sqlite3.connect(str(marvy_db_path()))
    try:
        rows = conn.execute(
            """SELECT id, event_name, event_start_datetime, event_end_datetime,
                      is_cancelled, instructors_string, synced_at
               FROM events"""
        ).fetchall()
    finally:
        conn.close()
    return {r[0]: r for r in rows}


def _plan_to_utc(date_str, plan):
    """Return UTC start/end datetimes derived from plan.date + plan.time + plan.duration."""
    time_str = (plan.get("time") or "00:00")[:5]  # 'HH:MM'
    local = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M").replace(tzinfo=TZ_MT)
    start_utc = local.astimezone(timezone.utc)
    end_utc = start_utc + timedelta(minutes=int(plan.get("duration") or 60))
    return start_utc, end_utc


def _description_for(plan, instructors, register_url=None):
    # `instructors` intentionally unused: it's always Tiffany on TWY's calendar.
    # Param kept to surface a guest-teacher line later if the data ever differs.
    del instructors
    parts = []
    if plan:
        if plan.get("description"):
            parts.append(plan["description"])
        if plan.get("props"):
            parts.append(f"Bring: {plan['props']}")
    if register_url:
        # Surfaced visibly in description; also emitted as VEVENT.URL so
        # clients that surface URL natively (Apple, Outlook) have a tap target.
        # Verb "Join" matches existing TWY pattern in send_class_email_reminders.py
        # ("Link to Join:" against the same {MARVELOUS_JOIN_BASE_URL}/{ev_id}).
        parts.append(f"Join: {register_url}")
    return "\n\n".join(parts)


def _build_ics(class_type_filter=None, cal_name=None, cal_desc=None):
    """Build VCALENDAR. Optional class_type_filter limits to plans whose
    plan.class_type matches exactly (e.g. "Habit" for the Habit-only feed).
    """
    now_utc = datetime.now(timezone.utc)
    now_stamp = now_utc.strftime("%Y%m%dT%H%M%SZ")
    event_index = _build_event_index()

    name = cal_name or CAL_NAME
    desc = cal_desc or CAL_DESC

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        f"PRODID:{PRODID}",
        "METHOD:PUBLISH",
        "CALSCALE:GREGORIAN",
        f"X-WR-CALNAME:{_esc(name)}",
        f"X-WR-CALDESC:{_esc(desc)}",
        f"X-WR-TIMEZONE:{ICS_TZID}",
        "X-PUBLISHED-TTL:PT1H",
        "REFRESH-INTERVAL;VALUE=DURATION:PT1H",
    ]

    for date_str, plan in _iter_published_plans():
        if class_type_filter and (plan.get("class_type") or "").strip() != class_type_filter:
            continue
        # Resolve matching marvy event (if any) by marvelous_event_id
        try:
            mev_int = int(plan["marvelous_event_id"]) if plan.get("marvelous_event_id") else None
        except (TypeError, ValueError):
            mev_int = None
        event = event_index.get(mev_int) if mev_int else None

        if event:
            event_id, event_name, start_z, end_z, is_cancelled, instructors, synced_at = event
            start_dt = datetime.fromisoformat(start_z.replace("Z", "+00:00"))
            end_dt = datetime.fromisoformat(end_z.replace("Z", "+00:00")) if end_z else start_dt
            status = "CANCELLED" if is_cancelled else "CONFIRMED"
            summary = event_name
            uid = f"heymarvelous-event-{event_id}@{UID_HOST}"
        else:
            start_dt, end_dt = _plan_to_utc(date_str, plan)
            status = "CONFIRMED"
            class_type = (plan.get("class_type") or "").strip()
            title = plan.get("title") or "Class"
            summary = f"{class_type}: {title}" if class_type else title
            instructors = "Tiffany Wood"
            synced_at = None
            uid = f"twy-plan-{plan['id']}@{UID_HOST}"

        # Keep classes visible for 74h after start (HM recordings live ~3 days).
        if start_dt < now_utc - timedelta(hours=74):
            continue

        register_url = f"{STUDIO_EVENT_URL}/{mev_int}" if mev_int else None
        description = _description_for(plan, instructors, register_url)

        lines.append("BEGIN:VEVENT")
        lines.append(f"UID:{uid}")
        lines.append(f"DTSTAMP:{now_stamp}")
        lines.append(f"DTSTART:{start_dt.strftime('%Y%m%dT%H%M%SZ')}")
        lines.append(f"DTEND:{end_dt.strftime('%Y%m%dT%H%M%SZ')}")
        lines.append(f"SUMMARY:{_esc(summary)}")
        if description:
            lines.append(f"DESCRIPTION:{_esc(description)}")
        if register_url:
            lines.append(f"URL:{register_url}")
        lines.append(f"STATUS:{status}")
        if synced_at:
            mod_dt = datetime.fromisoformat(synced_at.replace("Z", "+00:00"))
            mod_stamp = mod_dt.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            lines.append(f"LAST-MODIFIED:{mod_stamp}")
        lines.append("END:VEVENT")

    lines.append("END:VCALENDAR")
    return "\r\n".join(_fold(line) for line in lines) + "\r\n"


SUBSCRIBE_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>__PAGE_TITLE__</title>
  <link rel="stylesheet" href="https://assets.tiffanywoodyoga.com/twy-brand.css">
  <script src="https://assets.tiffanywoodyoga.com/twy-footer.js" defer></script>
</head>
<body>
<div class="outer">
<div class="card rounded">

  <div class="logo-row">
    <a href="https://tiffanywoodyoga.com">
      <img src="__LOGO_URL__" alt="Tiffany Wood Yoga" width="100" />
    </a>
  </div>

  <div class="content">
    <h1>__H1__</h1>
    <p class="lead">__LEAD__</p>
    <p>__NOTE__</p>

    <div class="cta-block">
      <a class="btn btn-primary" href="__WEBCAL_URL__">Add to my calendar</a>
    </div>

    <div class="details">
      <div class="label">Calendar URL</div>
      <p><code>__HTTPS_URL__</code></p>
    </div>
  </div>

  <hr class="divider">

  <div class="about">
    <h2>How to subscribe</h2>
    <p><strong>Apple Calendar (Mac, iPhone, iPad):</strong> Tap the button above. Your device offers to add the calendar.</p>
    <p><strong>Google Calendar:</strong> In "Other calendars" choose "From URL" and paste the calendar URL.</p>
    <p><strong>Outlook:</strong> Choose "Add calendar" then "Subscribe from web". Paste the calendar URL.</p>
  </div>


</div>
<twy-footer></twy-footer>
</div>
</body>
</html>
"""

def _render_subscribe(page_title, h1, lead, note, webcal_url, https_url):
    return (
        SUBSCRIBE_HTML_TEMPLATE
        .replace("__PAGE_TITLE__", page_title)
        .replace("__H1__", h1)
        .replace("__LEAD__", lead)
        .replace("__NOTE__", note)
        .replace("__LOGO_URL__", LOGO_URL)
        .replace("__WEBCAL_URL__", webcal_url)
        .replace("__HTTPS_URL__", https_url)
    )


@app.route("/classes.ics")
def classes_ics():
    return Response(
        _build_ics(),
        mimetype="text/calendar",
        headers={
            "Cache-Control": "public, max-age=3600",
            "Content-Disposition": 'inline; filename="classes.ics"',
        },
    )


@app.route("/classes/")
def classes_subscribe():
    return Response(
        _render_subscribe(
            page_title="Class Calendar - Tiffany Wood Yoga",
            h1="Tiffany Wood Yoga<br>Class Calendar",
            lead="Put Tiffany's class schedule on the calendar you already use. New classes appear. Cancellations slip away.",
            note="Live classes only, in your local timezone. Updated regularly.",
            webcal_url="webcal://calendar.tiffanywoodyoga.com/classes.ics",
            https_url="https://calendar.tiffanywoodyoga.com/classes.ics",
        ),
        mimetype="text/html",
    )


@app.route("/habit.ics")
def habit_ics():
    return Response(
        _build_ics(
            class_type_filter="Habit",
            cal_name="Tiffany Wood Yoga - Yoga Habit",
            cal_desc="Upcoming free Yoga Habit classes with Tiffany Wood. Open to everyone.",
        ),
        mimetype="text/calendar",
        headers={
            "Cache-Control": "public, max-age=3600",
            "Content-Disposition": 'inline; filename="habit.ics"',
        },
    )


@app.route("/habit/")
def habit_subscribe():
    return Response(
        _render_subscribe(
            page_title="Yoga Habit Calendar - Tiffany Wood Yoga",
            h1="Tiffany Wood Yoga<br>The Yoga Habit Calendar",
            lead="Tiffany's free monthly Yoga Habit class on the calendar you already use. Open to everyone.",
            note="On your calendar, in your local timezone. Always up to date.",
            webcal_url="webcal://calendar.tiffanywoodyoga.com/habit.ics",
            https_url="https://calendar.tiffanywoodyoga.com/habit.ics",
        ),
        mimetype="text/html",
    )


@app.route("/")
def root():
    return Response(status=302, headers={"Location": "/classes/"})


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5012, debug=False, use_reloader=True)
