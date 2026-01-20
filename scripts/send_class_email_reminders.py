#!/usr/bin/env python3
"""Send class reminder emails based on a Google Doc schedule.

This script is designed to run non-interactively on the Hetzner host.
It will:

1. Fetch a Google Doc (the class plan) as plain text using an existing
   OAuth token created previously on another machine.
2. Parse class entries (by date) from the document.
3. For each class, compute reminder times at T-26h, T-25h, and T-24h
   based on the Salt Lake City timezone (America/Denver).
4. Send reminder emails when a reminder window is reached.
5. Track sent reminders in a local JSON file to ensure each reminder
   is only sent once.

Environment/configuration:
- GOOGLE_DOC_ID                (required) Google Doc ID for the class plan
- TIMEZONE                     (default: America/Denver)
- REMINDER_OFFSETS             (default: "26,25,24")
- REMINDER_STATE_PATH          (default: ./data/reminder_state.json)
- EMAIL_FROM                   (optional) From header; defaults to Gmail account if omitted
- EMAIL_TO                     (required) comma-separated list of recipients

Google credentials:
- Expects an existing token at ~/.config/twy-google-sheets-token.pickle
  created via a separate, interactive auth flow (see twy-growth-blitz-2025
  scripts). This script will only refresh that token; it will not open
  a browser or prompt for input.
"""

from __future__ import annotations

import argparse
import json
import os
import smtplib
import sys
from dataclasses import dataclass
import base64
import requests
from datetime import date, datetime, time, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path
from typing import Dict, List, Tuple

from dateutil import parser as date_parser
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
import pickle
from zoneinfo import ZoneInfo

GOOGLE_TOKEN_PATH = os.path.expanduser("~/.config/twy-google-sheets-token.pickle")
GMAIL_TOKEN_PATH = os.path.expanduser("~/.config/twy-gmail-token.pickle")


@dataclass
class ClassEntry:
    id: str
    title: str
    series: str | None
    class_date: date
    start_dt: datetime  # timezone-aware
    description: str | None = None
    affirmation: str | None = None
    key_actions: str | None = None
    class_focus: str | None = None
    categories: str | None = None


def load_credentials():
    """Load stored OAuth credentials and refresh if needed.

    This is intentionally non-interactive: if the token is missing or
    cannot be refreshed, we exit with an explanatory message.
    """

    if not os.path.exists(GOOGLE_TOKEN_PATH):
        print(f"❌ Google token not found at {GOOGLE_TOKEN_PATH}", file=sys.stderr)
        print("   Run the interactive auth script on another machine and copy the token here.", file=sys.stderr)
        sys.exit(1)

    with open(GOOGLE_TOKEN_PATH, "rb") as f:
        creds = pickle.load(f)

    if not getattr(creds, "valid", False):
        if getattr(creds, "expired", False) and getattr(creds, "refresh_token", None):
            print("🔄 Refreshing Google OAuth token...", file=sys.stderr)
            creds.refresh(Request())
            with open(GOOGLE_TOKEN_PATH, "wb") as f:
                pickle.dump(creds, f)
        else:
            print("❌ Stored Google token is invalid and cannot be refreshed.", file=sys.stderr)
            sys.exit(1)

    return creds


def load_gmail_credentials():
    """Load stored Gmail OAuth credentials and refresh if needed.

    Uses a separate token file so Drive/Sheets tooling can remain independent.
    """
    if not os.path.exists(GMAIL_TOKEN_PATH):
        print(f"❌ Gmail token not found at {GMAIL_TOKEN_PATH}", file=sys.stderr)
        print("   Run the Gmail auth script on a trusted machine and copy the token here.", file=sys.stderr)
        sys.exit(1)

    with open(GMAIL_TOKEN_PATH, "rb") as f:
        creds = pickle.load(f)

    if not getattr(creds, "valid", False):
        if getattr(creds, "expired", False) and getattr(creds, "refresh_token", None):
            print("🔄 Refreshing Gmail OAuth token...", file=sys.stderr)
            creds.refresh(Request())
            with open(GMAIL_TOKEN_PATH, "wb") as f:
                pickle.dump(creds, f)
        else:
            print("❌ Stored Gmail token is invalid and cannot be refreshed.", file=sys.stderr)
            sys.exit(1)

    return creds


