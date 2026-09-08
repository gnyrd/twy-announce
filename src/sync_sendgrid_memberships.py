#!/usr/bin/env python3
"""Reconcile authoritative Marvelous memberships to exact SendGrid lists.

Members are keyed by their HeyMarvelous customer id, carried on the SendGrid
contact as twy_marvelous_customer_id and resolved from marvy.db each night.
When HeyMarvelous reports a known id under a new email address (its support
changed the student's address), the contact is renamed everywhere it lives
before the exact sync runs; see sendgrid_contact_rename. Run with --dry-run to
read everything and write nothing.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

import journey_enrollment
from marvelous_memberships import (
    MEMBER_ARCHIVE,
    MEMBER_YOGA_LIFESTYLE,
    PRODUCT_LISTS,
    load_active_rows_from_env,
)
from sendgrid_api import SendGridAPI
from sendgrid_campaigns import EXPECTED_ACCOUNT_EMAIL, SendGridRegistry
from sendgrid_contact_identity import (
    IDENTITY_FIELD,
    custom_field_ids,
    customer_ids_by_email,
    ensure_identity_field,
    identity_field_id,
)
from sendgrid_contact_rename import apply_rename, plan_renames, read_ledger
from sendgrid_contact_source import SOURCE_MEMBER_SYNC
from sendgrid_list_sync import ensure_list, sync_exact_list
from twy_paths import (
    journey_enrollments_db_path,
    load_env,
    marvy_db_path,
    sendgrid_contact_renames_path,
    sendgrid_registry_path,
)


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
            source=SOURCE_MEMBER_SYNC,
            source_detail=name,
        )
    return results


def attach_customer_ids(
    memberships: dict[str, list[dict]],
    ids: dict[str, str],
    field_id: str,
) -> tuple[dict[str, list[dict]], int, list[str]]:
    """Copy the memberships with each member's HeyMarvelous id as a custom field.

    Returns (memberships, stamped, unresolved). stamped counts the distinct
    addresses that carry an id and a field to put it in. unresolved lists the
    distinct addresses marvy.db does not know: it can lag HeyMarvelous by a
    day, so that is a count to watch, not an error.
    """
    stamped: set[str] = set()
    unresolved: set[str] = set()
    result: dict[str, list[dict]] = {}
    for name, contacts in memberships.items():
        rows = []
        for contact in contacts:
            copied = dict(contact)
            email = str(copied.get("email") or "").strip().lower()
            customer_id = ids.get(email)
            if not customer_id:
                if email:
                    unresolved.add(email)
            elif field_id:
                fields = dict(copied.get("custom_fields") or {})
                fields[field_id] = customer_id
                copied["custom_fields"] = fields
                stamped.add(email)
            rows.append(copied)
        result[name] = rows
    return result, len(stamped), sorted(unresolved)


def desired_customer_ids(
    memberships: dict[str, list[dict]],
    ids: dict[str, str],
) -> dict[str, str]:
    """Desired member email to customer id, across both member lists."""
    desired: dict[str, str] = {}
    for contacts in memberships.values():
        for contact in contacts:
            email = str(contact.get("email") or "").strip().lower()
            customer_id = ids.get(email)
            if email and customer_id:
                desired[email] = customer_id
    return desired


def listed_member_contacts(api, registry) -> list[dict]:
    """Every contact on either member list, with the identity field, once each."""
    seen: dict[str, dict] = {}
    for name in (MEMBER_YOGA_LIFESTYLE, MEMBER_ARCHIVE):
        try:
            list_id = registry.list_id(name)
        except KeyError:
            continue
        for row in api.list_contacts(list_id, fields=(IDENTITY_FIELD,)):
            seen.setdefault(str(row.get("id") or row.get("email")), row)
    return list(seen.values())


def rename_phase(
    api,
    registry,
    *,
    desired: dict[str, str],
    ledger_path,
    enrollments_path,
    identity_field: str,
    field_ids: dict[str, str],
    dry_run: bool,
) -> dict:
    """Rename every contact whose customer id now carries a different address.

    Runs before the exact sync, so the sync then finds the new address desired
    and present and the old one gone. A dry run plans and logs, writes nothing.
    """
    listed = listed_member_contacts(api, registry)
    plan = plan_renames(desired, listed, read_ledger(ledger_path))
    for customer_id, emails in plan.duplicates:
        log.warning(
            "customer %s is carried by %d contacts, untouched",
            customer_id,
            len(emails),
        )
    for email, listed_id, desired_id in plan.conflicts:
        log.warning(
            "a member address already carries customer %s while HeyMarvelous "
            "names it for %s, untouched",
            listed_id,
            desired_id,
        )
    for rename in plan.renames:
        log.info(
            "%srename planned for customer %s%s",
            "dry run: " if dry_run else "",
            rename.customer_id,
            " (resuming)" if rename.resume else "",
        )
    renamed = 0
    if not dry_run and plan.renames:
        connection = journey_enrollment.connect(enrollments_path)
        try:
            for rename in plan.renames:
                result = apply_rename(
                    api,
                    rename=rename,
                    suppression_group_id=registry.suppression_group_id,
                    enrollments=connection,
                    ledger_path=ledger_path,
                    identity_field_id=identity_field,
                    field_ids=field_ids,
                )
                if "skipped" not in result:
                    renamed += 1
        finally:
            connection.close()
    return {
        "planned": len(plan.renames),
        "renamed": renamed,
        "duplicates": len(plan.duplicates),
        "conflicts": len(plan.conflicts),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="read the report, marvy.db and SendGrid, write nothing, log the plan",
    )
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    load_env()
    api_key = os.getenv("SENDGRID_API_KEY", "")
    if not api_key:
        raise SystemExit("SENDGRID_API_KEY is not configured")

    api = SendGridAPI(api_key)
    if api.user_email() != EXPECTED_ACCOUNT_EMAIL:
        raise SystemExit("unexpected SendGrid account")

    registry = SendGridRegistry.load(
        sendgrid_registry_path()
    )
    rows = load_active_rows_from_env()
    memberships, unknown_products = normalize_active_memberships(rows)
    if unknown_products:
        log.warning(
            "ignored unrelated products: %s",
            ", ".join(sorted(unknown_products)),
        )
    if not any(memberships.values()):
        raise RuntimeError("no active TWY memberships parsed from report")

    ids = customer_ids_by_email(marvy_db_path())
    field_id = identity_field_id(api) if args.dry_run else ensure_identity_field(api)
    if not field_id:
        log.info(
            "identity field %s does not exist yet; a real run creates it",
            IDENTITY_FIELD,
        )
    memberships, stamped, unresolved = attach_customer_ids(memberships, ids, field_id)
    if unresolved:
        log.warning(
            "marvy.db has no customer id for %d member address(es)",
            len(unresolved),
        )
        log.debug("unresolved member addresses: %s", ", ".join(unresolved))

    renames = rename_phase(
        api,
        registry,
        desired=desired_customer_ids(memberships, ids),
        ledger_path=sendgrid_contact_renames_path(),
        enrollments_path=journey_enrollments_db_path(),
        identity_field=field_id,
        field_ids=custom_field_ids(api),
        dry_run=args.dry_run,
    )

    if args.dry_run:
        for name, contacts in memberships.items():
            log.info("dry run: %s would sync %d member(s)", name, len(contacts))
        log.info(
            "dry run: stamped=%d unresolved_ids=%d renames_planned=%d "
            "duplicates=%d conflicts=%d",
            stamped,
            len(unresolved),
            renames["planned"],
            renames["duplicates"],
            renames["conflicts"],
        )
        return 0

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
    log.info(
        "stamped=%d unresolved_ids=%d renames=%d duplicates=%d conflicts=%d",
        stamped,
        len(unresolved),
        renames["renamed"],
        renames["duplicates"],
        renames["conflicts"],
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
