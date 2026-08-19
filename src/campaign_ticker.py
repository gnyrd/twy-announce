"""Drive the monthly campaigns: launch each On, monthly, fully-approved campaign
for the current period, idempotently.

A campaign definition is period-independent (recurrence monthly, anchored dates),
but the launcher is pinned to one campaign_month and keeps one state file. This
resolves the current period onto a copy of the campaign and hands it to a
provider callback, so the same definition runs every month with no date picked
by hand. This is the only automated caller of the launcher; the editor's manual
launch is for one-time campaigns, which this never touches.

The provider work (building the SendGrid launcher and the gate context from the
class plans) is injected, so the selection and per-period logic here is unit
tested without reaching SendGrid or HeyMarvelous, and the heavy imports load only
in the entrypoint.
"""
from __future__ import annotations

from datetime import date

from twy_platform.journeys import (
    RECURRENCE_MONTHLY,
    TYPE_CAMPAIGN,
    journey_type,
)


def all_emails_approved(journey: dict) -> bool:
    """Whether every email in the campaign is approved. An empty campaign is not.

    The launch gate clears only when the complete set is approved, so the tick
    holds a campaign with even one unapproved email, exactly like the manual
    launch does.
    """
    emails = journey.get("emails") or []
    return bool(emails) and all(email.get("approved_at") for email in emails)


def is_due(journey: dict) -> bool:
    """A campaign the monthly tick may launch: a campaign, On, monthly, fully approved.

    Anything else, a product journey, a one-time campaign, an Off or partly
    approved one, is left alone. This is the whole safety gate: with nothing On
    and approved, the tick launches nothing.
    """
    try:
        if journey_type(journey) != TYPE_CAMPAIGN:
            return False
    except ValueError:
        return False
    return (
        bool(journey.get("active"))
        and journey.get("recurrence") == RECURRENCE_MONTHLY
        and all_emails_approved(journey)
    )


def period_journey(journey: dict, year: int, month: int) -> dict:
    """A copy of the campaign pinned to one period.

    The stored definition carries whatever month it was created in; the launcher
    resolves this period's anchored dates and Single Send names from
    campaign_month, so the tick sets it to the period being run rather than
    mutating the stored campaign.
    """
    resolved = dict(journey)
    resolved["campaign_month"] = f"{year:04d}_{month:02d}"
    resolved["run_date"] = date(year, month, 1).isoformat()
    return resolved


def launch_due_campaigns(journeys, year, month, *, launch_one, log=None) -> list[dict]:
    """Launch every due campaign for the period, fail-soft per campaign.

    `launch_one(period_journey, year, month)` does the provider work and is
    injected. One campaign's error is recorded and never stops the others, the
    same rule every TWY batch job follows, so a single bad campaign cannot take
    the whole tick down.
    """
    results = []
    for journey in journeys:
        jid = journey.get("journey_id")
        if not is_due(journey):
            continue
        pinned = period_journey(journey, year, month)
        try:
            outcome = launch_one(pinned, year, month)
        except Exception as exc:  # noqa: BLE001 - fail-soft is the point
            if log:
                log(f"campaign {jid} failed for {year:04d}_{month:02d}: {exc}")
            results.append({"journey_id": jid, "error": str(exc)})
            continue
        results.append({"journey_id": jid, "result": outcome})
    return results