def fetch_doc_text(doc_id: str) -> str:
    creds = load_credentials()
    drive = build("drive", "v3", credentials=creds)

    print(f"📄 Fetching Google Doc {doc_id} as plain text...", file=sys.stderr)
    data = drive.files().export(fileId=doc_id, mimeType="text/plain").execute()
    if isinstance(data, bytes):
        return data.decode("utf-8", errors="replace")
    return str(data)


def weekday_start_time(d: date) -> time:
    """Return the class start time for a given date.

    Rules (America/Denver):
    - Monday    -> 17:30
    - Tuesday   -> 08:00
    - Thursday  -> 08:00
    - Saturday  -> 09:00 (e.g. Sat Jan 31 class at 9am)
    """

    wd = d.weekday()  # Monday=0 ... Sunday=6
    if wd == 0:  # Monday
        return time(hour=17, minute=30)
    if wd == 1:  # Tuesday
        return time(hour=8, minute=0)
    if wd == 3:  # Thursday
        return time(hour=8, minute=0)
    if wd == 5:  # Saturday
        return time(hour=9, minute=0)
    # Default: 8am if we ever see another weekday
    return time(hour=8, minute=0)


def parse_classes(doc_text: str, tz: ZoneInfo) -> List[ClassEntry]:
    """Very simple parser for the class plan Google Doc.

    This expects sections that look roughly like the sample in
    docs/references/class_source_sample.md, e.g.:

        Thursday, Jan 15 — Expanding Potential
        Class Title: Standing Backbend
        ...
        Original Class Date: January 15, 2026

    We prioritize the explicit "Original Class Date" line when present.
    Otherwise we parse the heading line for a date.
    """

    lines = [ln.rstrip() for ln in doc_text.splitlines()]

    entries: List[ClassEntry] = []
    current_block: List[str] = []

    def flush_block(block: List[str]):
        if not block:
            return
        entry = parse_block(block, tz)
        if entry is not None:
            entries.append(entry)

    for ln in lines:
        if ln.strip().startswith("###") or ln.strip().startswith("Thursday") or ln.strip().startswith("Monday") or ln.strip().startswith("Tuesday") or ln.strip().startswith("Saturday"):
            # Heuristic: heading for a new class
            flush_block(current_block)
            current_block = [ln]
        else:
            current_block.append(ln)

    flush_block(current_block)
    return entries


