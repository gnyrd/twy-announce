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
