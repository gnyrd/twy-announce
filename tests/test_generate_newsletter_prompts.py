from datetime import datetime
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))
import generate_newsletter_prompts as prompts


class FrozenDatetime(datetime):
    current = datetime(2026, 7, 26, 9, 0)

    @classmethod
    def now(cls, tz=None):
        value = cls.current
        return value.replace(tzinfo=tz) if tz is not None else value


def _isolate_paths(monkeypatch, tmp_path):
    monkeypatch.setattr(
        prompts,
        "newsletter_path",
        lambda year, month, audience: tmp_path / f"{audience}.md",
    )
    monkeypatch.setattr(
        prompts,
        "prompt_path",
        lambda year, month, audience: tmp_path / f"prompt_{audience}.txt",
    )


def test_insufficient_plans_nudges_tiff_without_failing_the_job(
    monkeypatch, tmp_path
):
    FrozenDatetime.current = datetime(2026, 7, 26, 9, 0)
    _isolate_paths(monkeypatch, tmp_path)
    monkeypatch.setattr(prompts, "datetime", FrozenDatetime)
    monkeypatch.setattr(
        prompts,
        "load_month_overview",
        lambda month: {"title": "Fluid Motion"},
    )
    plans = {
        f"2026-08-{day:02d}": {"title": f"Class {day}"}
        for day in (3, 4, 6, 8)
    }
    monkeypatch.setattr(
        prompts,
        "load_plans_for_month",
        lambda year, month: plans,
    )
    monkeypatch.setattr(
        prompts,
        "check_coverage",
        lambda plans, year, month: (_ for _ in ()).throw(
            ValueError(
                "insufficient class plans: 4 plans for 2026-08 "
                "(need at least 7)"
            )
        ),
    )
    messages = []
    monkeypatch.setattr(
        prompts,
        "SLACK_STATUS_CHANNEL",
        "status-newsletters",
    )
    monkeypatch.setattr(
        prompts,
        "post_slack",
        lambda channel, message: messages.append((channel, message)),
    )

    assert prompts.main() is None
    assert messages == [
        (
            "status-newsletters",
            ":clipboard: Tiff action needed for August 2026: "
            "3 more class plans required (4 of 7). "
            "Newsletter draft generation remains blocked until all 7 are ready.",
        )
    ]


def test_missing_overview_is_content_readiness_not_job_failure(
    monkeypatch, tmp_path
):
    FrozenDatetime.current = datetime(2026, 7, 26, 9, 0)
    _isolate_paths(monkeypatch, tmp_path)
    monkeypatch.setattr(prompts, "datetime", FrozenDatetime)
    monkeypatch.setattr(prompts, "load_month_overview", lambda month: None)
    messages = []
    monkeypatch.setattr(
        prompts,
        "SLACK_STATUS_CHANNEL",
        "status-newsletters",
    )
    monkeypatch.setattr(
        prompts,
        "post_slack",
        lambda channel, message: messages.append((channel, message)),
    )

    assert prompts.main() is None
    assert messages == [
        (
            "status-newsletters",
            ":clipboard: Tiff action needed for August 2026: "
            "the monthly overview is missing. "
            "Newsletter draft generation remains blocked.",
        )
    ]


def test_expected_preparation_window_noop_is_silent(
    monkeypatch, tmp_path, capsys
):
    _isolate_paths(monkeypatch, tmp_path)
    monkeypatch.setattr(prompts, "datetime", FrozenDatetime)
    FrozenDatetime.current = datetime(2026, 7, 24, 9, 0)
    monkeypatch.setattr(
        prompts,
        "post_slack",
        lambda channel, message: (_ for _ in ()).throw(
            AssertionError("preparation window must not post")
        ),
    )

    assert prompts.main() is None
    assert capsys.readouterr().out == ""
