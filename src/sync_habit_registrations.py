#!/usr/bin/env python3
"""
Sync HeyMarvelous Habit-class registrations to MailChimp tags.

For each upcoming Habit event (within REGISTRATION_WINDOW_DAYS), reads the
event's registrations[] from HM via marvy and applies the MC tag
'Habit Registered - YYYY-MM' to each registrant's email. Removes the tag
from anyone who unregistered.

Idempotent. Run daily during the registration window. Used by the day-before
'reminder' MC campaign segment.
"""
import os
import sys
import calendar
import hashlib
import logging
from datetime import date, datetime, timedelta
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "paths"))
from twy_paths import load_env

load_env()

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "marvy"))
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "classes" / "scripts"))
from sync import get_token  # reuses the existing HM auth flow
from marvy.client import Client

# --- config ---
LIST_ID = os.environ["MAILCHIMP_AUDIENCE_ID"]
MC_API_KEY = os.environ["MAILCHIMP_API_KEY"]
MC_SERVER = os.environ["MAILCHIMP_SERVER_PREFIX"]
CLASSES_API = "http://localhost:5003"
REGISTRATION_WINDOW_DAYS = 35  # how far ahead to look for Habit classes
TAG_TEMPLATE = "Habit Registered - {year:04d}-{month:02d}"

logging.basicConfig(format="%(asctime)s %(levelname)s %(message)s", level=logging.INFO)
log = logging.getLogger("sync_habit_registrations")


def _mc_url(path):
    return f"https://{MC_SERVER}.api.mailchimp.com/3.0{path}"


def _mc_auth():
    return ("any", MC_API_KEY)


def _email_hash(email):
    return hashlib.md5(email.lower().encode()).hexdigest()


def upcoming_habit_events(today):
    """Find Habit events scheduled within REGISTRATION_WINDOW_DAYS."""
    out = []
    for offset in range(2):  # this month and next
        y = today.year
        m = today.month + offset
        if m > 12:
            m -= 12
            y += 1
        last = calendar.monthrange(y, m)[1]
        try:
            r = requests.get(
                f"{CLASSES_API}/api/plans",
                params={"from": f"{y:04d}-{m:02d}-01", "to": f"{y:04d}-{m:02d}-{last:02d}"},
                timeout=10,
            )
            if not r.ok:
                continue
            for plan in r.json():
                if plan.get("class_type") != "Habit":
                    continue
                event_id = plan.get("marvelous_event_id")
                if not event_id:
                    continue
                try:
                    plan_date = date.fromisoformat(plan["date"])
                except (KeyError, ValueError):
                    continue
                days_out = (plan_date - today).days
                if 0 <= days_out <= REGISTRATION_WINDOW_DAYS:
                    out.append((plan_date, event_id))
        except requests.RequestException as e:
            log.warning("classes API unreachable for %s-%02d: %s", y, m, e)
    return out


def get_registrant_emails(client, event_id):
    """Return set of lowercase emails registered for the event."""
    event = client.get_event(event_id)
    emails = set()
    for reg in event.get("registrations", []) or []:
        email = (reg.get("student_email") or
                 (reg.get("student") or {}).get("email") or "")
        email = email.strip().lower()
        if email:
            emails.add(email)
    return emails


def members_with_tag(tag_name):
    """Return set of lowercase emails currently tagged with tag_name in MC."""
    out = set()
    offset = 0
    while True:
        r = requests.get(
            _mc_url(f"/lists/{LIST_ID}/segments"),
            auth=_mc_auth(),
            params={"count": 1000, "offset": offset, "type": "static"},
            timeout=30,
        )
        r.raise_for_status()
        segs = r.json().get("segments", []) or []
        match = [s for s in segs if s.get("name") == tag_name]
        if match:
            seg_id = match[0]["id"]
            mem_offset = 0
            while True:
                rm = requests.get(
                    _mc_url(f"/lists/{LIST_ID}/segments/{seg_id}/members"),
                    auth=_mc_auth(),
                    params={"count": 1000, "offset": mem_offset},
                    timeout=30,
                )
                rm.raise_for_status()
                members = rm.json().get("members", []) or []
                out.update(m["email_address"].strip().lower() for m in members)
                if len(members) < 1000:
                    break
                mem_offset += 1000
            return out
        if len(segs) < 1000:
            return out  # tag doesn't exist yet
        offset += 1000


def apply_tag(email, tag_name, active):
    """Apply or remove tag_name on the member for email."""
    h = _email_hash(email)
    payload = {"tags": [{"name": tag_name, "status": "active" if active else "inactive"}]}
    r = requests.post(
        _mc_url(f"/lists/{LIST_ID}/members/{h}/tags"),
        auth=_mc_auth(),
        json=payload,
        timeout=15,
    )
    if r.status_code == 404:
        log.info("  skip %s (not in list)", email)
        return False
    if not r.ok:
        log.warning("  tag op failed %s %s: %s", email, r.status_code, r.text[:200])
        return False
    return True


def sync_event(client, event_date, event_id):
    tag_name = TAG_TEMPLATE.format(year=event_date.year, month=event_date.month)
    log.info("event %s (%s) -> tag '%s'", event_id, event_date, tag_name)

    registrants = get_registrant_emails(client, event_id)
    tagged = members_with_tag(tag_name)

    to_add = registrants - tagged
    to_remove = tagged - registrants

    log.info("  registrants=%d tagged=%d add=%d remove=%d",
             len(registrants), len(tagged), len(to_add), len(to_remove))

    add_ok = sum(1 for e in to_add if apply_tag(e, tag_name, True))
    rem_ok = sum(1 for e in to_remove if apply_tag(e, tag_name, False))
    log.info("  applied: +%d / -%d", add_ok, rem_ok)
    return {"event_id": event_id, "tag": tag_name, "added": add_ok, "removed": rem_ok}


def main():
    today = datetime.now().date()
    events = upcoming_habit_events(today)
    if not events:
        log.info("no upcoming Habit events within %d days", REGISTRATION_WINDOW_DAYS)
        return
    client = Client(auth_token=get_token())
    for event_date, event_id in events:
        try:
            sync_event(client, event_date, event_id)
        except Exception as e:
            log.error("sync failed for event %s: %s", event_id, e)


if __name__ == "__main__":
    main()