def parse_block(block: List[str], tz: ZoneInfo) -> ClassEntry | None:
    """Parse a single class block into a ClassEntry.

    Best-effort; if we can't find a date, we skip the block.
    """

    text = "\n".join(block)

    # Try to find "Original Class Date:" line first
    cls_date: date | None = None
    for ln in block:
        if "Original Class Date:" in ln:
            _, _, tail = ln.partition("Original Class Date:")
            tail = tail.strip()
            try:
                dt = date_parser.parse(tail, fuzzy=True).date()
                cls_date = dt
                break
            except Exception:
                pass

    # Fallback: parse date from first line
    if cls_date is None and block:
        try:
            dt = date_parser.parse(block[0], fuzzy=True).date()
            cls_date = dt
        except Exception:
            pass

    if cls_date is None:
        # Nothing we can do
        return None

    # Series/name from heading line, e.g. "Thursday, Jan 15 — Expanding Potential"
    heading = block[0].strip().lstrip("# ").strip("*")
    series: str | None = None
    if "—" in heading:
        _, _, after = heading.partition("—")
        series = after.strip()

    # Class title
    title = series or "Class"
    for ln in block:
        if "Class Title:" in ln:
            _, _, tail = ln.partition("Class Title:")
            tail = tail.strip()
            if tail:
                title = tail
            break

    start_time = weekday_start_time(cls_date)
    start_dt = datetime.combine(cls_date, start_time, tzinfo=tz)

    entry_id = f"{cls_date.isoformat()}::{title}"

    # Extract rich text fields from block
    def norm_label(s: str) -> str:
        return s.strip().strip('*').rstrip(':').lower()

    description = None
    affirmation = None
    key_actions = None
    class_focus = None
    categories = None

    labels = {
        'description': 'description',
        'affirmation of the class': 'affirmation',
        'key actions': 'key_actions',
        'class focus': 'class_focus',
        'categories': 'categories',
    }

    n = len(block)
    i = 0
    while i < n:
        label_key = labels.get(norm_label(block[i]), None)
        if not label_key:
            i += 1
            continue
        j = i + 1
        collected: list[str] = []
        while j < n:
            line = block[j].strip()
            if not line:
                break
            # Stop if we hit another known label
            if norm_label(block[j]) in labels:
                break
            # Stop if we hit other section headers we do NOT want folded into this field
            ln = line.lower()
            if ln.startswith('required item(') or ln.startswith('required item(s):') or                ln.startswith('original class date:') or ln.startswith("tiff's notes:") or                set(line) == {'-'}:
                break
            collected.append(block[j].rstrip())
            j += 1
        value = ' '.join(l.strip() for l in collected).strip() or None
        if label_key == 'description':
            description = value
        elif label_key == 'affirmation':
            affirmation = value
        elif label_key == 'key_actions':
            key_actions = value
        elif label_key == 'class_focus':
            class_focus = value
        elif label_key == 'categories':
            categories = value
        i = j

    return ClassEntry(
        id=entry_id,
        title=title,
        series=series,
        class_date=cls_date,
        start_dt=start_dt,
        description=description,
        affirmation=affirmation,
        key_actions=key_actions,
        class_focus=class_focus,
        categories=categories,
    )


MARVELOUS_EVENTS_URL = "https://api.namastream.com/api/studios/tiffany-wood-yoga/events"
MARVELOUS_JOIN_BASE_URL = "https://studio.tiffanywoodyoga.com/event/details"


