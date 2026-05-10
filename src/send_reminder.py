#!/usr/bin/env python3
"""Send the Yoga Habit Day-Before Reminder for tomorrow's class.

Cron: 0 10 * * * (every day 10am MT — convert to UTC for the actual crontab line)

Date guard: tomorrow must be the Habit class date for this month or next.
Self-skips every other day of the year. Class can fall on any weekday, so this
script runs daily rather than fixed-day-of-week like the other two.

Flags:
  --as-of YYYY-MM-DD   override 'today' for testing
  --dry-run            skip MC /actions/send AND skip all Slack posts
"""
import argparse
import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).parent))
from habit_newsletter_prompt import get_habit_class_date
from followup_send_lib import perform_send

MOUNTAIN = ZoneInfo("America/Denver")
AUDIENCE_LABEL = "Day-Before Reminder"

# Bridge: May 2026 had no auto reminder draft (the system shipped after the
# May submit cycle). Skip until the May class is past. After May 17 this guard
# is inert.
EARLIEST_FIRE_DATE = date(2026, 5, 17)


def is_today_due(today: date, class_date: date) -> bool:
    """Class is exactly one day after today (i.e. tomorrow)."""
    return (class_date - today).days == 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--as-of", help="YYYY-MM-DD override of today (testing)")
    ap.add_argument("--dry-run", action="store_true",
                    help="skip MC /actions/send AND skip all Slack posts (warnings go to stdout)")
    args = ap.parse_args()

    today = date.fromisoformat(args.as_of) if args.as_of else datetime.now(MOUNTAIN).date()
    notify = not args.dry_run

    if today < EARLIEST_FIRE_DATE:
        print(f"[reminder] today={today} — before EARLIEST_FIRE_DATE {EARLIEST_FIRE_DATE}, exiting silently")
        return 0

    next_y, next_m = (today.year + 1, 1) if today.month == 12 else (today.year, today.month + 1)
    for (y, m) in ((today.year, today.month), (next_y, next_m)):
        try:
            cd = get_habit_class_date(y, m)
        except Exception as e:
            print(f"  could not resolve Habit class for {y}-{m:02d}: {e}", file=sys.stderr)
            continue
        if is_today_due(today, cd):
            print(f"[reminder] today={today} class={cd} ({y}-{m:02d}) — DUE")
            return perform_send(AUDIENCE_LABEL, y, m, notify=notify, dry_run=args.dry_run)

    print(f"[reminder] today={today} — not the day to fire (no upcoming class matches)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
