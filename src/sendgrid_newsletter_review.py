#!/usr/bin/env python3
"""Collect a completed SendGrid newsletter for editorial review."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from diff_loop import (
    _convert_html_body_to_md as html_to_markdown,
    _parse_md_file as parse_generated_markdown,
)
from newsletter_editorial_review import (
    build_review_record,
    ensure_empty_review_is_done,
    validate_comparison,
)
from sendgrid_api import SendGridAPI
from twy_paths import load_env, newsletter_diffs_dir
from twy_platform import locked_create


SENDGRID_SINGLE_SENDS_URL = "https://mc.sendgrid.com/single-sends"


def collect_review(
    *,
    api: SendGridAPI,
    single_send_id: str,
    generated_path: Path,
    mailing_name: str,
    audience_key: str,
    captured_at: str,
) -> Path:
    single_send = api.get_single_send(single_send_id)
    if single_send.get("status") != "triggered":
        raise ValueError("Single Send must be triggered before review")
    if single_send.get("name") != mailing_name:
        raise ValueError("Single Send name does not match mailing name")

    generated_subject, generated_body = parse_generated_markdown(
        generated_path
    )
    if not generated_subject or not generated_body:
        raise ValueError("generated newsletter is missing a subject or body")

    email_config = single_send.get("email_config") or {}
    design_id = email_config.get("design_id")
    design = api.get_design(design_id) if design_id else {}
    sent_subject = design.get("subject") or email_config.get("subject")
    if not sent_subject:
        raise ValueError("SendGrid content is missing a subject")
    sent_body = html_to_markdown(
        design.get("html_content")
        or email_config.get("html_content")
        or ""
    )
    if not sent_body.strip():
        raise ValueError("SendGrid content is missing a body")

    record = build_review_record(
        mailing_name=mailing_name,
        audience_key=audience_key,
        captured_at=captured_at,
        provider_single_send_id=single_send_id,
        provider_design_id=design_id,
        provider_ui_url=SENDGRID_SINGLE_SENDS_URL,
        generated_subject=generated_subject,
        generated_body=generated_body,
        sent_subject=sent_subject,
        sent_body=sent_body,
    )
    destination = newsletter_diffs_dir() / captured_at[:7]
    destination.mkdir(parents=True, exist_ok=True)
    path = destination / f'{record["review_id"]}.review.json'
    serialized = json.dumps(record, indent=2, sort_keys=True) + "\n"
    try:
        locked_create(path, serialized)
    except FileExistsError:
        existing = validate_comparison(
            json.loads(path.read_text(encoding="utf-8"))
        )
        if existing != record:
            raise ValueError(f"newsletter review collision: {path}")
    ensure_empty_review_is_done(record)
    return path


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Collect one completed SendGrid newsletter review"
    )
    parser.add_argument("--single-send-id", required=True)
    parser.add_argument("--generated", required=True, type=Path)
    parser.add_argument("--mailing-name", required=True)
    parser.add_argument(
        "--audience-key",
        required=True,
        choices=("lifestyle", "non_lifestyle"),
    )
    parser.add_argument("--captured-at", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    load_env()
    api_key = os.getenv("SENDGRID_API_KEY", "")
    if not api_key:
        raise SystemExit(
            "missing required canonical configuration: SENDGRID_API_KEY"
        )
    path = collect_review(
        api=SendGridAPI(api_key),
        single_send_id=arguments.single_send_id,
        generated_path=arguments.generated,
        mailing_name=arguments.mailing_name,
        audience_key=arguments.audience_key,
        captured_at=arguments.captured_at,
    )
    record = json.loads(path.read_text(encoding="utf-8"))
    print(
        json.dumps(
            {
                "path": str(path),
                "review_id": record["review_id"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
