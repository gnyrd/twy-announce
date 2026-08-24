#!/usr/bin/env python3
"""Monthly campaign tick: launch every On, monthly, fully-approved campaign for
the current period, idempotently.

Runs daily. The launch is idempotent per period, so a daily run schedules the
period's sends once and, on later runs, provisions any email its send gate held
earlier once the gate passes (a recording attached late, the class date arriving)
without touching what already sent. With nothing On and approved, it launches
nothing.

The selection and per-period logic is campaign_ticker (unit tested). This wires
the live SendGrid launcher and the gate context resolved from the class plans.
"""
import calendar
import logging
from datetime import date, datetime
from zoneinfo import ZoneInfo

import requests

from twy_paths import journeys_dir, load_env, campaign_launch_state_path
from twy_platform.journeys import list_journeys

from campaign_ticker import is_due, launch_due_campaigns
from habit_newsletter_prompt import CLASSES_API, get_habit_class_date

load_env()

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
)
log = logging.getLogger("campaign_ticks")

MOUNTAIN = ZoneInfo("America/Denver")


def real_habit_class_date(year: int, month: int):
    """The month's Habit class date from an AUTHORED plan, or None.

    get_habit_class_date falls back to the second Saturday so a schedule always
    resolves; the class_exists gate needs the stricter fact of whether a plan
    was actually authored. Until 2026-08-23 this checked only that a Habit plan
    existed, so a published placeholder (the Sept "Yoga Habits Class"
    boilerplate) would have sent invitations for an unwritten class. The
    classes API now stamps every plan with authored (content-based, computed
    by plan_authored in the classes app); a missing stamp counts as authored
    so an older API keeps the pre-stamp behavior. On an API error this returns
    None, which HOLDS the class-gated invitations rather than sending against
    an unconfirmed class.
    """
    last_day = calendar.monthrange(year, month)[1]
    try:
        resp = requests.get(
            f"{CLASSES_API}/api/plans",
            params={"from": f"{year:04d}-{month:02d}-01",
                    "to": f"{year:04d}-{month:02d}-{last_day:02d}"},
            timeout=10,
        )
        if resp.ok:
            for plan in resp.json():
                if plan.get("class_type") == "Habit" and plan.get("authored", True):
                    return date.fromisoformat(plan["date"])
    except requests.RequestException as exc:
        log.warning("classes API unreachable resolving class_exists: %s", exc)
    return None


def _build_launcher_factory():
    """A launch_one bound to the live SendGrid account, built once for the run.

    The heavy provider imports and the account handles load here, not at module
    import, so the entrypoint stays importable and a test can substitute the
    whole thing.
    """
    import os

    from campaign_launch import CampaignLauncher, GateContext
    from provision_recording_product import recording_ready
    from sendgrid_api import SendGridAPI
    from sendgrid_campaigns import SendGridCampaigns, SendGridRegistry
    from sendgrid_newsletter_workflow import (
        ensure_recording_draft,
        read_local_sections,
        resolve_section_tokens,
    )
    from twy_paths import newsletters_dir, sendgrid_registry_path

    key = os.getenv("SENDGRID_API_KEY", "")
    if not key:
        raise RuntimeError("SENDGRID_API_KEY is not configured")
    api = SendGridAPI(key)
    registry = SendGridRegistry.load(sendgrid_registry_path())
    today = datetime.now(MOUNTAIN).date()

    def launch_one(pinned, year, month):
        try:
            real = real_habit_class_date(year, month)
        except Exception as exc:  # noqa: BLE001 - fail-soft is the point
            # real_habit_class_date already swallows a plain classes-API
            # network failure and returns None; this catches anything else it
            # might raise (a malformed response). Either way, a classes-API
            # hiccup must hold the recording seed, never abort this journey's
            # whole launch for the tick: read_local_sections and the launcher
            # still run below, exactly as if no class were confirmed yet.
            log.warning(
                "%04d_%02d: could not resolve the real habit class date, "
                "recording draft seed skipped this tick: %s",
                year, month, exc,
            )
            real = None
        if real is not None:
            # Seeds the month's recording draft from the canonical template the
            # same way run_sendgrid_mailings.py does, guarded by the same fact
            # (a confirmed Habit class this month). Idempotent, so a daily tick
            # after the first no-ops here. Makes the campaign path
            # self-sufficient once the old newsletter workflow retires.
            ensure_recording_draft(year, month)
        context = GateContext(
            class_exists=real is not None,
            # The Class Recording email holds until the edited recording is
            # actually attached to its free product at the provider. The resolver
            # fails closed, so a provider hiccup holds the email rather than
            # promising a recording that is not there.
            recording_ready=recording_ready(year, month),
            class_date=real or get_habit_class_date(year, month),
            now=today,
        )
        # Shares the newsletter workflow's own SendGrid state file for the
        # period, so a dynamic audience the launcher builds reuses the same
        # locked-name segment the workflow would, never a duplicate.
        campaigns = SendGridCampaigns(
            api=api,
            registry=registry,
            state_path=newsletters_dir() / f"{year:04d}-{month:02d}" / ".sendgrid.json",
        )
        # This period's reviewed newsletter drafts, with the token vocabulary
        # (CLASS_TITLE, RECORDING_CTA, ...) resolved against this month's facts,
        # so a campaign email tagged with a section sends real copy rather than
        # a literal {CLASS_TITLE}. resolve_section_tokens deliberately leaves a
        # token in place when the fact it needs is not there yet (no recording
        # record), and the launcher's own fail-closed guard holds that email
        # until it is.
        raw_sections = read_local_sections(year, month)
        sections = {
            key: resolve_section_tokens(key, section, year=year, month=month)
            for key, section in raw_sections.items()
        }
        launcher = CampaignLauncher(
            api=api,
            registry=registry,
            journey=pinned,
            state_path=campaign_launch_state_path(
                f"{pinned['journey_id']}_{year:04d}_{month:02d}"
            ),
            gate_context=context,
            sections=sections,
            # The handle for building the dynamic audiences (follow-ups'
            # interested-non-members, gentle reminder's openers-not-registered).
            campaigns=campaigns,
        )
        return launcher.launch(date(year, month, 1))

    return launch_one


def main():
    today = datetime.now(MOUNTAIN).date()
    year, month = today.year, today.month
    journeys = list_journeys(journeys_dir())
    if not any(is_due(j) for j in journeys):
        # The common case, and it needs no SendGrid handles at all: with nothing
        # On and fully approved there is nothing to launch.
        log.info("campaign tick %04d_%02d: nothing due", year, month)
        return 0
    launch_one = _build_launcher_factory()
    results = launch_due_campaigns(
        journeys, year, month, launch_one=launch_one, log=log.warning
    )
    launched = [r for r in results if "result" in r]
    failed = [r for r in results if "error" in r]
    log.info(
        "campaign tick %04d_%02d: %d launched, %d failed",
        year, month, len(launched), len(failed),
    )
    for row in launched:
        log.info("launched %s", row["journey_id"])
    for row in failed:
        log.error("failed %s: %s", row["journey_id"], row["error"])
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
