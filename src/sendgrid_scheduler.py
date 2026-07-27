"""Schedule and verify one month of TWY SendGrid mailings."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from sendgrid_mailings import MailingPurpose, mailing_schedule


SCHEDULING_WINDOW = timedelta(hours=24)


def schedule_month(
    *,
    campaigns,
    year: int,
    month: int,
    class_date: date | None,
    now: datetime,
) -> dict[str, dict]:
    if now.tzinfo is None:
        raise ValueError("scheduler time must be timezone aware")
    current = now.astimezone(timezone.utc)
    results: dict[str, dict] = {}

    for purpose in campaigns.expected_purposes():
        target = mailing_schedule(year, month, purpose, class_date)
        try:
            single_send = campaigns.single_send(purpose)
        except KeyError:
            results[purpose.value] = {
                "status": "missing",
                "send_at": target.isoformat(),
            }
            continue

        if target <= current:
            status = single_send.get("status")
            results[purpose.value] = {
                "id": single_send.get("id"),
                "status": (
                    "triggered" if status == "triggered" else "overdue"
                ),
                "provider_status": status,
                "send_at": target.isoformat(),
            }
            continue

        schedule_at = target - SCHEDULING_WINDOW
        if current < schedule_at:
            provider_status = single_send.get("status")
            if provider_status == "draft":
                status = "ready"
            elif provider_status == "scheduled":
                status = "scheduled"
            else:
                status = "unexpected"
            results[purpose.value] = {
                "id": single_send.get("id"),
                "status": status,
                "provider_status": provider_status,
                "schedule_at": schedule_at.isoformat(),
                "send_at": target.isoformat(),
            }
            continue

        scheduled = campaigns.schedule(purpose, target)
        results[purpose.value] = {
            "id": scheduled.get("id"),
            "status": "scheduled",
            "provider_status": scheduled.get("status"),
            "send_at": target.isoformat(),
        }

    return results
