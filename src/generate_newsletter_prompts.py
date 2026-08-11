#!/usr/bin/env python3
"""
Newsletter prompt generation -- runs daily via cron.

Logic:
- Any day: if next month's newsletters already exist, nothing to do.
- Any day: if next month's prompts exist but newsletters don't, post reminder to #status-newsletters.
- On/after 25th: if prompts don't exist yet, check class plan coverage and generate them.
- Before 25th: no prompt generation, but still sends reminder if prompts exist without newsletters.
"""
import json
import os
import sys
import calendar
from datetime import datetime, date
from pathlib import Path
from zoneinfo import ZoneInfo

from twy_paths import load_env, data_root
from twy_classplan import load_plan

load_env()

sys.path.insert(0, str(Path(__file__).parent))

from newsletter_paths import save_prompt, prompt_path, newsletter_path
import habit_newsletter_prompt as hnp
from habit_newsletter_prompt import check_coverage, MINIMUM_CLASS_PLANS
from newsletter_editorial_review import compile_approved_inputs
from slack_post import post_slack

MOUNTAIN             = ZoneInfo("America/Denver")
SLACK_STATUS_CHANNEL = os.getenv("SLACK_STATUS_CHANNEL", "#status-newsletters")


def load_month_overview(month: int) -> dict | None:
    f = data_root() / "monthly-overview.json"
    if not f.exists():
        return None
    return json.loads(f.read_text()).get(str(month))


def load_plans_for_month(year: int, month: int) -> dict:
    plans = {}
    for day in range(1, calendar.monthrange(year, month)[1] + 1):
        d = date(year, month, day).isoformat()
        p = load_plan(d)
        if p:
            plans[d] = p
    return plans


def next_month(year: int, month: int) -> tuple[int, int]:
    if month == 12:
        return year + 1, 1
    return year, month + 1


def main():
    today = datetime.now(MOUNTAIN).date()
    year, month = next_month(today.year, today.month)
    month_label = date(year, month, 1).strftime("%B %Y")

    # One-off: July 2026 has no Habit class (cancelled), so only the two monthly prompts
    # apply and they are hand-built. Skip auto-generation for July 2026 so it does not
    # red-X on the missing Habit plan or expect the follow-up prompts. Remove after 2026-07.
    if (year, month) == (2026, 7):
        print(f"{today}: July 2026 handled manually (no Habit class) -- skipping auto prompt generation.")
        return

    # If newsletters already exist for next month, nothing to do
    AUDIENCES = ("lifestyle", "non-lifestyle", "non-opener", "reminder", "gentle-nudge", "ph1", "ph2")
    nl_paths = {a: newsletter_path(year, month, a) for a in AUDIENCES}
    if all(p.exists() for p in nl_paths.values()):
        print(f"All {len(AUDIENCES)} newsletters exist for {month_label}, nothing to do")
        return

    # If prompts already exist but newsletters don't, send daily reminder
    p_paths = {a: prompt_path(year, month, a) for a in AUDIENCES}
    if all(p.exists() for p in p_paths.values()):
        msg = (
            f":bell: All prompts ready for {month_label} but content hasn't been generated yet. "
            f"Trigger Tweee: \"Use your Actions: get the {month_label} newsletter prompts and save the newsletter content\""
        )
        post_slack(SLACK_STATUS_CHANNEL, msg)
        print(msg)
        return

    # Only generate prompts on/after 25th
    if today.day < 25:
        return

    # Load overview
    overview = load_month_overview(month)
    if not overview:
        msg = (
            f":clipboard: Tiff action needed for {month_label}: "
            "the monthly overview is missing. "
            "Newsletter draft generation remains blocked."
        )
        print(msg)
        post_slack(SLACK_STATUS_CHANNEL, msg)
        return

    # Load class plans
    plans = load_plans_for_month(year, month)

    # Coverage check
    try:
        check_coverage(plans, year, month)
    except ValueError as e:
        if len(plans) < MINIMUM_CLASS_PLANS:
            missing = MINIMUM_CLASS_PLANS - len(plans)
            msg = (
                f":clipboard: Tiff action needed for {month_label}: "
                f"{missing} more class plans required "
                f"({len(plans)} of {MINIMUM_CLASS_PLANS}). "
                "Newsletter draft generation remains blocked until all "
                f"{MINIMUM_CLASS_PLANS} are ready."
            )
        else:
            msg = (
                f":clipboard: Tiff action needed for {month_label}: {e}. "
                "Newsletter draft generation remains blocked."
            )
        print(msg)
        post_slack(SLACK_STATUS_CHANNEL, msg)
        return

    # Compile only immutable, completed approvals into the prompt input indexes.
    # Missing or malformed indexes fail prompt generation closed.
    compile_approved_inputs()

    # Assemble and save prompts
    save_prompt(year, month, "lifestyle", hnp.assemble_lifestyle_prompt(overview, plans, year, month))
    save_prompt(year, month, "non-lifestyle", hnp.assemble_non_lifestyle_prompt(overview, plans, year, month))
    save_prompt(year, month, "non-opener", hnp.assemble_non_opener_prompt(overview, plans, year, month))
    save_prompt(year, month, "reminder", hnp.assemble_reminder_prompt(overview, plans, year, month))
    save_prompt(year, month, "gentle-nudge", hnp.assemble_gentle_nudge_prompt(overview, plans, year, month))
    save_prompt(year, month, "ph1", hnp.assemble_ph1_prompt(overview, plans, year, month))
    save_prompt(year, month, "ph2", hnp.assemble_ph2_prompt(overview, plans, year, month))

    msg = (
        f":memo: All prompts ready for {month_label} (newsletters + follow-ups). "
        f"Trigger Tweee: \"Use your Actions: get the {month_label} newsletter prompts and save the newsletter content\""
    )
    post_slack(SLACK_STATUS_CHANNEL, msg)
    print(msg)


if __name__ == "__main__":
    main()
