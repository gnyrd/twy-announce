"""Private, immutable evidence primitives for the migration dry-run."""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import parse_qsl, urlsplit, urlunsplit

from sendgrid_contact_mapping import DesiredContact


class EvidenceAlreadyExists(RuntimeError):
    pass


_SECRET_KEYS = {
    "authorization",
    "api_key",
    "apikey",
    "cookie",
    "set-cookie",
    "token",
    "access_token",
    "refresh_token",
}


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: "[REDACTED]" if str(key).lower() in _SECRET_KEYS else redact(child)
            for key, child in value.items()
        }
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, tuple):
        return [redact(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return [redact(item) for item in sorted(value)]
    if isinstance(value, str):
        parsed = urlsplit(value)
        if parsed.scheme and parsed.netloc and parsed.query:
            keys = [key.lower() for key, _ in parse_qsl(parsed.query)]
            if any(
                marker in key
                for key in keys
                for marker in ("signature", "credential", "token", "x-amz-", "x-goog-")
            ):
                return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))
    return value


def _canonical_contact(contact: DesiredContact) -> dict[str, Any]:
    payload = asdict(contact)
    payload["proposed_lists"] = sorted(contact.proposed_lists)
    payload["custom_fields"] = dict(sorted(contact.custom_fields.items()))
    payload["provenance"] = dict(sorted(contact.provenance.items()))
    payload["reasons"] = list(contact.reasons)
    return payload


def canonical_digest(contacts: Iterable[DesiredContact]) -> str:
    payload = [
        _canonical_contact(contact)
        for contact in sorted(contacts, key=lambda item: item.email)
    ]
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def summarize(contacts: Iterable[DesiredContact]) -> dict[str, Any]:
    contacts = list(contacts)
    terminal_counts = Counter(contact.terminal_class for contact in contacts)
    list_counts = Counter(
        list_name
        for contact in contacts
        for list_name in contact.proposed_lists
    )
    action_counts = Counter()
    for contact in contacts:
        if contact.terminal_class == "deliverable":
            action_counts["would_create_or_update_contact"] += 1
        elif contact.terminal_class == "marketing_suppressed":
            action_counts["would_add_or_preserve_marketing_suppression"] += 1
        elif contact.terminal_class == "cleaned_denylist":
            action_counts["cleaned_denylist_only"] += 1
        else:
            action_counts["quarantine"] += 1
    return {
        "total_contacts": len(contacts),
        "terminal_counts": dict(sorted(terminal_counts.items())),
        "proposed_list_counts": dict(sorted(list_counts.items())),
        "action_counts": dict(sorted(action_counts.items())),
        "mapping_digest": canonical_digest(contacts),
    }


class EvidenceStore:
    def __init__(self, root: Path):
        self.root = root
        if (root / "COMPLETE").exists():
            raise EvidenceAlreadyExists(f"completed evidence already exists: {root}")
        root.mkdir(parents=True, exist_ok=True)
        root.chmod(0o700)

    def write_json(self, relative_path: str, value: Any) -> Path:
        destination = self.root / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(f".{destination.name}.{os.getpid()}.tmp")
        payload = json.dumps(redact(value), indent=2, sort_keys=True) + "\n"
        with temporary.open("w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.chmod(0o600)
        os.replace(temporary, destination)
        destination.chmod(0o600)
        return destination

    def complete(self) -> Path:
        marker = self.root / "COMPLETE"
        fd = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write("complete\n")
            handle.flush()
            os.fsync(handle.fileno())
        return marker
