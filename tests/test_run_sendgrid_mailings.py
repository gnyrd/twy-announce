from datetime import datetime, timezone
from pathlib import Path

import pytest

import run_sendgrid_mailings as runner
from sendgrid_campaigns import EXPECTED_ACCOUNT_EMAIL


class FrozenDateTime:
    @classmethod
    def now(cls, tz=None):
        value = datetime(2026, 8, 2, 15, 39, tzinfo=timezone.utc)
        return value if tz is None else value.astimezone(tz)


def test_runner_materializes_due_local_sections_before_scheduling(
    monkeypatch,
    tmp_path,
    capsys,
):
    monkeypatch.setenv("SENDGRID_API_KEY", "test-key")
    monkeypatch.setenv("TWY_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(runner, "load_env", lambda: None)
    monkeypatch.setattr(runner, "datetime", FrozenDateTime)
    monkeypatch.setattr(runner, "habit_class_date", lambda year, month: None)

    class FakeAPI:
        def __init__(self, api_key):
            self.api_key = api_key

        def user_email(self):
            return EXPECTED_ACCOUNT_EMAIL

    class FakeRegistry:
        @classmethod
        def load(cls, path):
            return cls()

    campaigns_seen = []

    class FakeCampaigns:
        def __init__(self, *, api, registry, state_path):
            self.api = api
            self.registry = registry
            self.state_path = Path(state_path)
            campaigns_seen.append(self)

    local_sections = {
        "lifestyle": {
            "subject": "August",
            "body": "Monthly body",
            "preheader": "A useful inbox preview",
        },
    }
    calls = []
    monkeypatch.setattr(runner, "SendGridAPI", FakeAPI)
    monkeypatch.setattr(runner, "SendGridRegistry", FakeRegistry)
    monkeypatch.setattr(runner, "SendGridCampaigns", FakeCampaigns)
    monkeypatch.setattr(
        runner,
        "read_local_sections",
        lambda year, month: local_sections,
    )
    monkeypatch.setattr(
        runner,
        "lock_due_sections",
        lambda **kwargs: local_sections,
    )
    provider_reports = []
    monkeypatch.setattr(
        runner,
        "apply_provider_report",
        lambda **kwargs: provider_reports.append(kwargs),
    )

    def fake_provision_drafts(**kwargs):
        calls.append(("provision", kwargs))
        return {"lifestyle": {"id": "send1", "status": "draft"}}

    def fake_schedule_month(**kwargs):
        calls.append(("schedule", kwargs))
        return {"Monthly": {"id": "send1", "status": "scheduled"}}

    monkeypatch.setattr(runner, "provision_drafts", fake_provision_drafts)
    monkeypatch.setattr(runner, "schedule_month", fake_schedule_month)

    assert runner.main(["--month", "2026_08"]) == 0

    capsys.readouterr()
    assert [name for name, _ in calls] == ["provision", "schedule"]
    provision = calls[0][1]
    assert provision["sections"] == local_sections
    assert provision["campaigns"] is campaigns_seen[0]
    schedule = calls[1][1]
    assert schedule["campaigns"] is campaigns_seen[0]
    assert provider_reports[0]["report"] == {
        "Monthly": {"id": "send1", "status": "scheduled"}
    }


def test_runner_skips_local_sections_before_materialization_window(
    monkeypatch,
    tmp_path,
    capsys,
):
    monkeypatch.setenv("SENDGRID_API_KEY", "test-key")
    monkeypatch.setenv("TWY_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(runner, "load_env", lambda: None)
    monkeypatch.setattr(runner, "datetime", FrozenDateTime)
    monkeypatch.setattr(runner, "_periods", lambda now: [(2026, 8)])
    monkeypatch.setattr(runner, "habit_class_date", lambda year, month: None)

    class FakeAPI:
        def __init__(self, api_key):
            self.api_key = api_key

        def user_email(self):
            return EXPECTED_ACCOUNT_EMAIL

    class FakeRegistry:
        @classmethod
        def load(cls, path):
            return cls()

    local_sections = {
        "lifestyle": {
            "subject": "August",
            "body": "Monthly body",
            "preheader": "A useful inbox preview",
        },
    }
    monkeypatch.setattr(runner, "SendGridAPI", FakeAPI)
    monkeypatch.setattr(runner, "SendGridRegistry", FakeRegistry)
    monkeypatch.setattr(
        runner,
        "SendGridCampaigns",
        lambda **kwargs: (_ for _ in ()).throw(
            AssertionError("campaigns should not be constructed")
        ),
    )
    monkeypatch.setattr(
        runner,
        "read_local_sections",
        lambda year, month: local_sections,
    )
    monkeypatch.setattr(
        runner,
        "lock_due_sections",
        lambda **kwargs: {},
    )
    monkeypatch.setattr(
        runner,
        "apply_provider_report",
        lambda **kwargs: (_ for _ in ()).throw(
            AssertionError("provider report should not be applied")
        ),
    )

    assert runner.main([]) == 0

    captured = capsys.readouterr()
    assert captured.out.strip() == "{}"


def test_runner_still_processes_periods_through_the_legacy_final_period(
    monkeypatch,
    tmp_path,
    capsys,
):
    monkeypatch.setenv("SENDGRID_API_KEY", "test-key")
    monkeypatch.setenv("TWY_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(runner, "load_env", lambda: None)
    monkeypatch.setattr(runner, "datetime", FrozenDateTime)
    monkeypatch.setattr(
        runner, "_periods", lambda now: [(2026, 8), (2026, 9)]
    )
    monkeypatch.setattr(runner, "habit_class_date", lambda year, month: None)

    class FakeAPI:
        def __init__(self, api_key):
            self.api_key = api_key

        def user_email(self):
            return EXPECTED_ACCOUNT_EMAIL

    class FakeRegistry:
        @classmethod
        def load(cls, path):
            return cls()

    monkeypatch.setattr(runner, "SendGridAPI", FakeAPI)
    monkeypatch.setattr(runner, "SendGridRegistry", FakeRegistry)
    monkeypatch.setattr(runner, "SendGridCampaigns", lambda **kwargs: object())

    local_sections = {
        "lifestyle": {
            "subject": "Monthly",
            "body": "Monthly body",
            "preheader": "A useful inbox preview",
        },
    }
    monkeypatch.setattr(
        runner,
        "read_local_sections",
        lambda year, month: local_sections,
    )
    monkeypatch.setattr(
        runner,
        "lock_due_sections",
        lambda **kwargs: local_sections,
    )
    monkeypatch.setattr(runner, "provision_drafts", lambda **kwargs: {})
    monkeypatch.setattr(runner, "apply_provider_report", lambda **kwargs: None)

    scheduled_periods = []

    def fake_schedule_month(**kwargs):
        scheduled_periods.append((kwargs["year"], kwargs["month"]))
        return {"Monthly": {"id": "send1", "status": "scheduled"}}

    monkeypatch.setattr(runner, "schedule_month", fake_schedule_month)

    assert runner.main([]) == 0

    capsys.readouterr()
    assert scheduled_periods == [(2026, 8), (2026, 9)]


def test_runner_drops_the_default_window_period_after_the_legacy_final_period(
    monkeypatch,
    tmp_path,
    capsys,
):
    monkeypatch.setenv("SENDGRID_API_KEY", "test-key")
    monkeypatch.setenv("TWY_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(runner, "load_env", lambda: None)
    monkeypatch.setattr(runner, "datetime", FrozenDateTime)
    monkeypatch.setattr(
        runner, "_periods", lambda now: [(2026, 9), (2026, 10)]
    )
    monkeypatch.setattr(runner, "habit_class_date", lambda year, month: None)

    class FakeAPI:
        def __init__(self, api_key):
            self.api_key = api_key

        def user_email(self):
            return EXPECTED_ACCOUNT_EMAIL

    class FakeRegistry:
        @classmethod
        def load(cls, path):
            return cls()

    monkeypatch.setattr(runner, "SendGridAPI", FakeAPI)
    monkeypatch.setattr(runner, "SendGridRegistry", FakeRegistry)
    monkeypatch.setattr(runner, "SendGridCampaigns", lambda **kwargs: object())

    local_sections = {
        "lifestyle": {
            "subject": "Monthly",
            "body": "Monthly body",
            "preheader": "A useful inbox preview",
        },
    }
    monkeypatch.setattr(
        runner,
        "read_local_sections",
        lambda year, month: local_sections,
    )
    monkeypatch.setattr(
        runner,
        "lock_due_sections",
        lambda **kwargs: local_sections,
    )
    monkeypatch.setattr(runner, "provision_drafts", lambda **kwargs: {})
    monkeypatch.setattr(runner, "apply_provider_report", lambda **kwargs: None)

    scheduled_periods = []

    def fake_schedule_month(**kwargs):
        scheduled_periods.append((kwargs["year"], kwargs["month"]))
        return {"Monthly": {"id": "send1", "status": "scheduled"}}

    monkeypatch.setattr(runner, "schedule_month", fake_schedule_month)

    assert runner.main([]) == 0

    capsys.readouterr()
    assert scheduled_periods == [(2026, 9)]


def test_runner_refuses_an_explicit_month_beyond_the_legacy_final_period(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setenv("SENDGRID_API_KEY", "test-key")
    monkeypatch.setenv("TWY_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(runner, "load_env", lambda: None)

    class FakeAPI:
        def __init__(self, api_key):
            self.api_key = api_key

        def user_email(self):
            return EXPECTED_ACCOUNT_EMAIL

    class FakeRegistry:
        @classmethod
        def load(cls, path):
            return cls()

    monkeypatch.setattr(runner, "SendGridAPI", FakeAPI)
    monkeypatch.setattr(runner, "SendGridRegistry", FakeRegistry)
    monkeypatch.setattr(
        runner,
        "habit_class_date",
        lambda year, month: (_ for _ in ()).throw(
            AssertionError("habit_class_date should not run")
        ),
    )
    monkeypatch.setattr(
        runner,
        "ensure_recording_draft",
        lambda year, month: (_ for _ in ()).throw(
            AssertionError("ensure_recording_draft should not run")
        ),
    )
    monkeypatch.setattr(
        runner,
        "read_local_sections",
        lambda year, month: (_ for _ in ()).throw(
            AssertionError("read_local_sections should not run")
        ),
    )
    monkeypatch.setattr(
        runner,
        "lock_due_sections",
        lambda **kwargs: (_ for _ in ()).throw(
            AssertionError("lock_due_sections should not run")
        ),
    )
    monkeypatch.setattr(
        runner,
        "SendGridCampaigns",
        lambda **kwargs: (_ for _ in ()).throw(
            AssertionError("campaigns should not be constructed")
        ),
    )
    monkeypatch.setattr(
        runner,
        "provision_drafts",
        lambda **kwargs: (_ for _ in ()).throw(
            AssertionError("provision_drafts should not run")
        ),
    )
    monkeypatch.setattr(
        runner,
        "schedule_month",
        lambda **kwargs: (_ for _ in ()).throw(
            AssertionError("schedule_month should not run")
        ),
    )
    monkeypatch.setattr(
        runner,
        "apply_provider_report",
        lambda **kwargs: (_ for _ in ()).throw(
            AssertionError("apply_provider_report should not run")
        ),
    )

    with pytest.raises(SystemExit) as excinfo:
        runner.main(["--month", "2026_10"])

    assert str(excinfo.value) == (
        "--month 2026_10 is beyond the legacy runner's final period "
        "2026_09. The campaign model owns everything after that."
    )


def test_runner_exits_zero_with_no_slack_post_when_no_periods_remain(
    monkeypatch,
    tmp_path,
    capsys,
):
    monkeypatch.setenv("SENDGRID_API_KEY", "test-key")
    monkeypatch.setenv("TWY_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(runner, "load_env", lambda: None)
    monkeypatch.setattr(runner, "datetime", FrozenDateTime)
    monkeypatch.setattr(
        runner, "_periods", lambda now: [(2026, 10), (2026, 11)]
    )

    class FakeAPI:
        def __init__(self, api_key):
            self.api_key = api_key

        def user_email(self):
            return EXPECTED_ACCOUNT_EMAIL

    class FakeRegistry:
        @classmethod
        def load(cls, path):
            return cls()

    monkeypatch.setattr(runner, "SendGridAPI", FakeAPI)
    monkeypatch.setattr(runner, "SendGridRegistry", FakeRegistry)
    monkeypatch.setattr(
        runner,
        "habit_class_date",
        lambda year, month: (_ for _ in ()).throw(
            AssertionError("habit_class_date should not run")
        ),
    )
    monkeypatch.setattr(
        runner,
        "SendGridCampaigns",
        lambda **kwargs: (_ for _ in ()).throw(
            AssertionError("campaigns should not be constructed")
        ),
    )

    slack_calls = []
    monkeypatch.setattr(
        runner,
        "post_slack",
        lambda *args, **kwargs: slack_calls.append((args, kwargs)),
    )

    assert runner.main([]) == 0

    captured = capsys.readouterr()
    assert captured.out.strip() == "{}"
    assert slack_calls == []
