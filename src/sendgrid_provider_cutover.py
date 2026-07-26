#!/usr/bin/env python3
"""Plan and apply the bounded SendGrid provider naming cutover."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys

from sendgrid_api import SendGridAPI
from sendgrid_campaigns import (
    EXPECTED_ACCOUNT_EMAIL,
    EXPECTED_SENDER_EMAIL,
    SendGridRegistry,
)
from twy_paths import data_root, load_env
from twy_platform import locked_write


SENDER_ID = 9423402
SUPPRESSION_GROUP_ID = 35187
SUPPRESSION_GROUP_DESCRIPTION = "TWY email preferences"
LIST_RENAMES = {
    "TWY Marketing": "Email: Subscribed",
    "TWY Yoga Lifestyle": "Member: Yoga Lifestyle",
    "TWY Archive": "Member: Archive",
    "TWY Yoga Habit 2026-04": "Yoga Habit: Interested: 2026_04",
    "TWY Yoga Habit 2026-05": "Yoga Habit: Interested: 2026_05",
    "TWY Yoga Habit 2026-06": "Yoga Habit: Interested: 2026_06",
    "TWY Habit Registered 2026-05": "Yoga Habit: Registered: 2026_05",
    "TWY Habit Registered 2026-06": "Yoga Habit: Registered: 2026_06",
}
CURRENT_LISTS = [
    "Yoga Habit: Interested: 2026_08",
    "Yoga Habit: Registered: 2026_08",
]


def _canonical(value: dict) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()


def plan_digest(plan: dict) -> str:
    return hashlib.sha256(_canonical(plan)).hexdigest()


def approval_phrase(plan: dict) -> str:
    return (
        "APPROVE TWY SENDGRID PROVIDER CUTOVER "
        f"{plan_digest(plan)}"
    )


def build_plan(
    *,
    account_email: str,
    marketing_lists: list[dict],
    suppression_groups: list[dict],
) -> dict:
    if account_email != EXPECTED_ACCOUNT_EMAIL:
        raise ValueError("unexpected SendGrid account")
    by_name = {}
    for item in marketing_lists:
        name = str(item.get("name") or "")
        if name in by_name:
            raise ValueError(f"duplicate SendGrid list name: {name}")
        by_name[name] = item

    renames = []
    for source, target in LIST_RENAMES.items():
        source_item = by_name.get(source)
        target_item = by_name.get(target)
        if source_item and target_item:
            raise ValueError(
                f"both source and target SendGrid lists exist: {target}"
            )
        item = target_item or source_item
        if not item or not item.get("id"):
            raise ValueError(f"required SendGrid list is absent: {source}")
        renames.append({
            "id": str(item["id"]),
            "source_name": source,
            "target_name": target,
            "observed_name": str(item["name"]),
            "observed_count": int(item.get("contact_count") or 0),
        })

    groups = {
        int(group.get("id") or 0): group
        for group in suppression_groups
    }
    group = groups.get(SUPPRESSION_GROUP_ID)
    if not group:
        raise ValueError("required SendGrid suppression group is absent")
    if group.get("name") not in {
        "TWY Newsletters",
        "Email: Unsubscribed",
    }:
        raise ValueError("unexpected SendGrid suppression group name")

    return {
        "version": 1,
        "account_email": EXPECTED_ACCOUNT_EMAIL,
        "sender": {
            "id": SENDER_ID,
            "email": EXPECTED_SENDER_EMAIL,
        },
        "list_renames": sorted(
            renames,
            key=lambda item: item["target_name"],
        ),
        "list_ensures": sorted(CURRENT_LISTS),
        "suppression_group": {
            "id": SUPPRESSION_GROUP_ID,
            "observed_name": str(group["name"]),
            "target_name": "Email: Unsubscribed",
            "is_default": True,
        },
    }


def apply_plan(
    *,
    api,
    plan: dict,
    approval: str,
    registry_path: Path,
) -> dict:
    if approval != approval_phrase(plan):
        raise ValueError("exact provider cutover approval is required")
    if api.user_email() != EXPECTED_ACCOUNT_EMAIL:
        raise ValueError("unexpected SendGrid account")

    current_by_id = {
        str(item.get("id") or ""): item
        for item in api.marketing_lists()
    }
    list_ids = {}
    for operation in plan["list_renames"]:
        identifier = operation["id"]
        current = current_by_id.get(identifier)
        if not current or current.get("name") not in {
            operation["source_name"],
            operation["target_name"],
        }:
            raise ValueError(
                f"SendGrid list drifted: {operation['target_name']}"
            )
        if current["name"] != operation["target_name"]:
            api.update_list(identifier, operation["target_name"])
        list_ids[operation["target_name"]] = identifier

    current_by_name = {
        str(item.get("name") or ""): item
        for item in api.marketing_lists()
    }
    for name in plan["list_ensures"]:
        existing = current_by_name.get(name)
        if existing:
            identifier = str(existing.get("id") or "")
        else:
            created = api.create_list(name)
            identifier = str(created.get("id") or "")
        if not identifier:
            raise ValueError(f"SendGrid list returned no ID: {name}")
        list_ids[name] = identifier

    group_plan = plan["suppression_group"]
    group = api.suppression_group(group_plan["id"])
    if group.get("name") not in {
        group_plan["observed_name"],
        group_plan["target_name"],
    }:
        raise ValueError("SendGrid suppression group drifted")
    if (
        group.get("name") != group_plan["target_name"]
        or not group.get("is_default")
    ):
        api.update_suppression_group(
            group_plan["id"],
            name=group_plan["target_name"],
            description=SUPPRESSION_GROUP_DESCRIPTION,
            is_default=True,
        )

    observed_lists = {
        str(item.get("name") or ""): str(item.get("id") or "")
        for item in api.marketing_lists()
    }
    for name, identifier in list_ids.items():
        if observed_lists.get(name) != identifier:
            raise ValueError(f"SendGrid list verification failed: {name}")
    observed_group = api.suppression_group(group_plan["id"])
    if observed_group.get("name") != group_plan["target_name"]:
        raise ValueError("SendGrid suppression group verification failed")

    registry = {
        "version": 1,
        "account_email": EXPECTED_ACCOUNT_EMAIL,
        "sender": plan["sender"],
        "suppression_group": {
            "id": group_plan["id"],
            "name": group_plan["target_name"],
        },
        "lists": {
            name: {"id": identifier}
            for name, identifier in sorted(list_ids.items())
        },
        "cutover_plan_sha256": plan_digest(plan),
    }
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    locked_write(
        registry_path,
        json.dumps(registry, indent=2, sort_keys=True) + "\n",
    )
    os.chmod(registry_path, 0o600)
    SendGridRegistry.load(registry_path)
    return {
        "digest": plan_digest(plan),
        "registry_path": str(registry_path),
        "list_count": len(list_ids),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("plan")
    apply_parser = subparsers.add_parser("apply")
    apply_parser.add_argument("--approval", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    load_env()
    api_key = os.getenv("SENDGRID_API_KEY", "")
    if not api_key:
        raise SystemExit("SENDGRID_API_KEY is not configured")
    api = SendGridAPI(api_key)
    plan_path = data_root() / "sendgrid" / "provider_cutover_plan.json"
    registry_path = data_root() / "sendgrid" / "production_objects.json"

    if arguments.command == "plan":
        plan = build_plan(
            account_email=api.user_email(),
            marketing_lists=api.marketing_lists(),
            suppression_groups=api.suppression_groups(),
        )
        plan_path.parent.mkdir(parents=True, exist_ok=True)
        locked_write(
            plan_path,
            json.dumps(plan, indent=2, sort_keys=True) + "\n",
        )
        os.chmod(plan_path, 0o600)
        print(json.dumps({
            "plan_path": str(plan_path),
            "digest": plan_digest(plan),
            "approval": approval_phrase(plan),
        }, indent=2))
        return 0

    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    result = apply_plan(
        api=api,
        plan=plan,
        approval=arguments.approval,
        registry_path=registry_path,
    )
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
