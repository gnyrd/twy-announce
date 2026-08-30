"""Daily per-contact list membership, so a subscriber delta can be explained.

`email_history_dir()` stores one integer a day. That number cannot answer the
only question worth asking of it. On 2026-08-09 the "Email: Subscribed" count
went 922 to 940 while only a handful of contacts had been created all week: the
other additions were contacts SendGrid already held being swept onto the list by
a sync. A count records +18 either way, and nothing in the record could separate
eighteen new people from eighteen re-filings.

This writes contact ids, creation timestamps, list ids and the acquisition
source stamped by `sendgrid_contact_source`. From two consecutive files, every
useful delta is arithmetic: who was created, who was merely added to a list, who
left one. `membership_delta` does that and is the reason the file exists.

No email addresses are stored. The contact id is the join key and is stable, so
the snapshots answer the population question without carrying a mailing list
around on disk.

Composition: SendGrid's full contact export carries CREATED_AT and CONTACT_ID
but no list column, and the contacts-search-by-email endpoint carries list_ids
and custom fields but takes emails. So the export enumerates the population and
the search fills in membership, 100 addresses per call. Emails live in memory
for the length of the run and are not written out.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import csv
import io
import json
import logging
import os
import sys

from sendgrid_api import SendGridAPI
from sendgrid_campaigns import EXPECTED_ACCOUNT_EMAIL
from sendgrid_contact_source import (
    DETAIL_FIELD,
    EMAIL_LOOKUP_CHUNK,
    SOURCE_FIELD,
)
from twy_paths import email_membership_dir, load_env

log = logging.getLogger("snapshot_list_membership")

SCHEMA_VERSION = 1


def export_rows(api) -> list[dict]:
    """Every contact SendGrid holds: id, email and created_at.

    Raises rather than returning a short population: a truncated export written
    as a snapshot would read later as people having left.
    """
    started = api.start_contact_export(None)
    export_id = str((started or {}).get("id") or "")
    if not export_id:
        raise ValueError("SendGrid contact export returned no ID")
    ready = api.wait_contact_export(export_id, timeout_s=600)

    expected = ready.get("contact_count")
    if isinstance(expected, bool) or not isinstance(expected, int):
        raise ValueError("SendGrid contact export returned no contact_count")

    rows: list[dict] = []
    for url in ready.get("urls") or []:
        text = api.download_contact_export(url).decode("utf-8-sig")
        reader = csv.DictReader(io.StringIO(text))
        for row in reader:
            contact_id = str(row.get("CONTACT_ID") or "").strip()
            email = str(row.get("EMAIL") or "").strip().lower()
            if not contact_id or not email:
                raise ValueError("SendGrid contact export row is incomplete")
            rows.append(
                {
                    "id": contact_id,
                    "email": email,
                    "created_at": str(row.get("CREATED_AT") or "").strip(),
                }
            )

    if len(rows) != expected:
        raise ValueError(
            f"SendGrid contact export incomplete: "
            f"expected {expected}, got {len(rows)}"
        )
    return rows


def build_snapshot(api, *, captured_at: str, snapshot_date: str) -> dict:
    """One day's population: every contact with its lists, source and birthday."""
    rows = export_rows(api)
    by_email = {row["email"]: row for row in rows}

    field_ids = {
        str(field.get("name") or ""): str(field.get("id") or "")
        for field in api.field_definitions()
    }
    source_id = field_ids.get(SOURCE_FIELD, "")
    detail_id = field_ids.get(DETAIL_FIELD, "")

    emails = sorted(by_email)
    contacts: list[dict] = []
    for start in range(0, len(emails), EMAIL_LOOKUP_CHUNK):
        chunk = emails[start : start + EMAIL_LOOKUP_CHUNK]
        found = api.contacts_by_emails(chunk)
        for email in chunk:
            row = by_email[email]
            contact = found.get(email) or {}
            custom = contact.get("custom_fields") or {}
            entry = {
                "id": row["id"],
                "created_at": contact.get("created_at") or row["created_at"],
                "list_ids": sorted(
                    str(identifier)
                    for identifier in (contact.get("list_ids") or [])
                ),
            }
            source = str(custom.get(source_id) or "") if source_id else ""
            detail = str(custom.get(detail_id) or "") if detail_id else ""
            if source:
                entry["source"] = source
            if detail:
                entry["source_detail"] = detail
            contacts.append(entry)

    contacts.sort(key=lambda entry: entry["id"])
    lists = {
        str(item.get("id") or ""): str(item.get("name") or "")
        for item in api.marketing_lists()
    }
    return {
        "version": SCHEMA_VERSION,
        "date": snapshot_date,
        "captured_at": captured_at,
        "contact_count": len(contacts),
        "lists": lists,
        "contacts": contacts,
    }


def membership_delta(previous: dict, current: dict) -> dict:
    """What actually changed between two snapshots.

    `created` is new people. `added` and `removed` are list movements for
    contacts that already existed, which is the distinction a bare count loses.
    """
    before = {entry["id"]: entry for entry in previous.get("contacts") or []}
    after = {entry["id"]: entry for entry in current.get("contacts") or []}

    created = sorted(set(after) - set(before))
    deleted = sorted(set(before) - set(after))

    added: dict[str, list[str]] = {}
    removed: dict[str, list[str]] = {}
    for contact_id in sorted(set(before) & set(after)):
        was = set(before[contact_id].get("list_ids") or [])
        now = set(after[contact_id].get("list_ids") or [])
        for list_id in sorted(now - was):
            added.setdefault(list_id, []).append(contact_id)
        for list_id in sorted(was - now):
            removed.setdefault(list_id, []).append(contact_id)

    by_source: dict[str, int] = {}
    for contact_id in created:
        source = after[contact_id].get("source") or "unattributed"
        by_source[source] = by_source.get(source, 0) + 1

    return {
        "from": previous.get("date", ""),
        "to": current.get("date", ""),
        "created": created,
        "deleted": deleted,
        "created_by_source": by_source,
        "list_added": added,
        "list_removed": removed,
    }


def write_snapshot(snapshot: dict, directory=None):
    target_dir = directory or email_membership_dir()
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / f"{snapshot['date']}.json"
    path.write_text(
        json.dumps(snapshot, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path


def main() -> int:
    logging.basicConfig(
        format="%(asctime)s %(levelname)s %(message)s",
        level=logging.INFO,
    )
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="build the snapshot and report it without writing a file",
    )
    args = parser.parse_args()

    load_env()
    api_key = os.getenv("SENDGRID_API_KEY", "")
    if not api_key:
        log.error("SENDGRID_API_KEY is not set")
        return 1
    api = SendGridAPI(api_key)
    if api.user_email() != EXPECTED_ACCOUNT_EMAIL:
        log.error("unexpected SendGrid account")
        return 1

    now = datetime.now(timezone.utc)
    snapshot = build_snapshot(
        api,
        captured_at=now.isoformat(),
        snapshot_date=now.date().isoformat(),
    )
    if args.dry_run:
        log.info(
            "%d contacts across %d lists (not written)",
            snapshot["contact_count"],
            len(snapshot["lists"]),
        )
        return 0

    path = write_snapshot(snapshot)
    log.info("%d contacts written to %s", snapshot["contact_count"], path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
