import json

import pytest

import newsletter_editorial_review
from newsletter_editorial_review import (
    build_review_record,
    compile_approved_inputs,
    ensure_empty_review_is_done,
    import_historical_comparisons,
)


def _patch_locations(monkeypatch, tmp_path):
    diffs = tmp_path / "newsletter-diffs"
    reviews = tmp_path / "newsletter_reviews"
    monkeypatch.setattr(newsletter_editorial_review, "newsletter_diffs_dir", lambda: diffs)
    monkeypatch.setattr(newsletter_editorial_review, "newsletter_reviews_dir", lambda: reviews)
    monkeypatch.setattr(
        newsletter_editorial_review,
        "newsletter_editorial_guidance_path",
        lambda: reviews / "editorial_guidance.json",
    )
    monkeypatch.setattr(
        newsletter_editorial_review,
        "newsletter_approved_references_path",
        lambda: reviews / "approved_references.json",
    )
    return diffs, reviews


def test_identical_content_produces_stable_review_and_candidate_ids():
    arguments = {
        "mailing_name": "Yoga Habit: 2026_08: General Invitation",
        "audience_key": "non_lifestyle",
        "captured_at": "2026-08-04T10:15:00-06:00",
        "provider_single_send_id": "sg-single-send",
        "provider_design_id": "sg-design",
        "provider_ui_url": "https://mc.sendgrid.com/single-sends",
        "generated_subject": "Come to Yoga Habit",
        "generated_body": "Join us. Register now.",
        "sent_subject": "A gentle invitation",
        "sent_body": "You are warmly invited. Save your place.",
    }

    first = build_review_record(**arguments)
    second = build_review_record(**arguments)

    assert first["review_id"] == second["review_id"]
    assert [item["candidate_id"] for item in first["candidates"]] == [
        item["candidate_id"] for item in second["candidates"]
    ]
    assert len(first["review_id"]) == 32


def test_compiler_includes_only_done_reusable_approvals(monkeypatch, tmp_path):
    diffs, reviews = _patch_locations(monkeypatch, tmp_path)
    comparison = {
        "schema_version": 1,
        "review_id": "a" * 32,
        "mailing_name": "Yoga Habit: 2026_08: General Invitation",
        "audience_key": "non_lifestyle",
        "captured_at": "2026-08-04T10:15:00-06:00",
        "provider": "sendgrid",
        "provider_single_send_id": "single-send",
        "provider_design_id": "design",
        "provider_ui_url": "https://mc.sendgrid.com/single-sends",
        "content_digest": "d" * 64,
        "generated": {"subject": "Generated", "body": "Generated body"},
        "sent": {"subject": "Sent", "body": "Approved reference"},
        "candidates": [
            {
                "candidate_id": "1" * 32,
                "kind": "voice",
                "generated_excerpt": "Generated phrase",
                "sent_excerpt": "Tiff phrase",
                "guideline": "Approved guideline",
            },
            {
                "candidate_id": "2" * 32,
                "kind": "voice",
                "generated_excerpt": "Rejected generated phrase",
                "sent_excerpt": "Rejected Tiff phrase",
                "guideline": "Rejected guideline",
            },
            {
                "candidate_id": "3" * 32,
                "kind": "one time correction",
                "generated_excerpt": "August 5",
                "sent_excerpt": "August 12",
                "guideline": "Use the corrected class date",
            },
            {
                "candidate_id": "4" * 32,
                "kind": "voice",
                "generated_excerpt": "**Same words**",
                "sent_excerpt": "** Same words**",
                "guideline": "Formatting noise must not become guidance",
            },
        ],
    }
    comparison_path = diffs / "2026-08" / f'{"a" * 32}.review.json'
    comparison_path.parent.mkdir(parents=True)
    comparison_path.write_text(json.dumps(comparison), encoding="utf-8")
    approval_path = reviews / "2026_08" / f'{"a" * 32}.json'
    approval_path.parent.mkdir(parents=True)
    approval_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "review_id": "a" * 32,
                "status": "done",
                "content_digest": "d" * 64,
                "completed_at": "2026-08-04T11:00:00-06:00",
                "approved_candidate_ids": [
                    "1" * 32,
                    "3" * 32,
                    "4" * 32,
                ],
                "rejected_candidate_ids": ["2" * 32],
                "approved_as_reference": True,
            }
        ),
        encoding="utf-8",
    )
    pending = dict(comparison)
    pending["review_id"] = "b" * 32
    pending["content_digest"] = "e" * 64
    (diffs / "2026-08" / f'{"b" * 32}.review.json').write_text(
        json.dumps(pending),
        encoding="utf-8",
    )

    guidance, references = compile_approved_inputs()

    assert [item["candidate_id"] for item in guidance["items"]] == ["1" * 32]
    assert [item["review_id"] for item in references["items"]] == ["a" * 32]
    assert guidance["schema_version"] == 1
    assert references["schema_version"] == 1


def test_compiler_rejects_incomplete_reusable_decisions(monkeypatch, tmp_path):
    diffs, reviews = _patch_locations(monkeypatch, tmp_path)
    comparison = build_review_record(
        mailing_name="Yoga Lifestyle: 2026_08: Monthly",
        audience_key="lifestyle",
        captured_at="2026-08-03T09:39:00-06:00",
        provider_single_send_id="single-send",
        provider_design_id="design",
        provider_ui_url=None,
        generated_subject="Generated subject",
        generated_body="Generated body.",
        sent_subject="Sent subject",
        sent_body="Sent body.",
    )
    comparison_path = diffs / "2026-08" / f'{comparison["review_id"]}.review.json'
    comparison_path.parent.mkdir(parents=True)
    comparison_path.write_text(json.dumps(comparison), encoding="utf-8")
    approval_path = reviews / "2026_08" / f'{comparison["review_id"]}.json'
    approval_path.parent.mkdir(parents=True)
    approval_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "review_id": comparison["review_id"],
                "status": "done",
                "content_digest": comparison["content_digest"],
                "completed_at": "2026-08-03T10:00:00-06:00",
                "approved_candidate_ids": [],
                "rejected_candidate_ids": [],
                "approved_as_reference": False,
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="incomplete candidate decisions"):
        compile_approved_inputs()


