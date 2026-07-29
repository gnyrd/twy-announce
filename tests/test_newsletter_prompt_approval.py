import json
from datetime import date
from pathlib import Path

import pytest

import habit_newsletter_prompt
from habit_newsletter_prompt import (
    NewsletterApprovalError,
    _format_recent_references,
    assemble_gentle_nudge_prompt,
    assemble_lifestyle_prompt,
    assemble_non_lifestyle_prompt,
    assemble_non_opener_prompt,
    assemble_ph1_prompt,
    assemble_ph2_prompt,
    assemble_reminder_prompt,
)


@pytest.fixture
def prompt_inputs(monkeypatch, tmp_path):
    guidance_path = tmp_path / "editorial_guidance.json"
    references_path = tmp_path / "approved_references.json"
    guidance_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "items": [
                    {
                        "review_id": "a" * 32,
                        "candidate_id": "1" * 32,
                        "audience_key": "non_lifestyle",
                        "kind": "voice",
                        "generated_excerpt": "Generated excerpt",
                        "sent_excerpt": "Tiff excerpt",
                        "guideline": "Approved guideline sentinel",
                        "completed_at": "2026-08-04T11:00:00-06:00",
                    },
                    {
                        "review_id": "b" * 32,
                        "candidate_id": "2" * 32,
                        "audience_key": "lifestyle",
                        "kind": "voice",
                        "generated_excerpt": "Other generated excerpt",
                        "sent_excerpt": "Other Tiff excerpt",
                        "guideline": "Other audience sentinel",
                        "completed_at": "2026-08-04T11:00:00-06:00",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    references_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "items": [
                    {
                        "review_id": "a" * 32,
                        "audience_key": "non_lifestyle",
                        "mailing_name": (
                            "2026_08: Yoga Habit: General Invitation"
                        ),
                        "subject": "Approved reference subject",
                        "body": "Approved reference sentinel",
                        "completed_at": "2026-08-04T11:00:00-06:00",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        habit_newsletter_prompt,
        "newsletter_editorial_guidance_path",
        lambda: guidance_path,
    )
    monkeypatch.setattr(
        habit_newsletter_prompt,
        "newsletter_approved_references_path",
        lambda: references_path,
    )
    monkeypatch.setattr(
        habit_newsletter_prompt,
        "get_habit_class_date",
        lambda year, month: date(year, month, 11),
    )
    return guidance_path, references_path


@pytest.fixture
def sample_overview():
    return {
        "title": "Healing into Wholeness",
        "teaching_notes": "Healing as wisdom",
        "physical_arc": "Hip Openers",
        "apex_pose": "L Shape Handstand at Wall",
        "upa": "Outer Spiral",
        "affirmation": "I share my wisdom.",
    }


@pytest.fixture
def sample_plans():
    plans = {
        f"2026-04-{day:02d}": {
            "title": f"Class {day}",
            "date": f"2026-04-{day:02d}",
        }
        for day in range(1, 20)
    }
    plans["2026-04-11"] = {
        "title": "The Yoga Habit",
        "date": "2026-04-11",
        "class_type": "Habit",
        "time": "09:00",
        "duration": "60",
        "description": "Free monthly class.",
        "affirmation": "I share my wisdom.",
    }
    return plans


def test_prompt_includes_only_matching_approved_inputs(
    prompt_inputs,
    sample_overview,
    sample_plans,
):
    prompt = assemble_non_lifestyle_prompt(
        sample_overview,
        sample_plans,
        2026,
        4,
    )

    assert "Approved guideline sentinel" in prompt
    assert "Approved reference sentinel" in prompt
    assert "Other audience sentinel" not in prompt


def test_prompt_requires_preheader_output(
    prompt_inputs,
    sample_overview,
    sample_plans,
):
    prompt = assemble_non_lifestyle_prompt(
        sample_overview,
        sample_plans,
        2026,
        4,
    )

    assert "subject, preheader, and body" in prompt
    assert "inbox preview" in prompt
    assert "do not repeat the subject" in prompt.lower()
    assert "Output ONLY a subject line and a body" not in prompt


@pytest.mark.parametrize(
    ("assembler", "subject_job"),
    [
        (assemble_lifestyle_prompt, "Member monthly story"),
        (assemble_non_lifestyle_prompt, "General invitation"),
        (assemble_non_opener_prompt, "Non-opener resend"),
        (assemble_reminder_prompt, "Registered-attendee reminder"),
        (assemble_gentle_nudge_prompt, "Gentle nudge"),
        (assemble_ph1_prompt, "First post-class follow-up"),
        (assemble_ph2_prompt, "Second post-class follow-up"),
    ],
)
def test_each_audience_prompt_has_its_own_subject_job(
    assembler,
    subject_job,
    prompt_inputs,
    sample_overview,
    sample_plans,
):
    prompt = assembler(sample_overview, sample_plans, 2026, 4)

    assert f"SUBJECT JOB: {subject_job}" in prompt
    assert "Use it EXACTLY in the SUBJECT line" not in prompt
    assert "Do not invent or rename the canonical monthly theme" in prompt


def test_recent_references_do_not_glob_newsletter_directory(
    monkeypatch,
    prompt_inputs,
):
    monkeypatch.setattr(
        Path,
        "glob",
        lambda *args, **kwargs: pytest.fail(
            "prompt assembly must not glob sent newsletters"
        ),
    )

    rendered = _format_recent_references("non_lifestyle")

    assert "Approved reference sentinel" in rendered


@pytest.mark.parametrize("invalid_state", ["missing", "malformed"])
def test_prompt_fails_closed_for_invalid_approval_indexes(
    invalid_state,
    prompt_inputs,
    sample_overview,
    sample_plans,
):
    guidance_path, references_path = prompt_inputs
    if invalid_state == "missing":
        guidance_path.unlink()
    else:
        references_path.write_text("{", encoding="utf-8")

    with pytest.raises(NewsletterApprovalError):
        assemble_non_lifestyle_prompt(
            sample_overview,
            sample_plans,
            2026,
            4,
        )


def test_valid_empty_indexes_keep_manual_rules_only(
    prompt_inputs,
    sample_overview,
    sample_plans,
):
    guidance_path, references_path = prompt_inputs
    empty = json.dumps({"schema_version": 1, "items": []})
    guidance_path.write_text(empty, encoding="utf-8")
    references_path.write_text(empty, encoding="utf-8")

    prompt = assemble_non_lifestyle_prompt(
        sample_overview,
        sample_plans,
        2026,
        4,
    )

    assert "VOICE NOTES" in prompt
    assert "APPROVED EDITORIAL GUIDANCE" not in prompt
    assert "APPROVED REFERENCE NEWSLETTERS" not in prompt


def test_malformed_approval_item_fails_closed(
    prompt_inputs,
    sample_overview,
    sample_plans,
):
    guidance_path, _ = prompt_inputs
    guidance_path.write_text(
        json.dumps({"schema_version": 1, "items": [{}]}),
        encoding="utf-8",
    )

    with pytest.raises(NewsletterApprovalError):
        assemble_non_lifestyle_prompt(
            sample_overview,
            sample_plans,
            2026,
            4,
        )
