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

from newsletter import save_prompt, prompt_path, newsletter_path
import habit_newsletter_prompt as hnp
from habit_newsletter_prompt import check_coverage
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
        print(f"{today}: before 25th, skipping prompt generation")
        return

    # Diff loop (Phases 1-3): archive prior month's sent campaigns, capture diffs,
    # extract patterns, post review candidates to #review-newsletters.
    # The month that just wrapped is today.year/today.month (we generate prompts
    # for next_month()). By the 25th, all of prior-month's sends are complete:
    # main on the 1st, PH1 ~+1d after Habit class (2nd Saturday), PH2 +7d.
    from diff_loop import archive_prior_month_sent, extract_patterns_for_month, post_review_candidates
    prior_year, prior_month = today.year, today.month
    archive_results = archive_prior_month_sent(year=prior_year, month=prior_month)
    archived = sum(1 for v in archive_results.values() if v == "archived")
    print(f"diff-loop archival ({prior_year}-{prior_month:02d}): {archived}/{len(archive_results)} archived. Detail: {archive_results}")
    extract_patterns_for_month(prior_year, prior_month)
    # Assemblers read recent .md files directly via _format_recent_references()
    # at prompt-build time, so no module reload is needed -- whatever the
    # archival step just wrote is what the next assembler call will see.
    review_channel = os.getenv("SLACK_REVIEW_CHANNEL", "#review-newsletters")
    post_review_candidates(prior_year, prior_month, slack_post_fn=lambda t: post_slack(review_channel, t))
    print(f"diff-loop review post sent to {review_channel}")

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