def test_empty_reusable_review_is_completed_automatically(monkeypatch, tmp_path):
    _, reviews = _patch_locations(monkeypatch, tmp_path)
    comparison = build_review_record(
        mailing_name="Yoga Lifestyle: 2026_08: Monthly",
        audience_key="lifestyle",
        captured_at="2026-08-03T09:39:00-06:00",
        provider_single_send_id="single-send",
        provider_design_id="design",
        provider_ui_url=None,
        generated_subject="Same subject",
        generated_body="Same body.",
        sent_subject="Same subject",
        sent_body="Same body.",
    )

    approval = ensure_empty_review_is_done(comparison)

    assert approval["status"] == "done"
    assert approval["approved_as_reference"] is False
    assert (
        reviews / "2026_08" / f'{comparison["review_id"]}.json'
    ).exists()


def test_historical_import_converts_only_newsletter_audiences(monkeypatch, tmp_path):
    diffs, _ = _patch_locations(monkeypatch, tmp_path)
    period = diffs / "2026-07"
    period.mkdir(parents=True)
    base = {
        "month": "2026-07",
        "captured_at": "2026-07-25T09:00:00-06:00",
        "tweee_submitted": {"subject": "Generated", "body_md": "Generated body."},
        "tiff_sent": {"subject": "Sent", "body_md": "Sent body."},
        "removed_phrases": ["Generated body."],
        "added_phrases": ["Sent body."],
        "structural_signals": [],
    }
    lifestyle = dict(base, audience="lifestyle")
    reminder = dict(base, audience="reminder")
    (period / "lifestyle.diff.json").write_text(json.dumps(lifestyle), encoding="utf-8")
    (period / "reminder.diff.json").write_text(json.dumps(reminder), encoding="utf-8")

    written = import_historical_comparisons()

    assert len(written) == 1
    record = json.loads(written[0].read_text(encoding="utf-8"))
    assert record["provider"] == "historical"
    assert record["mailing_name"] == "Yoga Lifestyle: 2026_07: Monthly"
    assert not list(period.glob("*reminder*.review.json"))


def test_markdown_only_body_differences_produce_no_candidates():
    record = build_review_record(
        mailing_name="Yoga Lifestyle: 2026_07: Monthly",
        audience_key="lifestyle",
        captured_at="2026-07-25T03:00:22-06:00",
        provider_single_send_id="single-send",
        provider_design_id=None,
        provider_ui_url=None,
        generated_subject="Same subject",
        generated_body=(
            "**There will not be a class in July** Details.\n\n"
            r"\_\_\_\_\_\_\_\_\_\_"
        ),
        sent_subject="Same subject",
        sent_body=(
            "** There will not be a class in July** Details.\n\n"
            "__________"
        ),
    )

    assert record["candidates"] == []


def test_historical_import_refreshes_pending_formatting_noise(
    monkeypatch,
    tmp_path,
):
    diffs, reviews = _patch_locations(monkeypatch, tmp_path)
    period = diffs / "2026-07"
    period.mkdir(parents=True)
    raw = {
        "audience": "lifestyle",
        "month": "2026-07",
        "captured_at": "2026-07-25T03:00:22-06:00",
        "tweee_submitted": {
            "subject": "Same subject",
            "body_md": "**Same words**",
        },
        "tiff_sent": {
            "subject": "Same subject",
            "body_md": "** Same words**",
        },
        "removed_phrases": ["**Same words**"],
        "added_phrases": ["** Same words**"],
        "structural_signals": [],
    }
    (period / "lifestyle.diff.json").write_text(
        json.dumps(raw),
        encoding="utf-8",
    )
    comparison = newsletter_editorial_review._historical_review(raw)
    destination = period / f'{comparison["review_id"]}.review.json'
    stale = dict(comparison)
    stale["candidates"] = [
        {
            "candidate_id": "f" * 32,
            "kind": "voice",
            "generated_excerpt": "**Same words**",
            "sent_excerpt": "** Same words**",
            "guideline": "Prefer Tiff wording",
        }
    ]
    destination.write_text(json.dumps(stale), encoding="utf-8")

    import_historical_comparisons()

    refreshed = json.loads(destination.read_text(encoding="utf-8"))
    approval_path = (
        reviews / "2026_07" / f'{comparison["review_id"]}.json'
    )
    approval = json.loads(approval_path.read_text(encoding="utf-8"))
    assert refreshed["candidates"] == []
    assert approval["status"] == "done"
    assert approval["approved_candidate_ids"] == []
    assert approval["approved_as_reference"] is False


def test_invalid_audience_is_rejected():
    with pytest.raises(ValueError, match="unsupported newsletter audience"):
        build_review_record(
            mailing_name="Other: 2026_08: Monthly",
            audience_key="other",
            captured_at="2026-08-03T09:39:00-06:00",
            provider_single_send_id="single-send",
            provider_design_id=None,
            provider_ui_url=None,
            generated_subject="Generated",
            generated_body="Generated",
            sent_subject="Sent",
            sent_body="Sent",
        )
