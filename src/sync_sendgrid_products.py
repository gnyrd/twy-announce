#!/usr/bin/env python3
"""Synchronize Marvelous product acquisitions to SendGrid."""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
import json
import os
from pathlib import Path
import sqlite3
import sys
import time

from journey_enrollment import (
    connect as connect_enrollments,
    plan_enrollment,
    record_enrollments,
)
from sendgrid_contact_source import SOURCE_PRODUCT_PURCHASE, stamp_new_contacts
from sendgrid_list_sync import ensure_list
from sendgrid_mailings import product_attribute_name
from twy_platform.journeys import active_journeys_by_product


@dataclass(frozen=True)
class ProductPurchase:
    purchase_id: str
    customer_id: str
    email: str
    product_id: str
    product_name: str
    recurring_type: str | None
    amount_paid: Decimal | int | float | str
    created: str

    @property
    def pair(self) -> str:
        return f"{self.customer_id}:{self.product_id}"


@dataclass(frozen=True)
class ProductSyncPlan:
    contacts_by_product: dict[str, tuple[dict[str, str], ...]]
    subscribe_emails: frozenset[str] = frozenset()
    renewed_consent_emails: frozenset[str] = frozenset()
    blocked: dict[str, str] = field(default_factory=dict)
    # Journeys a first acquisition starts. Empty on backfill, which must
    # never mail the back catalogue.
    enrollments: tuple = ()


@dataclass(frozen=True)
class ProductSyncState:
    processed_purchase_ids: frozenset[str]
    acquired_pairs: frozenset[str]
    product_list_names: dict[str, str]


def load_state(path: Path) -> ProductSyncState:
    if not path.exists():
        return ProductSyncState(frozenset(), frozenset(), {})
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("version") != 1:
        raise ValueError("unsupported product sync state")
    return ProductSyncState(
        processed_purchase_ids=frozenset(
            str(value) for value in payload.get("processed_purchase_ids") or []
        ),
        acquired_pairs=frozenset(
            str(value) for value in payload.get("acquired_pairs") or []
        ),
        product_list_names={
            str(key): str(value)
            for key, value in (payload.get("product_list_names") or {}).items()
        },
    )


def save_state(path: Path, state: ProductSyncState) -> None:
    _write_private_state(path, state)


def _normalized_email(value: str) -> str:
    email = str(value).strip().lower()
    if not email or "@" not in email:
        raise ValueError("purchase has invalid email")
    return email


def _product_names(purchases: list[ProductPurchase]) -> dict[str, str]:
    names: dict[str, str] = {}
    owners: dict[str, str] = {}
    for purchase in purchases:
        name = product_attribute_name(purchase.product_name)
        existing = names.get(purchase.product_id)
        if existing and existing != name:
            continue
        owner = owners.get(name)
        if owner and owner != purchase.product_id:
            raise ValueError("product list name collision")
        names[purchase.product_id] = name
        owners[name] = purchase.product_id
    return names


def plan_historical_backfill(
    purchases: list[ProductPurchase],
    *,
    subscribed_emails: set[str],
) -> tuple[ProductSyncPlan, ProductSyncState]:
    subscribers = {_normalized_email(email) for email in subscribed_emails}
    contacts: dict[str, dict[str, dict[str, str]]] = {}
    for purchase in purchases:
        email = _normalized_email(purchase.email)
        if email in subscribers:
            contacts.setdefault(purchase.product_id, {})[email] = {"email": email}
    plan = ProductSyncPlan(
        contacts_by_product={
            product_id: tuple(by_email[email] for email in sorted(by_email))
            for product_id, by_email in sorted(contacts.items())
        }
    )
    state = ProductSyncState(
        processed_purchase_ids=frozenset(
            purchase.purchase_id for purchase in purchases
        ),
        acquired_pairs=frozenset(purchase.pair for purchase in purchases),
        product_list_names=_product_names(purchases),
    )
    return plan, state


