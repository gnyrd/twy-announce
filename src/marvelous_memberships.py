"""Shared Marvelous membership report source and snapshot helpers."""

from __future__ import annotations

import csv
from datetime import datetime, timezone
import os
from pathlib import Path
from typing import Iterable

from marvelous_report_jwt import ReportJWTError, fetch_report_rows
import requests


MEMBER_YOGA_LIFESTYLE = "Member: Yoga Lifestyle"
MEMBER_ARCHIVE = "Member: Archive"

PRODUCT_LISTS = {
    "The Yoga Lifestyle Membership": MEMBER_YOGA_LIFESTYLE,
    "Yoga Lifestyle": MEMBER_YOGA_LIFESTYLE,
    "The Archive": MEMBER_ARCHIVE,
    "TWY Archive": MEMBER_ARCHIVE,
}

PROGRAMS = {
    "The Yoga Lifestyle Membership": "Yoga Lifestyle",
    "Yoga Lifestyle": "Yoga Lifestyle",
    "The Archive": "Archive",
    "TWY Archive": "Archive",
}

ACTIVE_FIELDS = (
    "Billing Cycle",
    "Created",
    "Email",
    "First Name",
    "Last Name",
    "Paid",
    "Price",
    "Product Name",
    "Renewal Date",
    "Status",
    "Subscription Active Until",
    "split_part",
)

CANCELED_FIELDS = (
    "canceled_at",
    "email",
    "first_name",
    "last_name",
    "price",
    "product_name",
    "renewal_date",
    "subscription_active_until",
)


def membership_program(product_name: str) -> str | None:
    return PROGRAMS.get(str(product_name or "").strip())


def load_report_rows(
    *,
    report_id: int,
    report_category: str,
    label: str,
    allow_empty: bool,
) -> list[dict]:
    try:
        rows = fetch_report_rows(
            report_id=report_id,
            category=report_category,
            force_refresh=True,
        )
    except (ReportJWTError, requests.RequestException) as exc:
        raise RuntimeError(f"Marvelous {label} report failed: {exc}") from exc
    if not rows and not allow_empty:
        raise RuntimeError(f"Marvelous {label} report returned no rows")
    return rows


def load_active_rows_from_env() -> list[dict]:
    return load_report_rows(
        report_id=int(os.getenv("MARVELOUS_ACTIVE_SUBS_REPORT_ID", "15")),
        report_category=os.getenv(
            "MARVELOUS_ACTIVE_SUBS_REPORT_CATEGORY",
            "users",
        ),
        label="active membership",
        allow_empty=False,
    )


def load_canceled_rows_from_env() -> list[dict]:
    return load_report_rows(
        report_id=int(os.getenv("MARVELOUS_CANCELED_SUBS_REPORT_ID", "14")),
        report_category=os.getenv(
            "MARVELOUS_CANCELED_SUBS_REPORT_CATEGORY",
            "users",
        ),
        label="canceled membership",
        allow_empty=True,
    )


def write_report_snapshot(
    rows: Iterable[dict],
    *,
    reports_dir: Path,
    prefix: str,
    fields: tuple[str, ...],
    now: datetime | None = None,
) -> Path:
    rows = list(rows)
    stamp_time = now or datetime.now(timezone.utc)
    stamp = stamp_time.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = reports_dir / f"{prefix}_{stamp}.csv"
    reports_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(reports_dir, 0o700)
    discovered = {str(key) for row in rows for key in row}
    fieldnames = list(fields) + sorted(discovered - set(fields))
    temporary = path.with_name(f".{path.name}.tmp")
    with temporary.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=fieldnames,
            extrasaction="ignore",
        )
        writer.writeheader()
        writer.writerows(rows)
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)
    os.chmod(path, 0o600)
    return path


def _snapshot_time(path: Path, prefix: str) -> datetime:
    stamp = path.name.removeprefix(f"{prefix}_").removesuffix(".csv")
    try:
        return datetime.strptime(stamp, "%Y%m%dT%H%M%SZ").replace(
            tzinfo=timezone.utc
        )
    except ValueError as exc:
        raise RuntimeError(f"invalid Marvelous snapshot name: {path.name}") from exc


def latest_fresh_snapshot(
    *,
    reports_dir: Path,
    prefix: str,
    now: datetime | None = None,
    max_age_hours: float = 26,
) -> Path:
    matches = sorted(reports_dir.glob(f"{prefix}_*.csv"))
    if not matches:
        raise RuntimeError(f"no Marvelous {prefix} snapshot found")
    path = matches[-1]
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    age_hours = (current - _snapshot_time(path, prefix)).total_seconds() / 3600
    if age_hours < 0 or age_hours > max_age_hours:
        raise RuntimeError(
            f"Marvelous {prefix} snapshot is stale: {age_hours:.1f} hours old"
        )
    return path
