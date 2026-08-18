#!/usr/bin/env python3
"""Publish provider neutral Marvelous membership activity to Slack."""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import json
import os
from pathlib import Path
import sqlite3
from typing import Callable

from marvelous_memberships import (
    ACTIVE_FIELDS,
    CANCELED_FIELDS,
    load_active_rows_from_env,
    load_canceled_rows_from_env,
    membership_program,
    write_report_snapshot,
)


DEFAULT_CHANNEL = "C0BH3142LNP"


@dataclass(frozen=True)
class MembershipPurchase:
    purchase_id: str
    email: str
    name: str
    product_name: str
    recurring_type: str | None
    amount_paid: str
    created: str
    subscription_id: str | None
    is_active: bool
    is_canceled: bool


@dataclass(frozen=True)
class ActivityEvent:
    kind: str
    key: str
    email: str
    name: str
    program: str
    occurred_at: str
    amount: str | None = None
    access_until: str | None = None


@dataclass(frozen=True)
class ActivityState:
    active_memberships: dict[str, dict[str, str]]
    processed_purchase_ids: frozenset[str]
    processed_cancellation_keys: frozenset[str]

    @classmethod
    def empty(cls) -> "ActivityState":
        return cls({}, frozenset(), frozenset())


def _pick(row: dict, *keys: str) -> str:
    for key in keys:
        value = str(row.get(key) or "").strip()
        if value:
            return value
    return ""


def _email(value: str) -> str:
    result = str(value or "").strip().lower()
    if not result or "@" not in result:
        raise ValueError("membership activity has invalid email")
    return result


def _membership_key(email: str, program: str) -> str:
    return f"{_email(email)}:{program}"


def active_memberships(rows: list[dict]) -> dict[str, dict[str, str]]:
    result: dict[str, dict[str, str]] = {}
    for row in rows:
        status = _pick(row, "Status", "status").lower()
        if status and status != "active":
            continue
        product = _pick(row, "Product Name", "product_name")
        program = membership_program(product)
        if not program:
            continue
        email = _email(_pick(row, "Email", "email"))
        key = _membership_key(email, program)
        first = _pick(row, "First Name", "first_name")
        last = _pick(row, "Last Name", "last_name")
        result[key] = {
            "email": email,
            "name": f"{first} {last}".strip() or email,
            "program": program,
            "created": _pick(row, "Created", "created"),
        }
    return dict(sorted(result.items()))


def _cancellation(row: dict) -> tuple[str, dict[str, str]] | None:
    program = membership_program(_pick(row, "product_name", "Product Name"))
    if not program:
        return None
    email = _email(_pick(row, "email", "Email"))
    canceled_at = _pick(row, "canceled_at", "Canceled At")
    if not canceled_at:
        raise ValueError("cancellation is missing canceled_at")
    first = _pick(row, "first_name", "First Name")
    last = _pick(row, "last_name", "Last Name")
    key = f"canceled:{email}:{program}:{canceled_at}"
    return key, {
        "email": email,
        "name": f"{first} {last}".strip() or email,
        "program": program,
        "occurred_at": canceled_at,
        "access_until": _pick(
            row,
            "subscription_active_until",
            "Subscription Active Until",
        ),
    }


def _is_paid_active_repeat(
    purchase: MembershipPurchase,
    *,
    seen_subscriptions: set[str],
) -> bool:
    if not membership_program(purchase.product_name):
        return False
    if not purchase.recurring_type or not purchase.subscription_id:
        return False
    try:
        paid = Decimal(str(purchase.amount_paid or 0))
    except InvalidOperation as exc:
        raise ValueError("membership purchase has invalid amount") from exc
    return (
        paid > 0
        and purchase.is_active
        and not purchase.is_canceled
        and purchase.subscription_id in seen_subscriptions
    )


