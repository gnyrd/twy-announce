"""Pure, fail-closed contact mapping for the TWY SendGrid migration."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable


class MappingError(RuntimeError):
    pass


@dataclass(frozen=True)
class SourceContact:
    email: str
    status: str
    tags: frozenset[str]
    merge_fields: dict[str, Any]
    last_changed: str
    source_id: str


@dataclass(frozen=True)
class SendGridSafetyState:
    contact: dict | None = None
    confirmed_absent: bool = False
    lookup_error: str | None = None
    global_suppressed: bool = False
    group_suppressed: bool = False
    bounced: bool = False
    blocked: bool = False
    invalid: bool = False
    spam_reported: bool = False


@dataclass(frozen=True)
class DesiredContact:
    email: str
    terminal_class: str
    proposed_lists: frozenset[str]
    custom_fields: dict[str, str]
    provenance: dict[str, str]
    reasons: tuple[str, ...] = field(default_factory=tuple)


def normalize_email(email: str) -> str:
    normalized = email.strip().lower()
    if not normalized or "@" not in normalized:
        raise MappingError("contact has invalid or missing email")
    return normalized


def _schema(tags: frozenset[str], deliverable: bool) -> tuple[frozenset[str], dict[str, str]]:
    if not deliverable:
        return frozenset(), {}
    lists = {"TWY Marketing"}
    if "Membership - Yoga Lifestyle" in tags:
        lists.add("TWY Yoga Lifestyle")
    if "Membership - TWY Archive" in tags:
        lists.add("TWY Archive")
    if "New Subscriber YLS Membership" in tags:
        lists.add("TWY Welcome 3209")
    for tag in tags:
        if tag.startswith("Yoga Habit - ") and not tag.endswith(" - Redeemed"):
            suffix = tag.removeprefix("Yoga Habit - ")
            if len(suffix) == 7 and suffix[4] == "-":
                lists.add(f"TWY Yoga Habit {suffix}")
        if tag.startswith("Habit Registered - "):
            suffix = tag.removeprefix("Habit Registered - ")
            lists.add(f"TWY Habit Registered {suffix}")

    fields: dict[str, str] = {}
    if "Status - Member" in tags:
        fields["twy_status"] = "member"
    elif "Status - Lead" in tags:
        fields["twy_status"] = "lead"
    if "Role - Owner" in tags:
        fields["twy_role"] = "owner"
    elif "Role - Admin" in tags:
        fields["twy_role"] = "admin"
    return frozenset(lists), fields


def map_contact(
    source: SourceContact,
    sendgrid: SendGridSafetyState,
) -> DesiredContact:
    email = normalize_email(source.email)
    reasons: list[str] = []

    hard_failures = {
        "spam_reported": sendgrid.spam_reported,
        "bounced": sendgrid.bounced,
        "blocked": sendgrid.blocked,
        "invalid": sendgrid.invalid,
    }
    active_hard_failures = sorted(key for key, active in hard_failures.items() if active)
    if active_hard_failures:
        terminal = "cleaned_denylist"
        reasons.extend(f"sendgrid_{key}" for key in active_hard_failures)
    elif sendgrid.global_suppressed or sendgrid.group_suppressed:
        terminal = "marketing_suppressed"
        if sendgrid.global_suppressed:
            reasons.append("sendgrid_global_suppressed")
        if sendgrid.group_suppressed:
            reasons.append("sendgrid_group_suppressed")
    elif sendgrid.lookup_error:
        terminal = "quarantine"
        reasons.append("sendgrid_lookup_error")
    elif source.status == "cleaned":
        terminal = "cleaned_denylist"
        reasons.append("mailchimp_cleaned")
    elif source.status == "unsubscribed":
        terminal = "marketing_suppressed"
        reasons.append("mailchimp_unsubscribed")
    elif source.status == "subscribed":
        terminal = "deliverable"
        reasons.append("mailchimp_subscribed")
    else:
        terminal = "quarantine"
        reasons.append(f"mailchimp_status_{source.status or 'missing'}")

    lists, fields = _schema(source.tags, terminal == "deliverable")
    fields = dict(fields)
    if terminal == "deliverable":
        first = str(source.merge_fields.get("FNAME") or "").strip()
        last = str(source.merge_fields.get("LNAME") or "").strip()
        if first:
            fields["first_name"] = first
        if last:
            fields["last_name"] = last

    return DesiredContact(
        email=email,
        terminal_class=terminal,
        proposed_lists=lists,
        custom_fields=fields,
        provenance={
            "mailchimp_status": source.status,
            "mailchimp_source_id": source.source_id,
            "mailchimp_last_changed": source.last_changed,
        },
        reasons=tuple(reasons),
    )


def map_contacts(
    sources: Iterable[SourceContact],
    sendgrid_states: dict[str, SendGridSafetyState | dict[str, Any]],
) -> list[DesiredContact]:
    seen: set[str] = set()
    mapped: list[DesiredContact] = []
    for source in sources:
        email = normalize_email(source.email)
        if email in seen:
            raise MappingError(f"duplicate Mailchimp source identity: {email}")
        seen.add(email)
        state = sendgrid_states.get(email)
        if state is None:
            state = SendGridSafetyState(
                lookup_error="missing SendGrid safety state"
            )
        if isinstance(state, dict):
            state = SendGridSafetyState(**state)
        mapped.append(map_contact(source, state))
    return sorted(mapped, key=lambda item: item.email)
