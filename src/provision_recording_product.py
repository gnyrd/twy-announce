"""Provision the free product that carries a Yoga Habit class recording.

Everyone who registers for a free Habit class is mailed the edited recording the
day after. It lives in its own free, hidden, self-enrolling Marvelous product,
because claiming it forces account creation, which turns a registrant into a
customer. This creates that product once per class and records where it is.

Idempotent: a second run finds the recorded product, verifies it still exists at
the provider and still carries the media, and changes nothing.
"""

from __future__ import annotations

import argparse
import calendar
from datetime import date, datetime
import json
import logging
import os
from pathlib import Path
import sys

import requests

sys.path.insert(0, "/root/twy/paths")
sys.path.insert(0, "/root/twy/marvy")
sys.path.insert(0, "/root/twy/classes/scripts")

from twy_paths import habit_recording_state_path, load_env  # noqa: E402

CLASSES_API = "http://localhost:5003"
# Two weeks of access from each person's own enrollment (JP, 2026-08-08).
AVAILABLE_DAYS = 14

log = logging.getLogger("provision_recording_product")


def product_name(year: int, month: int) -> str:
    return f"FREE CLASS: Yoga Habit {calendar.month_name[month]} {year}"


def state_path(year: int, month: int) -> Path:
    return habit_recording_state_path(year, month)


def habit_class(year: int, month: int) -> dict | None:
    """The month's Habit plan, or None. Requires a published recording."""
    last = calendar.monthrange(year, month)[1]
    response = requests.get(
        f"{CLASSES_API}/api/plans",
        params={"from": f"{year:04d}-{month:02d}-01",
                "to": f"{year:04d}-{month:02d}-{last:02d}"},
        timeout=15,
    )
    response.raise_for_status()
    for plan in response.json():
        if plan.get("class_type") != "Habit":
            continue
        if not plan.get("marvelous_media_id"):
            log.info("%s Habit class has no published recording yet", plan.get("date"))
            return None
        return plan
    return None


def _client():
    from marvy.client import Client
    from sync import get_token
    return Client(auth_token=get_token())


def provision(year: int, month: int, *, dry_run: bool = False) -> dict | None:
    plan = habit_class(year, month)
    if not plan:
        return None
    media_id = int(plan["marvelous_media_id"])
    name = product_name(year, month)
    path = state_path(year, month)

    client = _client()
    recorded = {}
    if path.exists():
        recorded = json.loads(path.read_text(encoding="utf-8"))
        try:
            existing = client.get_product(int(recorded["product_id"]))
        except Exception:
            existing = None
        if existing and int(existing.get("content_count") or 0) > 0:
            log.info("%s already provisioned as product %s", name, recorded["product_id"])
            return recorded

    if dry_run:
        log.info("would create %s carrying media %s", name, media_id)
        return None

    product_id = recorded.get("product_id")
    if not product_id:
        product_id = client.create_product(
            name,
            product_type="course",
            pricing_type="free",
            published=True,
            visible=False,
            enrollment_enabled=True,
            product_available_days=AVAILABLE_DAYS,
        )
        log.info("created product %s for %s", product_id, name)

    client.add_media_to_product(int(product_id), media_id)
    check = client.get_product(int(product_id))
    if int(check.get("content_count") or 0) < 1:
        raise RuntimeError(f"product {product_id} carries no content after attach")

    payload = {
        "year": year,
        "month": month,
        "class_date": plan.get("date"),
        "class_title": str(plan.get("title") or ""),
        "product_id": int(product_id),
        "product_name": name,
        "media_id": media_id,
        "available_days": AVAILABLE_DAYS,
        "provisioned_at": datetime.now().astimezone().isoformat(),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=1, sort_keys=True), encoding="utf-8")
    log.info("%s ready: product %s carries media %s", name, product_id, media_id)
    return payload


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    load_env()
    parser = argparse.ArgumentParser()
    parser.add_argument("--year", type=int)
    parser.add_argument("--month", type=int)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    today = date.today()
    year = args.year or today.year
    month = args.month or today.month
    try:
        provision(year, month, dry_run=args.dry_run)
    except Exception as exc:
        log.error("provisioning failed: %s", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