def plan_activity(
    *,
    active_rows: list[dict],
    canceled_rows: list[dict],
    purchases: list[MembershipPurchase],
    state: ActivityState,
) -> tuple[list[ActivityEvent], ActivityState]:
    current_active = active_memberships(active_rows)
    events: list[ActivityEvent] = []

    for key in sorted(set(current_active) - set(state.active_memberships)):
        member = current_active[key]
        events.append(
            ActivityEvent(
                kind="Joined",
                key=(
                    f"joined:{member['email']}:{member['program']}:"
                    f"{member['created']}"
                ),
                email=member["email"],
                name=member["name"],
                program=member["program"],
                occurred_at=member["created"],
            )
        )

    processed_purchases = set(state.processed_purchase_ids)
    seen_subscriptions: set[str] = set()
    for purchase in sorted(
        purchases,
        key=lambda item: (item.created, item.purchase_id),
    ):
        program = membership_program(purchase.product_name)
        if not program:
            continue
        is_repeat = _is_paid_active_repeat(
            purchase,
            seen_subscriptions=seen_subscriptions,
        )
        if purchase.purchase_id not in processed_purchases and is_repeat:
            events.append(
                ActivityEvent(
                    kind="Renewed",
                    key=f"renewed:{purchase.purchase_id}",
                    email=_email(purchase.email),
                    name=purchase.name.strip() or _email(purchase.email),
                    program=program,
                    occurred_at=purchase.created,
                    amount=str(purchase.amount_paid),
                )
            )
        processed_purchases.add(purchase.purchase_id)
        if purchase.subscription_id:
            seen_subscriptions.add(purchase.subscription_id)

    processed_cancellations = set(state.processed_cancellation_keys)
    for row in canceled_rows:
        parsed = _cancellation(row)
        if not parsed:
            continue
        key, cancellation = parsed
        if key not in processed_cancellations:
            events.append(
                ActivityEvent(
                    kind="Canceled",
                    key=key,
                    email=cancellation["email"],
                    name=cancellation["name"],
                    program=cancellation["program"],
                    occurred_at=cancellation["occurred_at"],
                    access_until=cancellation["access_until"],
                )
            )
        processed_cancellations.add(key)

    next_state = ActivityState(
        active_memberships=current_active,
        processed_purchase_ids=frozenset(processed_purchases),
        processed_cancellation_keys=frozenset(processed_cancellations),
    )
    return events, next_state


