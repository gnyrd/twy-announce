#!/usr/bin/env python3
"""Reconcile authoritative Marvelous memberships to exact SendGrid lists."""

from __future__ import annotations

import logging
import os
from pathlib import Path
import sys

from marvelous_report_jwt import ReportJWTError, fetch_report_rows
import requests

from sendgrid_api import SendGridAPI
from sendgrid_campaigns import EXPECTED_ACCOUNT_EMAIL, SendGridRegistry
from sendgrid_list_sync import ensure_list, sync_exact_list
from twy_paths import data_root, load_env


MEMBER_YOGA_LIFESTYLE = "Member: Yoga Lifestyle"
MEMBER_ARCHIVE = "Member: Archive"
PRODUCT_LISTS = {
    "The Yoga Lifestyle Membership": MEMBER_YOGA_LIFESTYLE,
    "Yoga Lifestyle": MEMBER_YOGA_LIFESTYLE,
    "The Archive": MEMBER_ARCHIVE,
    "TWY Archive": MEMBER_ARCHIVE,
}

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("sync_sendgrid_memberships")


def _value(row: dict, *keys: str) -> str:
    for key in keys:
        value = str(row.get(key) or "").strip()
        if value:
            return value
    return ""


def normalize_active_memberships(
    rows: list[dict],
) -> tuple[dict[str, list[dict]], set[str]]:
    by_list: dict[str, dict[str, dict]] = {
        MEMBER_YOGA_LIFESTYLE: {},
        MEMBER_ARCHIVE: {},
    }
    unknown_products = set()
    profiles: dict[str, dict] = {}

    for row in rows:
        status = _value(row, "Status", "status").lower()
        if status and status != "active":
            continue
        email = _value(row, "Email", "email").lower()
        if not email or "@" not in email:
            continue
        product = _value(row, "Product Name", "product_name", "product")
        list_name = PRODUCT_LISTS.get(product)
        if not list_name:
            if product:
                unknown_products.add(product)
            continue

        profile = profiles.setdefault(email, {"email": email})
        first_name = _value(row, "First Name", "first_name", "firstName")
        last_name = _value(row, "Last Name", "last_name", "lastName")
        if first_name and not profile.get("first_name"):
            profile["first_name"] = first_name
        if last_name and not profile.get("last_name"):
            profile["last_name"] = last_name
        by_list[list_name][email] = profile

    return (
        {
            name: [dict(contacts[email]) for email in sorted(contacts)]
            for name, contacts in by_list.items()
        },
        unknown_products,
    )


def load_active_rows(
    report_id: int,
    report_category: str,
) -> list[dict]:
    try:
        rows = fetch_report_rows(
            report_id=report_id,
            category=report_category,
            force_refresh=True,
        )
    except (ReportJWTError, requests.RequestException) as exc:
        raise RuntimeError(f"Marvelous active membership report failed: {exc}") from exc
    if not rows:
        raise RuntimeError("Marvelous active membership report returned no rows")
    return rows


def sync_membership_lists(
    *,
    api,
    registry,
    memberships: dict[str, list[dict]],
) -> dict[str, dict]:
    results = {}
    for name in (MEMBER_YOGA_LIFESTYLE, MEMBER_ARCHIVE):
        list_id = ensure_list(api, registry, name)
        results[name] = sync_exact_list(
            api=api,
            destination_list_id=list_id,
            desired_contacts=memberships.get(name, []),
            additive_list_ids=None,
        )
    return results


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
    report_id = int(os.getenv("MARVELOUS_ACTIVE_SUBS_REPORT_ID", "15"))
    report_category = os.getenv(
        "MARVELOUS_ACTIVE_SUBS_REPORT_CATEGORY",
        "users",
    )
    rows = load_active_rows(report_id, report_category)
    memberships, unknown_products = normalize_active_memberships(rows)
    if unknown_products:
        log.warning(
            "ignored unrelated products: %s",
            ", ".join(sorted(unknown_products)),
        )
    if not any(memberships.values()):
        raise RuntimeError("no active TWY memberships parsed from report")

    results = sync_membership_lists(
        api=api,
        registry=registry,
        memberships=memberships,
    )
    for name, result in results.items():
        log.info(
            "%s: desired=%d previous=%d removed=%d",
            name,
            result["desired"],
            result["previous"],
            result["removed"],
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
