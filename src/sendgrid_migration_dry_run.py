#!/usr/bin/env python3
"""Run a zero-write Mailchimp to SendGrid contact reconciliation."""

from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import sys
from typing import Any

from migration_read_clients import ReadOnlyMailchimpAPI, ReadOnlySendGridAPI
from sendgrid_contact_mapping import SourceContact, map_contacts
from sendgrid_migration_evidence import EvidenceStore, summarize


ALLOWED_POST = {"/marketing/contacts/search/emails"}
WELCOME_JOURNEY_ID = 3209
REQUIRED_SEGMENTS_BY_ID = {
    2964430: "New Subscriber YLS Membership",
    3018884: "Membership - Yoga Lifestyle",
    3019143: "Lifestyle",
    3019144: "Non-Lifestyle",
}
REQUIRED_SEGMENT_NAMES = {
    "Status - Member",
    "Status - Lead",
    "Status - Yoga Lifestyle - Canceled",
    "Status - TWY Archive - Canceled",
    "Membership - Yoga Lifestyle",
    "Membership - TWY Archive",
    "Role - Owner",
    "Role - Admin",
    "New Subscriber YLS Membership",
    "Lifestyle",
    "Non-Lifestyle",
}
REQUIRED_DYNAMIC_PREFIXES = (
    "Yoga Habit - ",
    "Habit Registered - ",
)
REQUIRED_MERGE_FIELDS = {"FNAME", "LNAME"}


def _source_contacts(status_members: dict[str, list[dict]]) -> list[SourceContact]:
    contacts: list[SourceContact] = []
    for requested_status, members in status_members.items():
        for member in members:
            tags = frozenset(
                tag["name"]
                for tag in member.get("tags") or []
                if tag.get("name")
            )
            contacts.append(SourceContact(
                email=member.get("email_address") or "",
                status=member.get("status") or requested_status,
                tags=tags,
                merge_fields=member.get("merge_fields") or {},
                last_changed=member.get("last_changed") or "",
                source_id=str(member.get("id") or ""),
            ))
    return contacts


def _source_digest(sources: list[SourceContact]) -> str:
    payload = [
        {
            "email": source.email.strip().lower(),
            "status": source.status,
            "tags": sorted(source.tags),
            "merge_fields": source.merge_fields,
            "last_changed": source.last_changed,
            "source_id": source.source_id,
        }
        for source in sorted(sources, key=lambda item: item.email.strip().lower())
    ]
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _mutation_endpoints(audit: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        entry
        for entry in audit
        if entry["method"] != "GET"
        and not (
            entry["method"] == "POST"
            and entry["path"] in ALLOWED_POST
        )
    ]