def _short_date(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return value
    return parsed.strftime("%b %-d")


def _money(value: str) -> str:
    amount = Decimal(str(value))
    if amount == amount.to_integral():
        return f"${int(amount)}"
    return f"${amount.quantize(Decimal('0.01'))}"


def format_activity(
    events: list[ActivityEvent],
    *,
    customer_link: Callable[[str, str], str],
) -> str:
    lines: list[str] = []
    for event in events:
        person = customer_link(event.email, event.name)
        if event.kind == "Joined":
            details = f"{event.program}, joined {_short_date(event.occurred_at)}"
        elif event.kind == "Renewed":
            details = (
                f"{event.program}, {_money(event.amount or '0')}, "
                f"{_short_date(event.occurred_at)}"
            )
        elif event.kind == "Canceled":
            details = f"{event.program}, canceled {_short_date(event.occurred_at)}"
            if event.access_until:
                details += f", access until {_short_date(event.access_until)}"
        else:
            raise ValueError(f"unsupported member activity kind: {event.kind}")
        lines.append(f"*{event.kind}*: {person} ({details})")
    return "\n".join(lines)


def _state_payload(state: ActivityState) -> dict:
    return {
        "version": 1,
        "active_memberships": state.active_memberships,
        "processed_purchase_ids": sorted(state.processed_purchase_ids),
        "processed_cancellation_keys": sorted(
            state.processed_cancellation_keys
        ),
    }


def load_state(path: Path) -> ActivityState:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("version") != 1:
        raise ValueError("unsupported member activity state")
    return ActivityState(
        active_memberships={
            str(key): {str(k): str(v) for k, v in value.items()}
            for key, value in (payload.get("active_memberships") or {}).items()
        },
        processed_purchase_ids=frozenset(
            str(value) for value in payload.get("processed_purchase_ids") or []
        ),
        processed_cancellation_keys=frozenset(
            str(value)
            for value in payload.get("processed_cancellation_keys") or []
        ),
    )


def save_state(path: Path, state: ActivityState) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(_state_payload(state), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)
    os.chmod(path, 0o600)


def load_membership_purchases(
    database_path: Path,
    *,
    now: datetime | None = None,
    max_age_hours: float = 2,
) -> list[MembershipPurchase]:
    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    try:
        sync = connection.execute(
            "SELECT finished_at FROM sync_log "
            "WHERE tier = 'all' AND finished_at IS NOT NULL "
            "ORDER BY finished_at DESC LIMIT 1"
        ).fetchone()
        if not sync:
            raise RuntimeError("Marvelous full database sync is missing")
        finished = datetime.fromisoformat(str(sync["finished_at"]).replace("Z", "+00:00"))
        current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        age_hours = (current - finished.astimezone(timezone.utc)).total_seconds() / 3600
        if age_hours < 0 or age_hours > max_age_hours:
            raise RuntimeError(
                f"Marvelous full database sync is stale: {age_hours:.1f} hours old"
            )
        rows = connection.execute(
            """
            SELECT id, customer_email, customer_name, product_name,
                   recurring_type, amount_paid, created,
                   is_stripe_subscription, is_active, is_canceled
            FROM purchases
            WHERE product_name IN (?, ?, ?, ?)
            ORDER BY created, id
            """,
            (
                "The Yoga Lifestyle Membership",
                "Yoga Lifestyle",
                "The Archive",
                "TWY Archive",
            ),
        ).fetchall()
    finally:
        connection.close()
    return [
        MembershipPurchase(
            purchase_id=str(row["id"]),
            email=_email(row["customer_email"]),
            name=str(row["customer_name"] or "").strip(),
            product_name=str(row["product_name"] or "").strip(),
            recurring_type=(
                str(row["recurring_type"]).strip()
                if row["recurring_type"] is not None
                else None
            ),
            amount_paid=str(row["amount_paid"] or 0),
            created=str(row["created"] or ""),
            subscription_id=(
                str(row["is_stripe_subscription"]).strip()
                if row["is_stripe_subscription"] is not None
                else None
            ),
            is_active=bool(row["is_active"]),
            is_canceled=bool(row["is_canceled"]),
        )
        for row in rows
    ]


def _rows_from_csv(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def bootstrap_state(
    *,
    baseline_active_rows: list[dict],
    canceled_rows: list[dict],
    purchases: list[MembershipPurchase],
    purchase_cutoff: datetime,
) -> ActivityState:
    cutoff = purchase_cutoff.astimezone(timezone.utc)
    processed_purchases = {
        purchase.purchase_id
        for purchase in purchases
        if datetime.fromisoformat(purchase.created.replace("Z", "+00:00")).astimezone(
            timezone.utc
        ) < cutoff
    }
    processed_cancellations = set()
    for row in canceled_rows:
        parsed = _cancellation(row)
        if not parsed:
            continue
        key, value = parsed
        occurred = datetime.fromisoformat(
            value["occurred_at"].replace("Z", "+00:00")
        ).astimezone(timezone.utc)
        if occurred < cutoff:
            processed_cancellations.add(key)
    return ActivityState(
        active_memberships=active_memberships(baseline_active_rows),
        processed_purchase_ids=frozenset(processed_purchases),
        processed_cancellation_keys=frozenset(processed_cancellations),
    )


def post_activity(message: str, *, channel: str) -> bool:
    from twy_platform.slack import slack

    return bool(slack(message, channel=channel))


def customer_linker(database_path: Path) -> Callable[[str, str], str]:
    def link(email: str, name: str) -> str:
        connection = sqlite3.connect(database_path)
        try:
            row = connection.execute(
                "SELECT id FROM customers WHERE lower(email) = ?",
                (_email(email),),
            ).fetchone()
        finally:
            connection.close()
        if not row:
            return name
        return f"<https://app.heymarvelous.com/customers/{row[0]}|{name}>"

    return link


def apply_activity(
    *,
    active_rows: list[dict],
    canceled_rows: list[dict],
    purchases: list[MembershipPurchase],
    state_path: Path,
    dry_run: bool,
    channel: str,
    initial_state: ActivityState | None = None,
    customer_link: Callable[[str, str], str] | None = None,
) -> tuple[list[ActivityEvent], ActivityState]:
    state = initial_state or load_state(state_path)
    events, next_state = plan_activity(
        active_rows=active_rows,
        canceled_rows=canceled_rows,
        purchases=purchases,
        state=state,
    )
    message = format_activity(
        events,
        customer_link=customer_link or (lambda email, name: name),
    )
    if dry_run:
        if message:
            print(message)
        return events, next_state
    if message and not post_activity(message, channel=channel):
        raise RuntimeError("Slack delivery was not confirmed")
    save_state(state_path, next_state)
    return events, next_state


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Publish Marvelous member activity to Slack",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--baseline-active-snapshot", type=Path)
    parser.add_argument("--purchase-cutoff")
    parser.add_argument("--max-db-age-hours", type=float, default=2)
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    from twy_paths import data_root, hm_subscriptions_dir, load_env, marvy_db_path

    load_env()
    now = datetime.now(timezone.utc)
    active_rows = load_active_rows_from_env()
    canceled_rows = load_canceled_rows_from_env()
    active_path = write_report_snapshot(
        active_rows,
        reports_dir=hm_subscriptions_dir(),
        prefix="active_subscriptions",
        fields=ACTIVE_FIELDS,
        now=now,
    )
    canceled_path = write_report_snapshot(
        canceled_rows,
        reports_dir=hm_subscriptions_dir(),
        prefix="canceled_subscriptions",
        fields=CANCELED_FIELDS,
        now=now,
    )
    database_path = marvy_db_path()
    purchases = load_membership_purchases(
        database_path,
        now=now,
        max_age_hours=args.max_db_age_hours,
    )
    state_path = data_root() / "member_activity" / "state.json"

    initial_state = None
    if not state_path.exists():
        if not args.baseline_active_snapshot or not args.purchase_cutoff:
            raise RuntimeError(
                "member activity state is missing; explicit baseline and cutoff required"
            )
        if not args.baseline_active_snapshot.exists():
            raise RuntimeError("baseline active snapshot does not exist")
        initial_state = bootstrap_state(
            baseline_active_rows=_rows_from_csv(args.baseline_active_snapshot),
            canceled_rows=canceled_rows,
            purchases=purchases,
            purchase_cutoff=datetime.fromisoformat(
                args.purchase_cutoff.replace("Z", "+00:00")
            ),
        )

    events, next_state = apply_activity(
        active_rows=active_rows,
        canceled_rows=canceled_rows,
        purchases=purchases,
        state_path=state_path,
        dry_run=args.dry_run,
        channel=os.getenv("SLACK_MOVEMENT_CHANNEL", DEFAULT_CHANNEL),
        initial_state=initial_state,
        customer_link=customer_linker(database_path),
    )
    print(
        json.dumps(
            {
                "active_snapshot": str(active_path),
                "canceled_snapshot": str(canceled_path),
                "events": {
                    kind: sum(1 for event in events if event.kind == kind)
                    for kind in ("Joined", "Renewed", "Canceled")
                },
                "active_memberships": len(next_state.active_memberships),
                "processed_purchases": len(next_state.processed_purchase_ids),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
