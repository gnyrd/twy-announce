"""Safety and durable evidence primitives for the TWY SendGrid proof."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
import os
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlsplit, urlunsplit


DELIVERABLE_ADDRESSES = frozenset({
    "admin@tiffanywoodyoga.com",
    "jpgan6@gmail.com",
})
DEFAULT_SYNTHETIC_ADDRESSES = frozenset({
    "subscribed@twy-sendgrid-proof.invalid",
    "unsubscribed@twy-sendgrid-proof.invalid",
    "cleaned@twy-sendgrid-proof.invalid",
})
ALLOWED_STATUSES = frozenset({"proven", "unavailable", "plan-gated", "unknown"})
_SECRET_KEYS = frozenset({
    "authorization",
    "api_key",
    "apikey",
    "cookie",
    "set-cookie",
    "token",
    "access_token",
    "refresh_token",
})
_SIGNED_QUERY_MARKERS = (
    "signature",
    "credential",
    "token",
    "x-amz-",
    "x-goog-",
)


class RecipientSafetyError(RuntimeError):
    pass


@dataclass(frozen=True)
class ProofConfig:
    deliverable_addresses: frozenset[str] = DELIVERABLE_ADDRESSES
    synthetic_addresses: frozenset[str] = DEFAULT_SYNTHETIC_ADDRESSES
    list_name_prefix: str = "TWY SendGrid Migration Proof"
    scheduled_delay_seconds: int = 600

    def __post_init__(self):
        if self.deliverable_addresses != DELIVERABLE_ADDRESSES:
            raise ValueError("deliverable allowlist is fixed for this proof")
        if not self.synthetic_addresses or any(
            not address.lower().endswith(".invalid")
            for address in self.synthetic_addresses
        ):
            raise ValueError("synthetic addresses must use the reserved .invalid domain")
        if self.scheduled_delay_seconds != 600:
            raise ValueError("scheduled delay is fixed at 600 seconds")


@dataclass(frozen=True)
class CapabilityResult:
    status: str
    evidence: tuple[str, ...]
    detail: str

    def __post_init__(self):
        if self.status not in ALLOWED_STATUSES:
            raise ValueError(f"invalid capability status: {self.status}")


@dataclass(frozen=True)
class ProofManifest:
    run_id: str
    phase: str = "new"
    object_ids: dict[str, str] = field(default_factory=dict)
    capabilities: dict[str, CapabilityResult] = field(default_factory=dict)


def normalize_addresses(addresses: set[str] | frozenset[str]) -> frozenset[str]:
    return frozenset(address.strip().lower() for address in addresses)


def assert_allowed_recipients(addresses: set[str] | frozenset[str]) -> None:
    normalized = normalize_addresses(addresses)
    if normalized != DELIVERABLE_ADDRESSES:
        raise RecipientSafetyError(
            "resolved SendGrid recipients must equal the fixed two-address allowlist"
        )
    if any(address.endswith(".invalid") for address in normalized):
        raise RecipientSafetyError("synthetic address reached the deliverable list")


def _redact_url(value: str) -> str:
    try:
        parsed = urlsplit(value)
    except ValueError:
        return value
    if not parsed.scheme or not parsed.netloc or not parsed.query:
        return value
    query_keys = [key.lower() for key, _ in parse_qsl(parsed.query, keep_blank_values=True)]
    if any(marker in key for key in query_keys for marker in _SIGNED_QUERY_MARKERS):
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", parsed.fragment))
    return value


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        result = {}
        for key, child in value.items():
            if str(key).lower() in _SECRET_KEYS:
                result[key] = "[REDACTED]"
            else:
                result[key] = redact(child)
        return result
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact(item) for item in value)
    if isinstance(value, str):
        return _redact_url(value)
    return value


class EvidenceStore:
    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def write_json(self, relative_path: str, value: Any) -> Path:
        destination = self.root / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(f".{destination.name}.{os.getpid()}.tmp")
        payload = json.dumps(redact(value), indent=2, sort_keys=True) + "\n"
        with temporary.open("w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
        return destination

    def write_manifest(self, manifest: ProofManifest) -> Path:
        return self.write_json("manifest.json", asdict(manifest))

    def read_manifest(self) -> ProofManifest:
        payload = json.loads((self.root / "manifest.json").read_text())
        capabilities = {
            name: CapabilityResult(
                status=result["status"],
                evidence=tuple(result["evidence"]),
                detail=result["detail"],
            )
            for name, result in payload.get("capabilities", {}).items()
        }
        return ProofManifest(
            run_id=payload["run_id"],
            phase=payload.get("phase", "new"),
            object_ids=payload.get("object_ids", {}),
            capabilities=capabilities,
        )