def fetch_marvelous_events() -> list[dict]:
    """Load events from local cache, falling back to live Marvelous API if needed.

    NOTE: Studio slug and URL are hard-coded for Tiffany Wood Yoga.
    If the studio URL changes, update MARVELOUS_EVENTS_URL and MARVELOUS_JOIN_BASE_URL.
    """
    cache_path = Path(os.environ.get("MARVELOUS_EVENTS_PATH", "./data/marvelous_events.json"))

    # Prefer locally cached snapshot
    try:
        if cache_path.exists():
            with cache_path.open("r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                return data
    except Exception as e:  # pragma: no cover - corrupt cache
        print(f"⚠️ Failed to read Marvelous cache {cache_path}: {e}", file=sys.stderr)

    # Fallback: hit live API directly
    try:
        resp = requests.get(MARVELOUS_EVENTS_URL, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, list):
            return data
        return []
    except Exception as e:  # pragma: no cover - network failures
        print(f"⚠️ Failed to fetch Marvelous events: {e}", file=sys.stderr)
        return []


def find_marvelous_event_for_class(cls: ClassEntry, events: list[dict]) -> str | None:
    """Return a join URL for this class, if we can match one safely.

    Matching strategy:
    - Compare start times in UTC within a small tolerance window.
    - If multiple events match in time, prefer title-equal, then title-substring match.
    - On ambiguity or no match, return None. Caller can fall back to calendar URL.
    """
    if not events:
        return None

    target_utc = cls.start_dt.astimezone(timezone.utc)
    tolerance = timedelta(minutes=15)

    candidates: list[tuple[dict, float]] = []
    for ev in events:
        start_raw = ev.get("event_start_datetime")
        if not start_raw:
            continue
        try:
            ev_start = date_parser.parse(start_raw)
        except Exception:
            continue
        if not ev_start.tzinfo:
            ev_start = ev_start.replace(tzinfo=timezone.utc)
        diff = abs((ev_start - target_utc).total_seconds())
        if diff <= tolerance.total_seconds():
            candidates.append((ev, diff))

    if not candidates:
        return None

    # Prefer best title match among time-nearby candidates
    def score(ev: dict) -> tuple[int, int, float]:
        name = (ev.get("event_name") or "").strip().casefold()
        title = (cls.title or "").strip().casefold()
        exact = 0 if name == title and name else 1
        substr = 0 if (name and title and (name in title or title in name)) else 1
        start_raw = ev.get("event_start_datetime") or ""
        try:
            ev_start = date_parser.parse(start_raw)
            if not ev_start.tzinfo:
                ev_start = ev_start.replace(tzinfo=timezone.utc)
        except Exception:
            ev_start = target_utc
        diff = abs((ev_start - target_utc).total_seconds())
        return (exact, substr, diff)

    candidates.sort(key=lambda pair: score(pair[0]))
    best_ev, _ = candidates[0]
    ev_id = best_ev.get("id")
    if not ev_id:
        return None
    return f"{MARVELOUS_JOIN_BASE_URL}/{ev_id}"


def load_state(path: Path) -> Dict[str, Dict[str, str]]:
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(path: Path, state: Dict[str, Dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, sort_keys=True)
    tmp.replace(path)


def compute_due_reminders(
    classes: List[ClassEntry],
    offsets: List[int],
    now: datetime,
    state: Dict[str, Dict[str, str]],
    window_minutes: int = 15,
) -> List[Tuple[ClassEntry, int]]:
    """Return list of (class, offset_hours) that should fire now.

    A reminder is "due" if:
    - now is within [send_at, send_at + window]
    - and state does not already contain an entry for (class.id, offset)
    """

    due: List[Tuple[ClassEntry, int]] = []
    window = timedelta(minutes=window_minutes)

    for cls in classes:
        for offset in offsets:
            key = str(offset)
            sent_for_class = state.get(cls.id, {})
            if key in sent_for_class:
                continue

            send_at = cls.start_dt - timedelta(hours=offset)
            if send_at <= now < send_at + window:
                due.append((cls, offset))

    return due


def build_email(cls: ClassEntry, offset: int, tz: ZoneInfo, to_addrs: List[str], sender: str, join_url: str | None) -> EmailMessage:
    """Build an EmailMessage whose core is a copy-pastable WhatsApp block."""
    local_start = cls.start_dt.astimezone(tz)
    date_str = local_start.strftime("%B %d, %Y")
    time_str = local_start.strftime("%-I:%M %p %Z")

    subj = f"WhatsApp reminder (T-{offset}h): {cls.title} on {date_str}"

    header_lines = [
        f"T-{offset}h reminder for a Tiffany Wood Yoga WhatsApp post.",
        "",
        "Copy the WhatsApp message block below into the group:",
        "",
        "—— WhatsApp message ———",
        "",
    ]

    wa_lines: list[str] = []

    # Intro line
    wa_lines.append(f"✨ Join Tiff for class on {date_str} at {time_str}")
    wa_lines.append("")
    wa_lines.append(f"*\"{cls.title}\"*")
    wa_lines.append("")

    # Rich fields from the class source, when available
    if getattr(cls, 'description', None):
        wa_lines.append(cls.description)
        wa_lines.append("")

    if getattr(cls, 'affirmation', None):
        wa_lines.append("*Affirmation of the Class:*")
        wa_lines.append(cls.affirmation)
        wa_lines.append("")

    if getattr(cls, 'key_actions', None):
        wa_lines.append("*Key Actions:*")
        wa_lines.append(cls.key_actions)
        wa_lines.append("")

    if getattr(cls, 'class_focus', None):
        wa_lines.append("*Class Focus:*")
        wa_lines.append(cls.class_focus)
        wa_lines.append("")

    if getattr(cls, 'categories', None):
        wa_lines.append("*Categories:*")
        wa_lines.append(cls.categories)
        wa_lines.append("")

    if join_url:
        wa_lines.append(f"*Link to Join:* {join_url}")
    else:
        wa_lines.append("*Link to Join:* https://studio.tiffanywoodyoga.com/calendar")
    wa_lines.append("")
    wa_lines.append("See you there! 💕")

    footer_lines = [
        "",
        "—— end message ———",
    ]

    lines = header_lines + wa_lines + footer_lines

    msg = EmailMessage()
    msg["Subject"] = subj
    if sender:
        msg["From"] = sender
    if to_addrs:
        msg["To"] = ", ".join(to_addrs)
    msg.set_content("\n".join(lines))
    return msg


def send_email(msg: EmailMessage, host: str, port: int, user: str, password: str, dry_run: bool) -> None:
    if dry_run:
        print("--- DRY RUN: would send email ---")
        print("To:", msg["To"])
        print("Subject:", msg["Subject"])
        print("")
        print(msg.get_content())
        print("--- END ---")
        return

    with smtplib.SMTP(host, port, timeout=30) as smtp:
        smtp.starttls()
        smtp.login(user, password)
        smtp.send_message(msg)


def send_email_via_gmail(msg: EmailMessage, creds, dry_run: bool) -> None:
    """Send an email using the Gmail API."""
    if dry_run:
        print("--- DRY RUN: would send email via Gmail API ---")
        print("To:", msg.get("To"))
        print("Subject:", msg.get("Subject"))
        print("")
        print(msg.get_content())
        print("--- END ---")
        return

    service = build("gmail", "v1", credentials=creds)
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")
    body = {"raw": raw}
    service.users().messages().send(userId="me", body=body).execute()


def parse_offsets(env_value: str | None) -> List[int]:
    if not env_value:
        return [26, 25, 24]
    out: List[int] = []
    for part in env_value.split(','):
        part = part.strip()
        if not part:
            continue
        try:
            out.append(int(part))
        except ValueError:
            continue
    return out or [26, 25, 24]


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Send class reminder emails based on Google Doc schedule.")
    parser.add_argument("--now", help="Override current time (ISO 8601) for testing, e.g. 2026-01-14T07:00")
    parser.add_argument("--dry-run", action="store_true", help="Do not actually send emails; print them instead.")
    args = parser.parse_args(argv)

    doc_id = os.environ.get("GOOGLE_DOC_ID")
    if not doc_id:
        print("❌ GOOGLE_DOC_ID environment variable is required.", file=sys.stderr)
        return 1

    tz_name = os.environ.get("TIMEZONE", "America/Denver")
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        print(f"❌ Invalid TIMEZONE: {tz_name}", file=sys.stderr)
        return 1

    offsets = parse_offsets(os.environ.get("REMINDER_OFFSETS"))

    state_path = Path(os.environ.get("REMINDER_STATE_PATH", "./data/reminder_state.json"))
    state = load_state(state_path)

    if args.now:
        now = date_parser.parse(args.now)
        if not now.tzinfo:
            now = now.replace(tzinfo=tz)
        else:
            now = now.astimezone(tz)
    else:
        now = datetime.now(tz)

    email_from = os.environ.get("EMAIL_FROM")
    email_to_raw = os.environ.get("EMAIL_TO")

    if not email_to_raw:
        print("❌ EMAIL_TO environment variable is required.", file=sys.stderr)
        return 1

    to_addrs = [addr.strip() for addr in email_to_raw.split(',') if addr.strip()]
    if not to_addrs:
        print("❌ EMAIL_TO did not contain any valid addresses.", file=sys.stderr)
        return 1

    gmail_creds = load_gmail_credentials()

    doc_text = fetch_doc_text(doc_id)
    classes = parse_classes(doc_text, tz)

    if not classes:
        print("⚠️ No classes parsed from Google Doc. Check the document format.", file=sys.stderr)
        return 0

    due = compute_due_reminders(classes, offsets, now, state)

    if not due:
        print("No reminders due at this time.")
        return 0

    marvel_events = fetch_marvelous_events()

    for cls, offset in due:
        join_url = find_marvelous_event_for_class(cls, marvel_events)
        msg = build_email(cls, offset, tz, to_addrs, email_from, join_url)
        send_email_via_gmail(msg, gmail_creds, args.dry_run)

        state.setdefault(cls.id, {})[str(offset)] = now.isoformat()

    save_state(state_path, state)
    print(f"Sent {len(due)} reminder(s).")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

