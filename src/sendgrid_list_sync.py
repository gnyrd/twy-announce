"""Exact SendGrid list synchronization without changing suppression state."""

from __future__ import annotations

from sendgrid_campaigns import SendGridRegistry
from sendgrid_mailings import validate_sendgrid_name


def ensure_list(api, registry: SendGridRegistry, name: str) -> str:
    try:
        return registry.list_id(name)
    except KeyError:
        # Last waist before the provider. Every builder in sendgrid_mailings
        # already validates, but this took whatever it was handed, so a caller
        # skipping a builder could create a list carrying prohibited
        # punctuation. Naming a list wrong is not a typo you fix later: it is
        # the identity every mailing matches on.
        created = api.create_list(validate_sendgrid_name(name))
        identifier = str(created.get("id") or "")
        if not identifier:
            raise ValueError("SendGrid list returned no immutable ID")
        registry.register_list(name, identifier)
        return identifier


def _normalize_contact(contact: dict) -> dict:
    email = str(contact.get("email") or "").strip().lower()
    if not email or "@" not in email:
        raise ValueError("SendGrid list sync contact has invalid email")
    normalized = {"email": email}
    for field in ("first_name", "last_name"):
        value = str(contact.get(field) or "").strip()
        if value:
            normalized[field] = value
    return normalized


def sync_exact_list(
    *,
    api,
    destination_list_id: str,
    desired_contacts: list[dict],
    additive_list_ids: list[str] | None = None,
) -> dict:
    by_email = {
        normalized["email"]: normalized
        for normalized in map(_normalize_contact, desired_contacts)
    }
    desired = [by_email[email] for email in sorted(by_email)]
    previous = api.list_contacts(destination_list_id)

    if desired:
        list_ids = [
            destination_list_id,
            *[
                identifier
                for identifier in (additive_list_ids or [])
                if identifier != destination_list_id
            ],
        ]
        job_id = api.upsert_contacts(list_ids, desired)
        api.wait_contact_job(job_id, timeout_s=300)

    desired_emails = set(by_email)
    stale_ids = []
    for contact in previous:
        email = str(contact.get("email") or "").strip().lower()
        if email in desired_emails:
            continue
        identifier = str(contact.get("id") or "")
        if not identifier:
            raise ValueError("SendGrid list contact has no immutable ID")
        stale_ids.append(identifier)
    if stale_ids:
        job_id = api.remove_contacts_from_list(
            destination_list_id,
            stale_ids,
        )
        api.wait_contact_job(job_id, timeout_s=300)

    return {
        "desired": len(desired),
        "previous": len(previous),
        "removed": len(stale_ids),
    }