def journey_shape(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Return only stable, migration-relevant journey targeting and timing."""
    result: list[dict[str, Any]] = []
    for step in payload.get("steps") or []:
        step_type = step.get("step_type")
        shaped: dict[str, Any] = {"step_type": step_type}
        if step_type == "trigger-tag_added":
            shaped["tag_id"] = (step.get("trigger_settings") or {}).get("tag_id")
            shaped["tag_name"] = (
                ((step.get("trigger_details") or {}).get("tag") or {}).get("tag_name")
            )
        elif step_type == "delay":
            shaped["delay_time"] = step.get("delay_time")
        elif step_type == "action-send_email":
            shaped["campaign_id"] = (
                (step.get("action_settings") or {}).get("campaign_id")
            )
        result.append(shaped)
    return result


def journey_backup_report(
    live_steps: dict[str, Any],
    backup: dict[str, Any] | None,
) -> dict[str, Any]:
    if not backup:
        return {
            "match": False,
            "reason": "accepted journey backup not supplied",
            "live_shape": journey_shape(live_steps),
            "backup_shape": [],
        }
    backup_steps = backup.get("steps") or {}
    trigger = backup.get("trigger") or {}
    live_shape = journey_shape(live_steps)
    backup_shape = journey_shape(backup_steps)
    first = live_shape[0] if live_shape else {}
    trigger_matches = (
        trigger.get("state") == "resolved"
        and trigger.get("tag_id") == first.get("tag_id")
        and trigger.get("tag_name") == first.get("tag_name")
    )
    return {
        "match": live_shape == backup_shape and trigger_matches,
        "trigger_matches": trigger_matches,
        "live_shape": live_shape,
        "backup_shape": backup_shape,
    }


def source_dependency_report(inventory: dict[str, Any]) -> dict[str, Any]:
    segments = inventory.get("segments") or []
    names = {str(segment.get("name") or "") for segment in segments}
    by_id = {
        int(segment["id"]): str(segment.get("name") or "")
        for segment in segments
        if segment.get("id") is not None
    }
    mismatches = [
        {
            "id": segment_id,
            "expected_name": expected_name,
            "actual_name": by_id.get(segment_id),
        }
        for segment_id, expected_name in sorted(REQUIRED_SEGMENTS_BY_ID.items())
        if segment_id in by_id and by_id[segment_id] != expected_name
    ]
    missing_ids = sorted(
        segment_id
        for segment_id in REQUIRED_SEGMENTS_BY_ID
        if segment_id not in by_id
    )
    missing_names = sorted(REQUIRED_SEGMENT_NAMES - names)
    missing_prefixes = [
        prefix
        for prefix in REQUIRED_DYNAMIC_PREFIXES
        if not any(name.startswith(prefix) for name in names)
    ]
    merge_tags = {
        str(field.get("tag") or "")
        for field in inventory.get("merge_fields") or []
    }
    missing_merge_fields = sorted(REQUIRED_MERGE_FIELDS - merge_tags)
    complete = not (
        mismatches
        or missing_ids
        or missing_names
        or missing_prefixes
        or missing_merge_fields
    )
    return {
        "complete": complete,
        "required_ids": REQUIRED_SEGMENTS_BY_ID,
        "missing_ids": missing_ids,
        "id_name_mismatches": mismatches,
        "missing_names": missing_names,
        "missing_dynamic_prefixes": missing_prefixes,
        "missing_merge_fields": missing_merge_fields,
    }


def run_dry_run(
    mailchimp,
    sendgrid,
    evidence_root: Path,
    run_id: str,
    *,
    proof_manifest: dict | None = None,
    journey_backup: dict | None = None,
) -> dict[str, Any]:
    store = EvidenceStore(evidence_root)
    status_members = mailchimp.collect_members()
    sources = _source_contacts(status_members)
    emails = [source.email for source in sources]
    safety_states = sendgrid.safety_states(emails)
    contacts = map_contacts(sources, safety_states)
    sendgrid_inventory = sendgrid.inventory()
    mailchimp_inventory = mailchimp.inventory(WELCOME_JOURNEY_ID)
    dependencies = source_dependency_report(mailchimp_inventory)
    journey_report = journey_backup_report(
        (mailchimp_inventory.get("journey") or {}).get("steps") or {},
        journey_backup,
    )
    endpoint_audit = list(mailchimp.audit) + list(sendgrid.audit)
    mutations = _mutation_endpoints(endpoint_audit)
    if mutations:
        raise RuntimeError(f"mutation endpoint reached: {mutations}")

    summary = summarize(contacts)
    coverage_errors = dict(getattr(mailchimp, "coverage_errors", {}))
    summary.update({
        "run_id": run_id,
        "source_digest": _source_digest(sources),
        "coverage_errors": coverage_errors,
        "mutation_endpoint_count": len(mutations),
        "journey_backup_match": journey_report["match"],
        "source_dependencies_complete": dependencies["complete"],
        "gate_passed": not coverage_errors
        and summary["terminal_counts"].get("quarantine", 0) == 0
        and journey_report["match"]
        and dependencies["complete"],
    })

    store.write_json("manifest.json", summary)
    store.write_json(
        "contacts.json",
        [asdict(contact) for contact in contacts],
    )
    store.write_json("sendgrid_inventory.json", sendgrid_inventory)
    store.write_json("mailchimp_inventory.json", mailchimp_inventory)
    store.write_json("targeting_dependencies.json", dependencies)
    store.write_json("journey_backup_comparison.json", journey_report)
    store.write_json("endpoint_audit.json", endpoint_audit)
    store.write_json("proof_state.json", proof_manifest or {})
    store.complete()
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Read-only TWY Mailchimp to SendGrid reconciliation"
    )
    parser.add_argument("--run-id", required=True)
    parser.add_argument(
        "--proof-manifest",
        type=Path,
        help="accepted SendGrid proof manifest or teardown inventory",
    )
    parser.add_argument(
        "--journey-backup-dir",
        type=Path,
        required=True,
        help="accepted backup directory containing steps.json and trigger.json",
    )
    return parser


def main(argv=None) -> int:
    from twy_paths import load_env, sendgrid_migration_dir

    args = build_parser().parse_args(argv)
    load_env()
    required = {
        "MAILCHIMP_API_KEY": os.getenv("MAILCHIMP_API_KEY"),
        "MAILCHIMP_AUDIENCE_ID": os.getenv("MAILCHIMP_AUDIENCE_ID"),
        "MAILCHIMP_SERVER_PREFIX": os.getenv("MAILCHIMP_SERVER_PREFIX"),
        "SENDGRID_API_KEY": os.getenv("SENDGRID_API_KEY"),
    }
    missing = sorted(name for name, value in required.items() if not value)
    if missing:
        print(f"missing required configuration: {', '.join(missing)}", file=sys.stderr)
        return 2

    mailchimp = ReadOnlyMailchimpAPI(
        required["MAILCHIMP_SERVER_PREFIX"],
        required["MAILCHIMP_API_KEY"],
        required["MAILCHIMP_AUDIENCE_ID"],
    )
    sendgrid = ReadOnlySendGridAPI(required["SENDGRID_API_KEY"])
    proof_manifest = None
    if args.proof_manifest:
        proof_manifest = json.loads(args.proof_manifest.read_text())
    journey_backup = {
        "steps": json.loads((args.journey_backup_dir / "steps.json").read_text()),
        "trigger": json.loads((args.journey_backup_dir / "trigger.json").read_text()),
    }
    summary = run_dry_run(
        mailchimp,
        sendgrid,
        sendgrid_migration_dir(args.run_id),
        args.run_id,
        proof_manifest=proof_manifest,
        journey_backup=journey_backup,
    )
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0 if summary["gate_passed"] else 3


if __name__ == "__main__":
    raise SystemExit(main())
