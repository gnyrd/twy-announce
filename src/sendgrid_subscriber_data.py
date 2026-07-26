#!/usr/bin/env python3
"""Capture the daily count of contacts in the locked subscriber list."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys

from sendgrid_api import SendGridAPI
from sendgrid_campaigns import EXPECTED_ACCOUNT_EMAIL, SendGridRegistry
from sendgrid_mailings import EMAIL_SUBSCRIBED
from twy_paths import data_root, load_env, twy_root


def collect_snapshot(*, api, registry, captured_at: str) -> dict:
    list_id = registry.list_id(EMAIL_SUBSCRIBED)
    subscriber_count = api.list_contact_count(list_id)
    return {
        "captured_at": captured_at,
        "list_name": EMAIL_SUBSCRIBED,
        "subscriber_count": subscriber_count,
    }


def save_snapshot(
    snapshot: dict,
    *,
    date_string: str,
    history_dir: Path,
) -> Path:
    history_dir.mkdir(parents=True, exist_ok=True)
    destination = history_dir / f"{date_string}.json"
    destination.write_text(json.dumps(snapshot, indent=2) + "\n")
    return destination


def main() -> int:
    load_env()
    api_key = os.getenv("SENDGRID_API_KEY", "")
    if not api_key:
        raise SystemExit("SENDGRID_API_KEY is not configured")
    api = SendGridAPI(api_key)
    if api.user_email() != EXPECTED_ACCOUNT_EMAIL:
        raise SystemExit("unexpected SendGrid account")
    registry = SendGridRegistry.load(
        data_root() / "sendgrid" / "production_objects.json"
    )
    now = datetime.now(timezone.utc)
    snapshot = collect_snapshot(
        api=api,
        registry=registry,
        captured_at=now.isoformat().replace("+00:00", "Z"),
    )
    destination = save_snapshot(
        snapshot,
        date_string=now.date().isoformat(),
        history_dir=twy_root() / "announce" / "data" / "email" / "history",
    )
    print(
        f"Saved {snapshot['subscriber_count']} email subscribers to "
        f"{destination}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
