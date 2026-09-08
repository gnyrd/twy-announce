"""The HeyMarvelous customer id on a SendGrid contact: a member's durable identity.

A member's email address can change. HeyMarvelous changes it only through its
support, and the active-subscriptions report that drives the member sync names
members by email alone, so the sync used to read a changed address as a new
person and put the old address back the next night. This field is what lets
the sync recognise the same person under a new address and rename the contact
instead (vault: planning/2026_09_07_member_email_rename_sync_design.md).

The field name and type are locked in the email naming guide (Locked contact
fields). The id itself comes from marvy.db, the only place HeyMarvelous's
customer id and current email sit side by side: the report has no id column.

Since 2026-09-07 evening (JP: the extension is worthwhile) the id goes on
every existing SendGrid contact HeyMarvelous knows, member or not, so a
rename works for anyone who ever bought, enrolled or registered. Knowing a
person is not consent to email them: a customer with no contact gets none.
"""

from __future__ import annotations

from pathlib import Path
import sqlite3

IDENTITY_FIELD = "twy_marvelous_customer_id"
IDENTITY_FIELD_TYPE = "Text"


def identity_field_id(api) -> str:
    """The SendGrid field id for the identity field, or "" when it does not exist.

    Never creates anything, so a dry run can read it.
    """
    for field in api.field_definitions():
        if str(field.get("name") or "") == IDENTITY_FIELD:
            return str(field.get("id") or "")
    return ""


def ensure_identity_field(api) -> str:
    """The field id, creating the field once when it is missing."""
    identifier = identity_field_id(api)
    if identifier:
        return identifier
    created = api.create_field_definition(IDENTITY_FIELD, IDENTITY_FIELD_TYPE)
    identifier = str((created or {}).get("id") or "")
    if not identifier:
        raise ValueError(
            f"SendGrid custom field {IDENTITY_FIELD} was created without an ID"
        )
    return identifier


def custom_field_ids(api) -> dict[str, str]:
    """Every custom field's name to id, for copying fields between contacts."""
    return {
        str(field.get("name") or ""): str(field.get("id") or "")
        for field in api.field_definitions()
        if field.get("name") and field.get("id")
    }


def customer_ids_by_email(db_path) -> dict[str, str]:
    """Lowercase email to HeyMarvelous customer id, read-only from marvy.db.

    marvy.db is rebuilt from the HM API every morning, so this is at most a
    day behind HeyMarvelous. Two customers sharing one address would collide
    here; there are none, and the last row read wins if one ever appears.
    """
    connection = sqlite3.connect(f"file:{Path(db_path)}?mode=ro", uri=True)
    try:
        rows = connection.execute(
            "SELECT id, email FROM customers "
            "WHERE email IS NOT NULL AND email != ''"
        ).fetchall()
    finally:
        connection.close()
    result: dict[str, str] = {}
    for customer_id, email in rows:
        key = str(email or "").strip().lower()
        if key and "@" in key and customer_id is not None:
            result[key] = str(customer_id)
    return result


def plan_identity_stamps(
    listed: list[dict],
    ids_by_email: dict[str, str],
    field_id: str,
) -> tuple[list[dict], dict]:
    """The upsert payloads that put the id on existing contacts, plus counts.

    listed is a contact export carrying the identity field under `fields`.
    Only contacts that already exist are ever stamped, never created. A contact
    carrying a different id than marvy.db now gives for its address is
    restamped, marvy.db being the source, and counted apart so it is visible.
    """
    payloads: list[dict] = []
    counts = {"matched": 0, "already": 0, "stamped": 0, "restamped": 0}
    for row in listed:
        email = str(row.get("email") or "").strip().lower()
        customer_id = ids_by_email.get(email)
        if not customer_id:
            continue
        counts["matched"] += 1
        current = str((row.get("fields") or {}).get(IDENTITY_FIELD) or "").strip()
        if current == customer_id:
            counts["already"] += 1
            continue
        counts["restamped" if current else "stamped"] += 1
        payloads.append({"email": email, "custom_fields": {field_id: customer_id}})
    return payloads, counts
