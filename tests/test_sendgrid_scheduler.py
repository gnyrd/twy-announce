from datetime import date, datetime, timezone

from sendgrid_mailings import MailingPurpose
from sendgrid_scheduler import schedule_month


class FakeCampaigns:
    def __init__(self, statuses=None):
        self.statuses = statuses or {}
        self.calls = []

    def expected_purposes(self):
        return list(MailingPurpose)

    def single_send(self, purpose):
        if purpose not in self.statuses:
            raise KeyError(purpose.value)
        return {
            "id": purpose.value,
            "status": self.statuses[purpose],
        }

    def schedule(self, purpose, send_at):
        self.calls.append((purpose, send_at))
        return {
            "id": purpose.value,
            "status": "scheduled",
            "send_at": send_at.isoformat(),
        }


def test_scheduler_keeps_draft_editable_before_scheduling_window():
    campaigns = FakeCampaigns({
        MailingPurpose.MONTHLY: "draft",
    })
    campaigns.expected_purposes = lambda: [MailingPurpose.MONTHLY]

    results = schedule_month(
        campaigns=campaigns,
        year=2026,
        month=8,
        class_date=date(2026, 8, 8),
        now=datetime(2026, 8, 2, 15, 38, tzinfo=timezone.utc),
    )

    assert campaigns.calls == []
    assert results["Monthly"] == {
        "id": "Monthly",
        "status": "ready",
        "provider_status": "draft",
        "schedule_at": "2026-08-02T15:39:00+00:00",
        "send_at": "2026-08-03T15:39:00+00:00",
    }


def test_scheduler_schedules_automatically_at_window_boundary():
    campaigns = FakeCampaigns({
        MailingPurpose.MONTHLY: "draft",
    })
    campaigns.expected_purposes = lambda: [MailingPurpose.MONTHLY]

    results = schedule_month(
        campaigns=campaigns,
        year=2026,
        month=8,
        class_date=date(2026, 8, 8),
        now=datetime(2026, 8, 2, 15, 39, tzinfo=timezone.utc),
    )

    assert campaigns.calls == [
        (
            MailingPurpose.MONTHLY,
            datetime(2026, 8, 3, 15, 39, tzinfo=timezone.utc),
        ),
    ]
    assert results["Monthly"]["status"] == "scheduled"


def test_scheduler_applies_window_to_every_recurring_mailing():
    campaigns = FakeCampaigns({
        purpose: "draft" for purpose in MailingPurpose
    })

    results = schedule_month(
        campaigns=campaigns,
        year=2026,
        month=8,
        class_date=date(2026, 8, 8),
        now=datetime(2026, 8, 1, tzinfo=timezone.utc),
    )

    assert campaigns.calls == []
    assert {item["status"] for item in results.values()} == {"ready"}


def test_scheduler_rejects_unexpected_provider_state_before_window():
    campaigns = FakeCampaigns({
        MailingPurpose.MONTHLY: "canceled",
    })
    campaigns.expected_purposes = lambda: [MailingPurpose.MONTHLY]

    results = schedule_month(
        campaigns=campaigns,
        year=2026,
        month=8,
        class_date=date(2026, 8, 8),
        now=datetime(2026, 8, 1, tzinfo=timezone.utc),
    )

    assert campaigns.calls == []
    assert results["Monthly"]["status"] == "unexpected"
    assert results["Monthly"]["provider_status"] == "canceled"


def test_scheduler_handles_monthly_only_without_a_habit_class():
    campaigns = FakeCampaigns({MailingPurpose.MONTHLY: "draft"})
    campaigns.expected_purposes = lambda: [MailingPurpose.MONTHLY]

    results = schedule_month(
        campaigns=campaigns,
        year=2026,
        month=8,
        class_date=None,
        now=datetime(2026, 8, 2, 15, 39, tzinfo=timezone.utc),
    )

    assert set(results) == {"Monthly"}
    assert len(campaigns.calls) == 1


def test_scheduler_requires_triggered_state_after_due_time():
    campaigns = FakeCampaigns({
        MailingPurpose.MONTHLY: "triggered",
        MailingPurpose.GENERAL_INVITATION: "draft",
    })
    results = schedule_month(
        campaigns=campaigns,
        year=2026,
        month=8,
        class_date=date(2026, 8, 8),
        now=datetime(2026, 8, 4, tzinfo=timezone.utc),
    )
    assert results["Monthly"]["status"] == "triggered"
    assert results["General Invitation"]["status"] == "overdue"
    assert not campaigns.calls


def test_scheduler_reports_missing_drafts_without_hiding_them():
    campaigns = FakeCampaigns({})
    results = schedule_month(
        campaigns=campaigns,
        year=2026,
        month=8,
        class_date=date(2026, 8, 8),
        now=datetime(2026, 8, 1, tzinfo=timezone.utc),
    )
    assert set(results) == {purpose.value for purpose in MailingPurpose}
    assert {item["status"] for item in results.values()} == {"missing"}