def load_historical_purchases(database_path: Path) -> list[ProductPurchase]:
    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            """
            SELECT id, customer_id, customer_email, product_id, product_name,
                   recurring_type, amount_paid, created
            FROM purchases
            WHERE id IS NOT NULL
              AND customer_id IS NOT NULL
              AND customer_email IS NOT NULL
              AND product_id IS NOT NULL
              AND product_name IS NOT NULL
              AND created IS NOT NULL
            ORDER BY created, id
            """
        ).fetchall()
    finally:
        connection.close()
    return [
        ProductPurchase(
            purchase_id=str(row["id"]),
            customer_id=str(row["customer_id"]),
            email=_normalized_email(row["customer_email"]),
            product_id=str(row["product_id"]),
            product_name=str(row["product_name"]).strip(),
            recurring_type=(
                str(row["recurring_type"]).strip()
                if row["recurring_type"] is not None
                else None
            ),
            amount_paid=str(
                row["amount_paid"] if row["amount_paid"] is not None else 0
            ),
            created=str(row["created"]),
        )
        for row in rows
    ]


def _purchase_from_api(payload: dict) -> ProductPurchase:
    customer = payload.get("customer") or {}
    product = payload.get("product") or {}
    return ProductPurchase(
        purchase_id=str(payload.get("id") or ""),
        customer_id=str(customer.get("id") or ""),
        email=_normalized_email(customer.get("email") or ""),
        product_id=str(product.get("id") or ""),
        product_name=str(product.get("product_name") or "").strip(),
        recurring_type=(
            str(product.get("recurring_type")).strip()
            if product.get("recurring_type") is not None
            else None
        ),
        amount_paid=str(payload.get("amount_paid") or 0),
        created=str(payload.get("created") or ""),
    )


def fetch_incremental_purchases(
    client,
    *,
    processed_purchase_ids: set[str] | frozenset[str],
    max_pages: int = 50,
) -> list[ProductPurchase]:
    processed = set(processed_purchase_ids)
    purchases: list[ProductPurchase] = []
    for page in range(1, max_pages + 1):
        response = client.session.get(
            f"{client.BASE_URL}/api/purchases",
            params={"page": page},
            headers=client._get_auth_headers(),
            timeout=15,
        )
        response.raise_for_status()
        payload = response.json()
        rows = payload.get("results") or []
        ids = {str(row.get("id") or "") for row in rows}
        purchases.extend(
            _purchase_from_api(row)
            for row in rows
            if str(row.get("id") or "") not in processed
        )
        if rows and ids <= processed:
            break
        if not payload.get("next"):
            break
    else:
        raise RuntimeError("Marvelous purchase polling exceeded page safety limit")
    return sorted(purchases, key=lambda item: (item.created, item.purchase_id))


def _paid_nonrecurring(purchase: ProductPurchase) -> bool:
    recurring = str(purchase.recurring_type or "").strip()
    try:
        amount = Decimal(str(purchase.amount_paid or 0))
    except Exception as exc:
        raise ValueError("purchase has invalid amount") from exc
    return not recurring and amount > 0


def _merge_product_name(
    names: dict[str, str],
    purchase: ProductPurchase,
) -> None:
    if purchase.product_id in names:
        return
    name = product_attribute_name(purchase.product_name)
    owner = next(
        (product_id for product_id, value in names.items() if value == name),
        None,
    )
    if owner and owner != purchase.product_id:
        raise ValueError("product list name collision")
    names[purchase.product_id] = name


def _journey_for(journeys: dict, product_id) -> dict | None:
    """The active journey bound to this product, or None for most products."""
    try:
        key = int(str(product_id).strip())
    except (TypeError, ValueError):
        return None
    return journeys.get(key)


