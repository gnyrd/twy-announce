"""Deterministic newsletter comparisons and approved editorial inputs."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from diff_loop import detect_structural_signals, diff_phrases
from twy_paths import (
    newsletter_approved_references_path,
    newsletter_diffs_dir,
    newsletter_editorial_guidance_path,
    newsletter_reviews_dir,
)
from twy_platform import locked_create, locked_write


ALLOWED_AUDIENCES = {"lifestyle", "non_lifestyle"}
ALLOWED_PROVIDERS = {"sendgrid", "historical"}
ALLOWED_KINDS = {
    "subject",
    "voice",
    "structure",
    "call to action",
    "formatting",
    "factual correction",
    "one time correction",
}
REUSABLE_KINDS = ALLOWED_KINDS - {
    "factual correction",
    "one time correction",
}
OPAQUE_ID = re.compile(r"^[0-9a-f]{32}$")
CONTENT_DIGEST = re.compile(r"^[0-9a-f]{64}$")
ONE_TIME_PATTERN = re.compile(
    r"(https?://|www\.|\$\d|\b\d{1,2}:\d{2}\b|"
    r"\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)"
    r"[a-z]*\b|\b\d{4}\b)",
    re.IGNORECASE,
)
STRUCTURE_GUIDANCE = {
    "h1_removed": (
        "Prefer Tiff's paragraph flow when she removes a generated heading."
    ),
    "rhetorical_question_subject_replaced": (
        "Use rhetorical question subjects sparingly when Tiff replaces one."
    ),
    "oppositional_opener_replaced": (
        "Prefer Tiff's direct lived experience opener over a generated "
        "oppositional opener."
    ),
}
HISTORICAL_MAILING_PARTS = {
    "lifestyle": ("Yoga Lifestyle", "Monthly"),
    "non_lifestyle": ("Yoga Habit", "General Invitation"),
}


def _normalized(value: str) -> str:
    return " ".join(value.split())


def _opaque_id(*values: str) -> str:
    source = "\x1f".join(_normalized(value) for value in values)
    return hashlib.sha256(source.encode("utf-8")).hexdigest()[:32]


def _content_digest(
    generated_subject: str,
    generated_body: str,
    sent_subject: str,
    sent_body: str,
) -> str:
    source = json.dumps(
        {
            "generated": {
                "subject": generated_subject,
                "body": generated_body,
            },
            "sent": {
                "subject": sent_subject,
                "body": sent_body,
            },
        },
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


def _excerpt(value: str, limit: int = 240) -> str:
    compact = _normalized(value)
    if len(compact) <= limit:
        return compact
    shortened = compact[: limit + 1].rsplit(" ", 1)[0]
    return shortened.rstrip(" ,;:") + "…"


def _candidate(
    *,
    review_id: str,
    kind: str,
    generated_excerpt: str,
    sent_excerpt: str,
    guideline: str,
) -> dict[str, str]:
    generated_excerpt = _excerpt(generated_excerpt)
    sent_excerpt = _excerpt(sent_excerpt)
    candidate_id = _opaque_id(
        review_id,
        kind,
        generated_excerpt,
        sent_excerpt,
        guideline,
    )
    return {
        "candidate_id": candidate_id,
        "kind": kind,
        "generated_excerpt": generated_excerpt,
        "sent_excerpt": sent_excerpt,
        "guideline": guideline,
    }


def _build_candidates_from_diffs(
    *,
    review_id: str,
    generated_subject: str,
    generated_body: str,
    sent_subject: str,
    sent_body: str,
    removed: list[str],
    added: list[str],
    signals: list[str],
) -> list[dict[str, str]]:
    candidates: list[dict[str, str]] = []
    if _normalized(generated_subject) != _normalized(sent_subject):
        candidates.append(
            _candidate(
                review_id=review_id,
                kind="subject",
                generated_excerpt=generated_subject,
                sent_excerpt=sent_subject,
                guideline=(
                    "Use Tiff's sent subject as the preferred contrast "
                    "example for this audience."
                ),
            )
        )

    pair_count = max(len(removed), len(added))
    for index in range(pair_count):
        generated = removed[index] if index < len(removed) else ""
        sent = added[index] if index < len(added) else ""
        combined = f"{generated} {sent}"
        kind = (
            "one time correction"
            if ONE_TIME_PATTERN.search(combined)
            else "voice"
        )
        guideline = (
            "Treat this as a correction for this mailing only."
            if kind == "one time correction"
            else (
                "Prefer Tiff's sent wording over the generated wording in "
                "similar newsletter passages."
            )
        )
        candidates.append(
            _candidate(
                review_id=review_id,
                kind=kind,
                generated_excerpt=generated,
                sent_excerpt=sent,
                guideline=guideline,
            )
        )

    for signal in sorted(set(signals)):
        guideline = STRUCTURE_GUIDANCE.get(signal)
        if signal.startswith("somatic_marketing_phrase_removed:"):
            phrase = signal.split(":", 1)[1]
            guideline = (
                f'Use the generated phrase "{phrase}" sparingly when Tiff '
                "removes it."
            )
        if guideline:
            candidates.append(
                _candidate(
                    review_id=review_id,
                    kind="structure",
                    generated_excerpt=generated_body,
                    sent_excerpt=sent_body,
                    guideline=guideline,
                )
            )

    unique = {item["candidate_id"]: item for item in candidates}
    return [unique[candidate_id] for candidate_id in sorted(unique)]


def _build_candidates(
    *,
    review_id: str,
    generated_subject: str,
    generated_body: str,
    sent_subject: str,
    sent_body: str,
) -> list[dict[str, str]]:
    removed, added = diff_phrases(generated_body, sent_body)
    signals = detect_structural_signals(
        generated_subject,
        generated_body,
        sent_subject,
        sent_body,
    )
    return _build_candidates_from_diffs(
        review_id=review_id,
        generated_subject=generated_subject,
        generated_body=generated_body,
        sent_subject=sent_subject,
        sent_body=sent_body,
        removed=removed,
        added=added,
        signals=signals,
    )


def build_review_record(
    *,
    mailing_name: str,
    audience_key: str,
    captured_at: str,
    provider_single_send_id: str,
    provider_design_id: str | None,
    provider_ui_url: str | None,
    generated_subject: str,
    generated_body: str,
    sent_subject: str,
    sent_body: str,
) -> dict[str, Any]:
    if audience_key not in ALLOWED_AUDIENCES:
        raise ValueError("unsupported newsletter audience")
    digest = _content_digest(
        generated_subject,
        generated_body,
        sent_subject,
        sent_body,
    )
    review_id = _opaque_id(provider_single_send_id, digest)
    return validate_comparison(
        {
            "schema_version": 1,
            "review_id": review_id,
            "mailing_name": mailing_name,
            "audience_key": audience_key,
            "captured_at": captured_at,
            "provider": "sendgrid",
            "provider_single_send_id": provider_single_send_id,
            "provider_design_id": provider_design_id,
            "provider_ui_url": provider_ui_url,
            "content_digest": digest,
            "generated": {
                "subject": generated_subject,
                "body": generated_body,
            },
            "sent": {
                "subject": sent_subject,
                "body": sent_body,
            },
            "candidates": _build_candidates(
                review_id=review_id,
                generated_subject=generated_subject,
                generated_body=generated_body,
                sent_subject=sent_subject,
                sent_body=sent_body,
            ),
        }
    )


def validate_comparison(record: dict[str, Any]) -> dict[str, Any]:
    if record.get("schema_version") != 1:
        raise ValueError("unsupported comparison schema")
    if not OPAQUE_ID.fullmatch(str(record.get("review_id", ""))):
        raise ValueError("invalid review identifier")
    if record.get("audience_key") not in ALLOWED_AUDIENCES:
        raise ValueError("invalid audience key")
    datetime.fromisoformat(record["captured_at"])
    if record.get("provider") not in ALLOWED_PROVIDERS:
        raise ValueError("invalid provider")
    if record["provider"] == "sendgrid" and not record.get(
        "provider_single_send_id"
    ):
        raise ValueError("SendGrid comparison is missing Single Send identity")
    if not CONTENT_DIGEST.fullmatch(str(record.get("content_digest", ""))):
        raise ValueError("invalid content digest")
    candidate_ids: set[str] = set()
    for candidate in record.get("candidates", []):
        candidate_id = str(candidate.get("candidate_id", ""))
        if not OPAQUE_ID.fullmatch(candidate_id):
            raise ValueError("invalid candidate identifier")
        if candidate_id in candidate_ids:
            raise ValueError("duplicate candidate identifier")
        candidate_ids.add(candidate_id)
        if candidate.get("kind") not in ALLOWED_KINDS:
            raise ValueError("invalid candidate kind")
    return record


def validate_approval(record: dict[str, Any]) -> dict[str, Any]:
    if record.get("schema_version") != 1 or record.get("status") != "done":
        raise ValueError("invalid approval state")
    if not OPAQUE_ID.fullmatch(str(record.get("review_id", ""))):
        raise ValueError("invalid review identifier")
    if not CONTENT_DIGEST.fullmatch(str(record.get("content_digest", ""))):
        raise ValueError("invalid content digest")
    datetime.fromisoformat(record["completed_at"])
    if not isinstance(record.get("approved_as_reference"), bool):
        raise ValueError("invalid reference decision")
    approved = record.get("approved_candidate_ids")
    rejected = record.get("rejected_candidate_ids")
    if not isinstance(approved, list) or not isinstance(rejected, list):
        raise ValueError("invalid candidate decisions")
    decisions = approved + rejected
    if any(not OPAQUE_ID.fullmatch(str(value)) for value in decisions):
        raise ValueError("invalid candidate decision identifier")
    if len(set(approved)) != len(approved) or len(set(rejected)) != len(rejected):
        raise ValueError("duplicate candidate decision identifier")
    if set(approved) & set(rejected):
        raise ValueError("candidate cannot be approved and rejected")
    return record


def _approval_path(comparison: dict[str, Any]) -> Path:
    captured = datetime.fromisoformat(comparison["captured_at"])
    return (
        newsletter_reviews_dir()
        / f"{captured.year:04d}_{captured.month:02d}"
        / f'{comparison["review_id"]}.json'
    )


def ensure_empty_review_is_done(
    comparison: dict[str, Any],
) -> dict[str, Any] | None:
    comparison = validate_comparison(comparison)
    reusable_ids = {
        item["candidate_id"]
        for item in comparison["candidates"]
        if item["kind"] in REUSABLE_KINDS
    }
    if reusable_ids:
        return None
    captured = datetime.fromisoformat(comparison["captured_at"])
    approval = {
        "schema_version": 1,
        "review_id": comparison["review_id"],
        "status": "done",
        "content_digest": comparison["content_digest"],
        "completed_at": captured.isoformat(),
        "approved_candidate_ids": [],
        "rejected_candidate_ids": [],
        "approved_as_reference": False,
    }
    path = _approval_path(comparison)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        locked_create(
            path,
            json.dumps(approval, indent=2, sort_keys=True) + "\n",
        )
    except FileExistsError:
        return validate_approval(json.loads(path.read_text(encoding="utf-8")))
    return approval


def _historical_review(record: dict[str, Any]) -> dict[str, Any]:
    audience = record.get("audience")
    if audience not in HISTORICAL_MAILING_PARTS:
        raise ValueError("historical record is not a newsletter audience")
    month = str(record["month"])
    datetime.fromisoformat(record["captured_at"])
    generated = record["tweee_submitted"]
    sent = record["tiff_sent"]
    digest = _content_digest(
        generated["subject"],
        generated["body_md"],
        sent["subject"],
        sent["body_md"],
    )
    review_id = _opaque_id("historical", month, audience, digest)
    program, purpose = HISTORICAL_MAILING_PARTS[audience]
    mailing_name = f'{program}: {month.replace("-", "_")}: {purpose}'
    return validate_comparison(
        {
            "schema_version": 1,
            "review_id": review_id,
            "mailing_name": mailing_name,
            "audience_key": audience,
            "captured_at": record["captured_at"],
            "provider": "historical",
            "provider_single_send_id": None,
            "provider_design_id": None,
            "provider_ui_url": None,
            "content_digest": digest,
            "generated": {
                "subject": generated["subject"],
                "body": generated["body_md"],
            },
            "sent": {
                "subject": sent["subject"],
                "body": sent["body_md"],
            },
            "candidates": _build_candidates_from_diffs(
                review_id=review_id,
                generated_subject=generated["subject"],
                generated_body=generated["body_md"],
                sent_subject=sent["subject"],
                sent_body=sent["body_md"],
                removed=record.get("removed_phrases", []),
                added=record.get("added_phrases", []),
                signals=record.get("structural_signals", []),
            ),
        }
    )


def import_historical_comparisons() -> list[Path]:
    written: list[Path] = []
    for source in sorted(newsletter_diffs_dir().glob("*/*.diff.json")):
        raw = json.loads(source.read_text(encoding="utf-8"))
        if raw.get("audience") not in HISTORICAL_MAILING_PARTS:
            continue
        review = _historical_review(raw)
        destination = source.parent / f'{review["review_id"]}.review.json'
        try:
            locked_create(
                destination,
                json.dumps(review, indent=2, sort_keys=True) + "\n",
            )
        except FileExistsError:
            existing = validate_comparison(
                json.loads(destination.read_text(encoding="utf-8"))
            )
            if existing["content_digest"] != review["content_digest"]:
                raise ValueError(f"historical review collision: {destination}")
        ensure_empty_review_is_done(review)
        written.append(destination)
    return written


def compile_approved_inputs() -> tuple[dict[str, Any], dict[str, Any]]:
    guidance_items: list[dict[str, Any]] = []
    reference_items: list[dict[str, Any]] = []
    for comparison_path in sorted(
        newsletter_diffs_dir().glob("*/*.review.json")
    ):
        comparison = validate_comparison(
            json.loads(comparison_path.read_text(encoding="utf-8"))
        )
        approval_path = _approval_path(comparison)
        if not approval_path.exists():
            continue
        approval = validate_approval(
            json.loads(approval_path.read_text(encoding="utf-8"))
        )
        if approval["review_id"] != comparison["review_id"]:
            raise ValueError(f"approval review mismatch: {approval_path}")
        if approval["content_digest"] != comparison["content_digest"]:
            continue
        by_id = {
            item["candidate_id"]: item
            for item in comparison["candidates"]
        }
        eligible_ids = {
            candidate_id
            for candidate_id, item in by_id.items()
            if item["kind"] in REUSABLE_KINDS
        }
        decided_ids = set(approval["approved_candidate_ids"]) | set(
            approval["rejected_candidate_ids"]
        )
        if decided_ids - set(by_id):
            raise ValueError(f"unknown candidate decision: {approval_path}")
        if eligible_ids - decided_ids:
            raise ValueError(
                f"incomplete candidate decisions: {approval_path}"
            )
        for candidate_id in approval["approved_candidate_ids"]:
            candidate = by_id.get(candidate_id)
            if candidate and candidate["kind"] in REUSABLE_KINDS:
                guidance_items.append(
                    {
                        "review_id": comparison["review_id"],
                        "candidate_id": candidate_id,
                        "audience_key": comparison["audience_key"],
                        "kind": candidate["kind"],
                        "generated_excerpt": candidate["generated_excerpt"],
                        "sent_excerpt": candidate["sent_excerpt"],
                        "guideline": candidate["guideline"],
                        "completed_at": approval["completed_at"],
                    }
                )
        if approval["approved_as_reference"]:
            reference_items.append(
                {
                    "review_id": comparison["review_id"],
                    "audience_key": comparison["audience_key"],
                    "mailing_name": comparison["mailing_name"],
                    "subject": comparison["sent"]["subject"],
                    "body": comparison["sent"]["body"],
                    "completed_at": approval["completed_at"],
                }
            )

    guidance = {
        "schema_version": 1,
        "items": sorted(
            guidance_items,
            key=lambda item: (
                item["completed_at"],
                item["candidate_id"],
            ),
        ),
    }
    references = {
        "schema_version": 1,
        "items": sorted(
            reference_items,
            key=lambda item: (
                item["completed_at"],
                item["review_id"],
            ),
        ),
    }
    locked_write(
        newsletter_editorial_guidance_path(),
        json.dumps(guidance, indent=2, sort_keys=True) + "\n",
    )
    locked_write(
        newsletter_approved_references_path(),
        json.dumps(references, indent=2, sort_keys=True) + "\n",
    )
    return guidance, references
