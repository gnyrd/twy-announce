"""Acquisition source stamped onto a SendGrid contact at creation.

SendGrid records when a contact was created and what lists it sits on, and
nothing else about where it came from. That is not enough to answer "where are
these subscribers coming from": a contact created by the Habit registrant sync
and a person who typed their address into the landing page form are
indistinguishable after the fact, and adding an existing contact to a list looks
identical to acquiring a new one.

Two Text custom fields close that gap. `twy_source` is a closed vocabulary
naming the write path that first created the contact. `twy_source_detail` is the
free-form specific: the UTM the signup carried, the product that was bought, the
operator who ran an import.

The stamp is CREATE-ONLY, and that is the whole correctness burden of this
module. `PUT /marketing/contacts` overwrites every field it is handed, so a
writer that stamped unconditionally would rewrite a person's origin to
`habit_sync` the first morning the sync swept them up. `stamp_new_contacts`
therefore asks SendGrid which of these emails already exist and stamps only the
ones that do not.

Attribution is never worth failing a write for. Callers on a person-facing path
(the signup form) wrap the stamp in try/except and upsert unstamped rather than
turn a subscriber away because a custom field could not be resolved.
"""

from __future__ import annotations

SOURCE_FIELD = "twy_source"
DETAIL_FIELD = "twy_source_detail"

SOURCE_FIELD_TYPES = {SOURCE_FIELD: "Text", DETAIL_FIELD: "Text"}

# The closed vocabulary. One value per write path that can create a contact.
SOURCE_HABIT_SIGNUP = "habit_signup"
SOURCE_HABIT_SYNC = "habit_sync"
SOURCE_PRODUCT_PURCHASE = "product_purchase"
SOURCE_FREE_CLASS_CLAIM = "free_class_claim"
SOURCE_MEMBER_SYNC = "member_sync"
SOURCE_MIGRATION = "migration"
SOURCE_MANUAL_IMPORT = "manual_import"

KNOWN_SOURCES = frozenset(
    {
        SOURCE_HABIT_SIGNUP,
        SOURCE_HABIT_SYNC,
        SOURCE_PRODUCT_PURCHASE,
        SOURCE_FREE_CLASS_CLAIM,
        SOURCE_MEMBER_SYNC,
        SOURCE_MIGRATION,
        SOURCE_MANUAL_IMPORT,
    }
)

# SendGrid's contacts/search/emails endpoint caps a request at 100 addresses.
EMAIL_LOOKUP_CHUNK = 100

# SendGrid rejects a custom field value longer than 100 characters.
MAX_DETAIL_LENGTH = 100


class UnknownContactSource(ValueError):
    """A caller named a source outside the closed vocabulary."""


def ensure_source_fields(api) -> dict[str, str]:
    """Return {field name: field id}, creating either field if it is missing.

    Idempotent: the fields are created once and found by name afterwards.
    """
    by_name = {
        str(field.get("name") or ""): str(field.get("id") or "")
        for field in api.field_definitions()
    }
    resolved: dict[str, str] = {}
    for name, field_type in SOURCE_FIELD_TYPES.items():
        identifier = by_name.get(name) or ""
        if not identifier:
            created = api.create_field_definition(name, field_type)
            identifier = str((created or {}).get("id") or "")
            if not identifier:
                raise ValueError(
                    f"SendGrid custom field {name} was created without an ID"
                )
        resolved[name] = identifier
    return resolved


def normalize_detail(detail: str) -> str:
    """Collapse a detail to something SendGrid will accept, or empty."""
    value = " ".join(str(detail or "").split())
    return value[:MAX_DETAIL_LENGTH]


def existing_emails(api, emails: list[str]) -> set[str]:
    """The subset of these addresses SendGrid already holds a contact for."""
    unique = sorted({str(e or "").strip().lower() for e in emails if e})
    found: set[str] = set()
    for start in range(0, len(unique), EMAIL_LOOKUP_CHUNK):
        chunk = unique[start : start + EMAIL_LOOKUP_CHUNK]
        if not chunk:
            continue
        found.update(api.contacts_by_emails(chunk))
    return found


def stamp_new_contacts(
    api,
    contacts: list[dict],
    *,
    source: str,
    detail: str = "",
    field_ids: dict[str, str] | None = None,
) -> list[dict]:
    """Copy `contacts`, adding source custom fields to the not-yet-existing ones.

    Contacts SendGrid already knows are returned untouched, so a person's
    original acquisition source survives every later sync that re-upserts them.
    """
    if source not in KNOWN_SOURCES:
        raise UnknownContactSource(
            f"unknown contact source {source!r}; "
            f"add it to KNOWN_SOURCES deliberately"
        )
    if not contacts:
        return []

    ids = field_ids or ensure_source_fields(api)
    known = existing_emails(
        api, [contact.get("email", "") for contact in contacts]
    )
    normalized_detail = normalize_detail(detail)

    stamped: list[dict] = []
    for contact in contacts:
        copied = dict(contact)
        email = str(copied.get("email") or "").strip().lower()
        if email and email not in known:
            fields = dict(copied.get("custom_fields") or {})
            fields[ids[SOURCE_FIELD]] = source
            if normalized_detail:
                fields[ids[DETAIL_FIELD]] = normalized_detail
            copied["custom_fields"] = fields
        stamped.append(copied)
    return stamped