def plan_incremental_sync(
    purchases: list[ProductPurchase],
    *,
    state: ProductSyncState,
    subscribed_emails: set[str],
    suppressed_emails: set[str],
    cleaned_emails: set[str],
    bounced_emails: set[str],
    journeys_by_product: dict | None = None,
    now: datetime | None = None,
) -> tuple[ProductSyncPlan, ProductSyncState]:
    subscribers = {_normalized_email(email) for email in subscribed_emails}
    suppressed = {_normalized_email(email) for email in suppressed_emails}
    cleaned = {_normalized_email(email) for email in cleaned_emails}
    bounced = {_normalized_email(email) for email in bounced_emails}
    processed = set(state.processed_purchase_ids)
    acquired = set(state.acquired_pairs)
    names = dict(state.product_list_names)
    contacts: dict[str, dict[str, dict[str, str]]] = {}
    subscribe: set[str] = set()
    renewed: set[str] = set()
    blocked: dict[str, str] = {}
    journeys = journeys_by_product or {}
    moment = now or datetime.now(timezone.utc)
    enrollments: list = []

    for purchase in sorted(purchases, key=lambda item: item.created):
        if purchase.purchase_id in processed:
            continue
        _merge_product_name(names, purchase)
        email = _normalized_email(purchase.email)
        first_acquisition = purchase.pair not in acquired
        consent_event = first_acquisition or _paid_nonrecurring(purchase)

        if email in cleaned:
            blocked[email] = "cleaned"
        elif email in bounced:
            blocked[email] = "bounced"
        else:
            already_subscribed = email in subscribers and email not in suppressed
            if consent_event or already_subscribed:
                contacts.setdefault(purchase.product_id, {})[email] = {
                    "email": email,
                }
            if consent_event:
                subscribe.add(email)
                if email in suppressed:
                    renewed.add(email)
            # A journey starts on acquiring the product, never on the
            # recurring charge that keeps it, so a member is welcomed once.
            journey = (
                _journey_for(journeys, purchase.product_id)
                if first_acquisition
                else None
            )
            if journey is not None:
                enrollment = plan_enrollment(
                    journey,
                    email=email,
                    customer_id=purchase.customer_id,
                    product_id=purchase.product_id,
                    purchase_id=purchase.purchase_id,
                    purchased_at=purchase.created,
                    now=moment,
                )
                if enrollment is not None:
                    enrollments.append(enrollment)

        processed.add(purchase.purchase_id)
        acquired.add(purchase.pair)

    plan = ProductSyncPlan(
        contacts_by_product={
            product_id: tuple(by_email[email] for email in sorted(by_email))
            for product_id, by_email in sorted(contacts.items())
        },
        subscribe_emails=frozenset(subscribe),
        renewed_consent_emails=frozenset(renewed),
        blocked=dict(sorted(blocked.items())),
        enrollments=tuple(enrollments),
    )
    next_state = ProductSyncState(
        processed_purchase_ids=frozenset(processed),
        acquired_pairs=frozenset(acquired),
        product_list_names=names,
    )
    return plan, next_state


def _plan_counts(plan: ProductSyncPlan, *, dry_run: bool) -> dict[str, int | bool]:
    return {
        "products": len(plan.contacts_by_product),
        "contacts": sum(len(contacts) for contacts in plan.contacts_by_product.values()),
        "subscribed": len(plan.subscribe_emails),
        "renewed_consent": len(plan.renewed_consent_emails),
        "blocked": len(plan.blocked),
        "enrolled": len(plan.enrollments),
        "dry_run": dry_run,
    }


def _wait_for_suppression_removal(
    *, api, suppression_group_id: int, emails: list[str]
) -> None:
    attempts = 16
    for attempt in range(attempts):
        remaining = api.search_group_suppressions(
            suppression_group_id,
            emails,
        )
        if not remaining:
            return
        if attempt < attempts - 1:
            time.sleep(1)
    raise RuntimeError("renewed consent suppression removal failed")


