from __future__ import annotations

from datetime import date

import pytest

import habit_newsletter_prompt
import provision_recording_product
import run_habit_followup
import run_sendgrid_mailings
import sync_habit_registrations
import sync_sendgrid_habit_registrations
from twy_platform.planning import PlanningClientError


HABIT_PLAN = {
    "id": "habit-plan",
    "date": "2026-08-15",
    "class_type": "Habit",
    "marvelous_event_id": 101,
    "marvelous_media_id": 202,
    "title": "Fluid Motion",
}


class FakePlanningClient:
    def __init__(self):
        self.calls = []

    def list_plans(self, *, from_date="", to_date="", timeout=10):
        self.calls.append(("list", from_date, to_date, timeout))
        if from_date <= HABIT_PLAN["date"] <= to_date:
            return [dict(HABIT_PLAN)]
        return []

    def get_plan(self, date_str, *, timeout=10):
        self.calls.append(("get", date_str, timeout))
        return dict(HABIT_PLAN) if date_str == HABIT_PLAN["date"] else None


def _install(monkeypatch, module):
    client = FakePlanningClient()
    class BoundPlanningClient:
        @classmethod
        def from_env(cls):
            return client

    monkeypatch.setattr(module, "PlanningClient", BoundPlanningClient)
    return client


def test_day_of_followup_uses_authenticated_document_read(monkeypatch):
    client = _install(monkeypatch, run_habit_followup)

    assert run_habit_followup.is_habit_class_today(date(2026, 8, 15)) is True
    assert client.calls == [("get", "2026-08-15", 10)]


def test_registration_readers_keep_their_measured_windows(monkeypatch):
    mailchimp_client = _install(monkeypatch, sync_habit_registrations)
    sendgrid_client = _install(monkeypatch, sync_sendgrid_habit_registrations)

    events, failures = sync_habit_registrations.upcoming_habit_events(
        date(2026, 8, 12)
    )
    sendgrid_events, sendgrid_failures = (
        sync_sendgrid_habit_registrations.upcoming_habit_events(
            date(2026, 8, 12)
        )
    )

    assert events == [(date(2026, 8, 15), 101)]
    assert failures == []
    assert sendgrid_events == [(date(2026, 8, 15), 101)]
    assert sendgrid_failures == []
    assert [call[0] for call in mailchimp_client.calls] == ["list", "list"]
    assert [call[0] for call in sendgrid_client.calls] == ["list", "list"]


def test_registration_readers_report_configuration_failures(monkeypatch):
    class UnconfiguredPlanningClient:
        @classmethod
        def from_env(cls):
            raise PlanningClientError("planning token missing")

    monkeypatch.setattr(
        sync_habit_registrations,
        "PlanningClient",
        UnconfiguredPlanningClient,
    )
    monkeypatch.setattr(
        sync_sendgrid_habit_registrations,
        "PlanningClient",
        UnconfiguredPlanningClient,
    )

    try:
        events, failures = sync_habit_registrations.upcoming_habit_events(
            date(2026, 8, 12)
        )
    except PlanningClientError:
        pytest.fail("MailChimp registration reader leaked a configuration failure")
    try:
        sendgrid_events, sendgrid_failures = (
            sync_sendgrid_habit_registrations.upcoming_habit_events(
                date(2026, 8, 12)
            )
        )
    except PlanningClientError:
        pytest.fail("SendGrid registration reader leaked a configuration failure")

    assert events == []
    assert sendgrid_events == []
    assert len(failures) == 2
    assert len(sendgrid_failures) == 2
    assert all("planning token missing" in failure for failure in failures)
    assert all(
        "planning token missing" in failure for failure in sendgrid_failures
    )


def test_mailing_recording_and_prompt_readers_share_the_planning_client(monkeypatch):
    mailing_client = _install(monkeypatch, run_sendgrid_mailings)
    recording_client = _install(monkeypatch, provision_recording_product)
    prompt_client = _install(monkeypatch, habit_newsletter_prompt)

    assert run_sendgrid_mailings.habit_class_date(2026, 8) == date(2026, 8, 15)
    assert provision_recording_product.habit_class(2026, 8) == HABIT_PLAN
    assert habit_newsletter_prompt.get_habit_class_date(2026, 8) == date(
        2026, 8, 15
    )
    assert mailing_client.calls[0][0] == "list"
    assert recording_client.calls[0][0] == "list"
    assert prompt_client.calls[0][0] == "list"


def test_prompt_date_lookup_refuses_to_invent_a_date_when_no_habit_plan_exists(
    monkeypatch,
):
    class EmptyPlanningClient:
        def list_plans(self, **_kwargs):
            return []

    class BoundPlanningClient:
        @classmethod
        def from_env(cls):
            return EmptyPlanningClient()

    monkeypatch.setattr(
        habit_newsletter_prompt,
        "PlanningClient",
        BoundPlanningClient,
    )

    with pytest.raises(ValueError, match="No Habit class plan"):
        habit_newsletter_prompt.get_habit_class_date(2026, 8)


def test_prompt_date_lookup_propagates_planning_api_failures(monkeypatch):
    class FailingPlanningClient:
        def list_plans(self, **_kwargs):
            raise PlanningClientError("planning unavailable", status_code=503)

    class BoundPlanningClient:
        @classmethod
        def from_env(cls):
            return FailingPlanningClient()

    monkeypatch.setattr(
        habit_newsletter_prompt,
        "PlanningClient",
        BoundPlanningClient,
    )

    with pytest.raises(PlanningClientError, match="planning unavailable"):
        habit_newsletter_prompt.get_habit_class_date(2026, 8)
