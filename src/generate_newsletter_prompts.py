#!/usr/bin/env python3
"""
Newsletter prompt generation -- runs daily via cron.

Logic:
- Any day: if next month's newsletters already exist, nothing to do.
- Any day: if next month's prompts exist but newsletters don't, post reminder to #status-newsletters.
- On/after 28th: if prompts don't exist yet, check class plan coverage and generate them.
- Before 28th: no prompt generation, but still sends reminder if prompts exist without newsletters.
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

from newsletter import save_prompt, prompt_path, newsletter_path
from newsletter_prompt import check_coverage, assemble_lifestyle_prompt, assemble_non_lifestyle_prompt
from slack import post_slack

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

    # If newsletters already exist for next month, nothing to do
    nl_lifestyle = newsletter_path(year, month, "lifestyle")
    nl_non_lifestyle = newsletter_path(year, month, "non-lifestyle")
    if nl_lifestyle.exists() and nl_non_lifestyle.exists():
        print(f"Newsletters exist for {month_label}, nothing to do")
        return

    # If prompts already exist but newsletters don't, send daily reminder
    p_lifestyle = prompt_path(year, month, "lifestyle")
    p_non_lifestyle = prompt_path(year, month, "non-lifestyle")
    if p_lifestyle.exists() and p_non_lifestyle.exists():
        msg = (
            f":bell: Newsletter prompts are ready for {month_label} but newsletters haven't been generated yet. "
            f"Trigger Tweee: \"Create the {month_label} newsletters\""
        )
        post_slack(SLACK_STATUS_CHANNEL, msg)
        print(msg)
        return

    # Only generate prompts on/after 28th
    if today.day < 28:
        print(f"{today}: before 28th, skipping prompt generation")
        return

    # Load overview
    overview = load_month_overview(month)
    if not overview:
        msg = f":x: Newsletter prompts FAILED for {month_label}: no monthly overview found"
        print(msg)
        post_slack(SLACK_STATUS_CHANNEL, msg)
        sys.exit(1)

    # Load class plans
    plans = load_plans_for_month(year, month)

    # Coverage check
    try:
        check_coverage(plans, year, month)
    except ValueError as e:
        msg = f":x: Insufficient class plans for {month_label}: {e}. Add plans and re-run."
        print(msg)
        post_slack(SLACK_STATUS_CHANNEL, msg)
        sys.exit(1)

    # Assemble and save prompts
    lifestyle_prompt = assemble_lifestyle_prompt(overview, plans, year, month)
    non_lifestyle_prompt = assemble_non_lifestyle_prompt(overview, plans, year, month)

    save_prompt(year, month, "lifestyle", lifestyle_prompt)
    save_prompt(year, month, "non-lifestyle", non_lifestyle_prompt)

    msg = (
        f":memo: Newsletter prompts ready for {month_label}. "
        f"Trigger Tweee: \"Create the {month_label} newsletters\""
    )
    post_slack(SLACK_STATUS_CHANNEL, msg)
    print(msg)


if __name__ == "__main__":
    main()