def apply_product_plan(
    *,
    api,
    registry,
    plan: ProductSyncPlan,
    next_state: ProductSyncState,
    state_path: Path,
    evidence_path: Path,
    enrollments_db_path: Path,
    dry_run: bool,
) -> dict[str, int | bool]:
    if dry_run:
        return _plan_counts(plan, dry_run=True)

    subscribed_list_id = registry.list_id("Email: Subscribed")
    product_list_ids: dict[str, str] = {}
    for product_id in sorted(plan.contacts_by_product):
        product_list_ids[product_id] = ensure_list(
            api,
            registry,
            next_state.product_list_names[product_id],
        )

    for email in sorted(plan.renewed_consent_emails):
        api.remove_group_suppression(registry.suppression_group_id, email)
    if plan.renewed_consent_emails:
        _wait_for_suppression_removal(
            api=api,
            suppression_group_id=registry.suppression_group_id,
            emails=sorted(plan.renewed_consent_emails),
        )

    jobs: list[str] = []
    for product_id, contacts in sorted(plan.contacts_by_product.items()):
        jobs.append(
            api.upsert_contacts(
                [subscribed_list_id, product_list_ids[product_id]],
                stamp_new_contacts(
                    api,
                    list(contacts),
                    source=SOURCE_PRODUCT_PURCHASE,
                    detail=next_state.product_list_names[product_id],
                ),
            )
        )
    for job_id in jobs:
        api.wait_contact_job(job_id, timeout_s=300)

    for product_id, contacts in sorted(plan.contacts_by_product.items()):
        expected_ids = {subscribed_list_id, product_list_ids[product_id]}
        emails = [contact["email"] for contact in contacts]
        for batch in _chunks(emails, 100):
            actual = api.contacts_by_emails(batch)
            for email in batch:
                contact = actual.get(email) or {}
                if not expected_ids.issubset(
                    set(contact.get("list_ids") or [])
                ):
                    raise RuntimeError("membership verification failed")

    # Enrollments land after the contact is verifiably on the list and
    # before the state write. Fail here and the purchase stays unprocessed,
    # so the next run retries it; succeed and crash before the state write
    # and the retry is a no-op, because the pair is already enrolled.
    enrolled = 0
    if plan.enrollments:
        connection = connect_enrollments(enrollments_db_path)
        try:
            enrolled = record_enrollments(connection, plan.enrollments)
        finally:
            connection.close()

    counts = _plan_counts(plan, dry_run=False)
    # The plan says who a journey would start for; this says who it started
    # for. A replayed purchase plans one and records none.
    counts["enrolled"] = enrolled
    evidence = {
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "state_purchase_count": len(next_state.processed_purchase_ids),
        "renewed_consent_emails": sorted(plan.renewed_consent_emails),
        "blocked": plan.blocked,
        "enrolled_pairs": sorted(
            f"{item.journey_id}:{item.email}" for item in plan.enrollments
        ),
        "counts": counts,
    }
    _append_private_json(evidence_path, evidence)
    _write_private_state(state_path, next_state)
    return counts


def _chunks(values: list[str], size: int) -> list[list[str]]:
    return [values[index:index + size] for index in range(0, len(values), size)]


