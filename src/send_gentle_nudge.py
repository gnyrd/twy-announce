#!/usr/bin/env python3
"""Send the Yoga Habit Gentle Nudge for the upcoming class.

Cron: 0 17 * * 5 (Friday 5pm MT — convert to UTC for the actual crontab line)

Date guard: today must be a Friday strictly before this month's Habit class
(within the same week). Self-skips Fridays that aren't class week.

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
AUDIENCE_LABEL = "Gentle Nudge"

# Bridge: May 2026 was hand-managed (manual drafts with legacy titles
# "Non-Openers" / "Openers" that this script can't match). New auto-system
# owns June 2026 onward. After May 17, this guard is inert.
EARLIEST_FIRE_DATE = date(2026, 5, 17)


def is_today_due(today: date, class_date: date) -> bool:
    """Friday strictly before class, in the same week (1..7 days out)."""
    if today.weekday() != 4:  # 4 = Fri
        return False
    days_until = (class_date - today).days
    return 0 < days_until <= 7


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--as-of", help="YYYY-MM-DD override of today (testing)")
    ap.add_argument("--dry-run", action="store_true",
                    help="skip MC send AND skip all Slack posts (warnings go to stdout)")
    args = ap.parse_args()

    today = date.fromisoformat(args.as_of) if args.as_of else datetime.now(MOUNTAIN).date()
    notify = not args.dry_run

    if today < EARLIEST_FIRE_DATE:
        print(f"[gentle_nudge] today={today} — before EARLIEST_FIRE_DATE {EARLIEST_FIRE_DATE}, exiting silently")
        return 0

    next_y, next_m = (today.year + 1, 1) if today.month == 12 else (today.year, today.month + 1)
    for (y, m) in ((today.year, today.month), (next_y, next_m)):
        try:
            cd = get_habit_class_date(y, m)
        except Exception as e:
            print(f"  could not resolve Habit class for {y}-{m:02d}: {e}", file=sys.stderr)
            continue
        if is_today_due(today, cd):
            print(f"[gentle_nudge] today={today} class={cd} ({y}-{m:02d}) — DUE")
            return perform_send(AUDIENCE_LABEL, y, m, notify=notify, dry_run=args.dry_run)

    print(f"[gentle_nudge] today={today} — not the day to fire (no upcoming class matches)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