def run_sync(
    *,
    mode: str,
    dry_run: bool,
    api,
    registry,
    marvelous_client,
    database_path: Path,
    cleaned_path: Path,
    state_path: Path,
    evidence_path: Path,
    journeys_directory: Path,
    enrollments_db_path: Path,
) -> dict[str, int | bool]:
    subscribed_list_id = registry.list_id("Email: Subscribed")
    if mode == "backfill":
        subscribed_emails = {
            _normalized_email(contact.get("email") or "")
            for contact in api.list_contacts(subscribed_list_id)
        }
        purchases = load_historical_purchases(database_path)
        plan, next_state = plan_historical_backfill(
            purchases,
            subscribed_emails=subscribed_emails,
        )
    elif mode == "incremental":
        current_state = load_state(state_path)
        if not current_state.processed_purchase_ids:
            raise RuntimeError("product sync backfill must run before incremental mode")
        purchases = fetch_incremental_purchases(
            marvelous_client,
            processed_purchase_ids=current_state.processed_purchase_ids,
        )
        emails = sorted({_normalized_email(purchase.email) for purchase in purchases})
        contacts = api.contacts_by_emails(emails) if emails else {}
        subscribed_emails = {
            email
            for email, contact in contacts.items()
            if subscribed_list_id in set(contact.get("list_ids") or [])
        }
        suppressed_emails = (
            api.search_group_suppressions(registry.suppression_group_id, emails)
            if emails
            else set()
        )
        bounced_emails = {
            email for email in emails if api.get_bounce(email) is not None
        }
        cleaned_emails = load_cleaned_emails(cleaned_path)
        plan, next_state = plan_incremental_sync(
            purchases,
            state=current_state,
            subscribed_emails=subscribed_emails,
            suppressed_emails=suppressed_emails,
            cleaned_emails=cleaned_emails,
            bounced_emails=bounced_emails,
            journeys_by_product=active_journeys_by_product(
                journeys_directory
            ),
        )
    else:
        raise ValueError("unsupported product sync mode")
    return apply_product_plan(
        api=api,
        registry=registry,
        plan=plan,
        next_state=next_state,
        state_path=state_path,
        evidence_path=evidence_path,
        enrollments_db_path=enrollments_db_path,
        dry_run=dry_run,
    )


def load_cleaned_emails(path: Path) -> set[str]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("cleaned denylist must be a list")
    return {
        _normalized_email(item.get("email") or "")
        for item in payload
        if isinstance(item, dict)
    }


def _state_payload(state: ProductSyncState) -> dict:
    return {
        "version": 1,
        "processed_purchase_ids": sorted(state.processed_purchase_ids),
        "acquired_pairs": sorted(state.acquired_pairs),
        "product_list_names": dict(sorted(state.product_list_names.items())),
    }


def _prepare_private_parent(path: Path) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)


def _write_private_state(path: Path, state: ProductSyncState) -> None:
    _prepare_private_parent(path)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(_state_payload(state), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)
    os.chmod(path, 0o600)


def _append_private_json(path: Path, payload: dict) -> None:
    _prepare_private_parent(path)
    descriptor = os.open(
        path,
        os.O_WRONLY | os.O_APPEND | os.O_CREAT,
        0o600,
    )
    try:
        os.write(
            descriptor,
            (json.dumps(payload, sort_keys=True) + "\n").encode("utf-8"),
        )
    finally:
        os.close(descriptor)
    os.chmod(path, 0o600)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Synchronize Marvelous product acquisitions to SendGrid",
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--backfill", action="store_true")
    mode.add_argument("--incremental", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser


def _marvelous_client():
    sys.path.insert(0, "/root/twy/marvy")
    sys.path.insert(0, "/root/twy/classes/scripts")
    from marvy.client import Client
    from sync import get_token

    return Client(auth_token=get_token())


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    from sendgrid_api import SendGridAPI
    from sendgrid_campaigns import EXPECTED_ACCOUNT_EMAIL, SendGridRegistry
    from twy_paths import (
        journey_enrollments_db_path,
        journeys_dir,
        load_env,
        marvy_db_path,
        sendgrid_cleaned_denylist_path,
        sendgrid_dir,
        sendgrid_registry_path,
    )

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
    result = run_sync(
        mode="backfill" if args.backfill else "incremental",
        dry_run=args.dry_run,
        api=api,
        registry=registry,
        marvelous_client=None if args.backfill else _marvelous_client(),
        database_path=marvy_db_path(),
        cleaned_path=sendgrid_cleaned_denylist_path(),
        state_path=sendgrid_dir() / "product_sync_state.json",
        evidence_path=(
            sendgrid_dir() / "product_consent_events.jsonl"
        ),
        journeys_directory=journeys_dir(),
        enrollments_db_path=journey_enrollments_db_path(),
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
