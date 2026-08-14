"""Fail closed SendGrid draft and segment state for TWY mailings."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import time
from typing import Callable

from newsletter_rendering import render_newsletter
from sendgrid_mailings import (
    INTERNAL_SEND_COPY,
    MailingPurpose,
    mailing_name,
    validate_sendgrid_name,
)
from twy_platform import locked_write


EXPECTED_ACCOUNT_EMAIL = "admin@tiffanywoodyoga.com"
EXPECTED_SENDER_EMAIL = "hello@tiffanywoodyoga.com"
UNSUBSCRIBE_GROUP_NAME = "Email: Unsubscribed"
PROVIDER_INJECTED_HTML_TOKENS = ("%sg_open_track%",)


def _without_provider_injected_html(value: object) -> object:
    if not isinstance(value, str):
        return value
    for token in PROVIDER_INJECTED_HTML_TOKENS:
        value = value.replace(token, "")
    return value


class ProviderVerificationError(ValueError):
    """Provider content or state does not match the locked mailing."""


class SendGridRegistry:
    def __init__(self, path: Path, payload: dict):
        self.path = path
        self.payload = payload

    @classmethod
    def load(cls, path: Path) -> "SendGridRegistry":
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("account_email") != EXPECTED_ACCOUNT_EMAIL:
            raise ValueError("unexpected SendGrid account")
        sender = payload.get("sender") or {}
        if (
            int(sender.get("id") or 0) <= 0
            or sender.get("email") != EXPECTED_SENDER_EMAIL
        ):
            raise ValueError("unexpected SendGrid sender")
        suppression_group = payload.get("suppression_group") or {}
        if (
            int(suppression_group.get("id") or 0) <= 0
            or suppression_group.get("name") != UNSUBSCRIBE_GROUP_NAME
        ):
            raise ValueError("unexpected unsubscribe group")
        for name, item in (payload.get("lists") or {}).items():
            validate_sendgrid_name(name)
            if not str((item or {}).get("id") or ""):
                raise ValueError(f"SendGrid list has no immutable ID: {name}")
        return cls(path, payload)

    @property
    def sender_id(self) -> int:
        return int(self.payload["sender"]["id"])

    @property
    def sender_email(self) -> str:
        return str(self.payload["sender"]["email"])

    @property
    def suppression_group_id(self) -> int:
        return int(self.payload["suppression_group"]["id"])

    def list_id(self, name: str) -> str:
        validate_sendgrid_name(name)
        item = (self.payload.get("lists") or {}).get(name)
        if not item or not item.get("id"):
            raise KeyError(f"SendGrid list is not registered: {name}")
        return str(item["id"])

    def register_list(self, name: str, list_id: str) -> None:
        validate_sendgrid_name(name)
        identifier = str(list_id).strip()
        if not identifier:
            raise ValueError("SendGrid list ID must not be empty")
        lists = self.payload.setdefault("lists", {})
        existing = lists.get(name)
        if existing and str(existing.get("id")) != identifier:
            raise ValueError("SendGrid list name is already registered")
        lists[name] = {"id": identifier}
        locked_write(
            self.path,
            json.dumps(self.payload, indent=2, sort_keys=True) + "\n",
        )

class SendGridCampaigns:
    def __init__(
        self,
        *,
        api,
        registry: SendGridRegistry,
        state_path: Path,
        now_fn: Callable[[], datetime] | None = None,
        sleep_fn: Callable[[float], None] = time.sleep,
        created_verification_delays: tuple[float, ...] = (0.5, 1.0, 2.0),
    ):
        self.api = api
        self.registry = registry
        self.state_path = state_path
        self.now_fn = now_fn or (lambda: datetime.now(timezone.utc))
        self._sleep = sleep_fn
        self._created_verification_delays = created_verification_delays

    def _load_state(self) -> dict:
        if not self.state_path.exists():
            return {
                "version": 1,
                "single_sends": {},
                "segments": {},
            }
        payload = json.loads(self.state_path.read_text(encoding="utf-8"))
        if payload.get("version") != 1:
            raise ValueError("unsupported SendGrid mailing state")
        return payload

    def _save_state(self, payload: dict) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        locked_write(
            self.state_path,
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
        )

    def set_expected_purposes(
        self,
        purposes: list[MailingPurpose],
    ) -> None:
        state = self._load_state()
        expected = set(state.get("expected_purposes") or [])
        expected.update(self._purpose_key(purpose) for purpose in purposes)
        state["expected_purposes"] = sorted(expected)
        self._save_state(state)

    def expected_purposes(self) -> list[MailingPurpose]:
        state = self._load_state()
        values = state.get("expected_purposes")
        if values is None:
            values = list((state.get("single_sends") or {}).keys())
        return [MailingPurpose(value) for value in values]

    def hold_purpose(self, purpose: MailingPurpose) -> None:
        """Remove a purpose from automatic scheduling and disarm its send."""
        state = self._load_state()
        key = self._purpose_key(purpose)
        expected = state.get("expected_purposes")
        if expected is None:
            expected = list((state.get("single_sends") or {}).keys())
        if key in expected:
            state["expected_purposes"] = [
                value for value in expected if value != key
            ]
            self._save_state(state)
        elif state.get("expected_purposes") is None:
            state["expected_purposes"] = sorted(expected)
            self._save_state(state)
        entry = (state.get("single_sends") or {}).get(key)
        if entry:
            try:
                single_send = self.api.get_single_send(entry["id"])
            except (KeyError, RuntimeError) as exc:
                if "returned 404" not in str(exc) and not isinstance(
                    exc, KeyError
                ):
                    raise
                single_send = None
            if single_send and single_send.get("status") == "scheduled":
                self.api.unschedule_single_send(entry["id"])

    def release_purpose(self, purpose: MailingPurpose) -> None:
        """Return a held purpose to automatic scheduling."""
        state = self._load_state()
        key = self._purpose_key(purpose)
        if not (state.get("single_sends") or {}).get(key):
            return
        expected = state.get("expected_purposes")
        if expected is None:
            return
        if key not in expected:
            state["expected_purposes"] = sorted(set(expected) | {key})
            self._save_state(state)

    @staticmethod
    def _purpose_key(purpose: MailingPurpose) -> str:
        return purpose.value

    @staticmethod
    def _normalized_send_to(value: dict | None) -> dict:
        value = value or {}
        return {
            "list_ids": sorted(str(item) for item in value.get("list_ids") or []),
            "segment_ids": sorted(
                str(item) for item in value.get("segment_ids") or []
            ),
            "all": bool(value.get("all")),
        }

    @staticmethod
    def _content_hash(subject: str, preheader: str, body: str) -> str:
        return hashlib.sha256(
            f"{subject}\n{preheader}\n{body}".encode("utf-8")
        ).hexdigest()

    @staticmethod
    def _rendered_hash(payload: dict, *, provider: bool = False) -> str:
        email_config = payload["email_config"]
        html_content = email_config["html_content"]
        if provider:
            html_content = _without_provider_injected_html(html_content)
        source = json.dumps(
            {
                "subject": email_config["subject"],
                "html_content": html_content,
                "plain_content": email_config["plain_content"],
                "send_to": payload["send_to"],
            },
            sort_keys=True,
            ensure_ascii=False,
        )
        return hashlib.sha256(source.encode("utf-8")).hexdigest()

    def _expected_single_send_payload(
        self,
        *,
        name: str,
        subject: str,
        body: str,
        preheader: str,
        send_to: dict,
    ) -> dict:
        normalized_send_to = self._normalized_send_to(send_to)
        if normalized_send_to["all"] or not (
            normalized_send_to["list_ids"]
            or normalized_send_to["segment_ids"]
        ):
            raise ValueError("Single Send must use bounded recipients")
        rendered = render_newsletter(
            body,
            use_template=True,
            preheader=preheader,
        )
        return {
            "name": name,
            "send_to": normalized_send_to,
            "email_config": {
                "subject": subject,
                "html_content": rendered.html,
                "plain_content": rendered.plain_text,
                "generate_plain_content": False,
                "editor": "design",
                "suppression_group_id": self.registry.suppression_group_id,
                "sender_id": self.registry.sender_id,
            },
        }

    def _verification_mismatches(
        self,
        single_send: dict,
        expected: dict,
    ) -> list[str]:
        mismatches: list[str] = []
        if single_send.get("name") != expected["name"]:
            mismatches.append("name")
        if self._normalized_send_to(
            single_send.get("send_to")
        ) != self._normalized_send_to(expected["send_to"]):
            mismatches.append("send_to")
        actual_config = single_send.get("email_config") or {}
        for field in (
            "subject",
            "html_content",
            "plain_content",
            "generate_plain_content",
            "editor",
            "suppression_group_id",
            "sender_id",
        ):
            actual_value = actual_config.get(field)
            expected_value = expected["email_config"][field]
            if field == "html_content" and not any(
                token in expected_value
                for token in PROVIDER_INJECTED_HTML_TOKENS
            ):
                actual_value = _without_provider_injected_html(actual_value)
            if actual_value != expected_value:
                mismatches.append(f"email_config.{field}")
        return mismatches

    def _cleanup_single_send(self, single_send: dict) -> None:
        identifier = str(single_send.get("id") or "")
        status = single_send.get("status")
        if not identifier:
            raise ProviderVerificationError(
                "cannot clean up a Single Send without an ID"
            )
        if status == "triggered":
            raise ProviderVerificationError(
                f"refusing to delete triggered Single Send {identifier}"
            )
        if status == "scheduled":
            self.api.unschedule_single_send(identifier)
        elif status != "draft":
            raise ProviderVerificationError(
                f"refusing to delete Single Send {identifier} "
                f"with unexpected status {status}"
            )
        self.api.delete_single_send(identifier)

    def _verify_created_single_send(
        self,
        identifier: str,
        expected: dict,
    ) -> dict:
        single_send = None
        mismatches: list[str] = []
        delays = (*self._created_verification_delays, None)
        for delay in delays:
            single_send = self.api.get_single_send(identifier)
            mismatches = self._verification_mismatches(
                single_send,
                expected,
            )
            if not mismatches:
                return single_send
            if delay is not None:
                self._sleep(delay)

        assert single_send is not None
        self._cleanup_single_send(single_send)
        raise ProviderVerificationError(
            "created Single Send verification failed: "
            + ", ".join(mismatches)
        )

    def _record_single_send(
        self,
        *,
        state: dict,
        key: str,
        single_send: dict,
        expected: dict,
        source_sha256: str,
    ) -> None:
        state.setdefault("single_sends", {})[key] = {
            "id": str(single_send["id"]),
            "name": expected["name"],
            "source_sha256": source_sha256,
            "rendered_sha256": self._rendered_hash(expected),
            "send_to": expected["send_to"],
            "provider_status": single_send.get("status"),
            "verification_status": "verified",
            "verified_at": self.now_fn().astimezone(timezone.utc).isoformat(),
        }
        self._save_state(state)

    def _provider_sends_with_name(self, name: str) -> list[dict]:
        rows = self.api.single_sends_by_name(name)
        details: list[dict] = []
        seen: set[str] = set()
        for row in rows:
            identifier = str(row.get("id") or "")
            if not identifier or identifier in seen:
                continue
            seen.add(identifier)
            details.append(self.api.get_single_send(identifier))
        return details

    @staticmethod
    def _keeper_sort_key(single_send: dict) -> tuple[int, str, str]:
        status_order = {
            "triggered": 0,
            "scheduled": 1,
            "draft": 2,
        }
        return (
            status_order.get(str(single_send.get("status")), 9),
            str(single_send.get("created_at") or ""),
            str(single_send.get("id") or ""),
        )

    def ensure_list(self, name: str) -> str:
        validate_sendgrid_name(name)
        try:
            return self.registry.list_id(name)
        except KeyError:
            matches = [
                item
                for item in self.api.marketing_lists()
                if item.get("name") == name
            ]
            if len(matches) > 1:
                raise ProviderVerificationError(
                    f"multiple SendGrid lists use locked name: {name}"
                )
            item = matches[0] if matches else self.api.create_list(name)
            identifier = str(item.get("id") or "")
            if not identifier:
                raise ValueError("SendGrid list returned no immutable ID")
            self.registry.register_list(name, identifier)
            return identifier

    def _with_internal_copy(self, send_to: dict) -> dict:
        # JP directive 2026-08-09: every Single Send delivers a copy to the
        # Internal: Send Copy audience (admin@ and Tiffany). A registry miss
        # fails the mailing loudly rather than sending without the copy.
        copy_id = self.registry.list_id(INTERNAL_SEND_COPY)
        merged = dict(send_to)
        list_ids = [str(item) for item in (merged.get("list_ids") or [])]
        if copy_id not in list_ids:
            list_ids.append(copy_id)
        merged["list_ids"] = list_ids
        return merged

    def create_draft(
        self,
        *,
        purpose: MailingPurpose,
        year: int,
        month: int,
        subject: str,
        body_md: str,
        preheader: str = "",
        send_to: dict,
    ) -> dict:
        clean_subject = subject.strip()
        clean_body = body_md.strip()
        clean_preheader = preheader.strip()
        if not clean_subject or not clean_body:
            raise ValueError("SendGrid draft requires subject and body")
        send_to = self._with_internal_copy(send_to)
        name = mailing_name(year, month, purpose)
        state = self._load_state()
        key = self._purpose_key(purpose)
        payload = self._expected_single_send_payload(
            name=name,
            subject=clean_subject,
            body=clean_body,
            preheader=clean_preheader,
            send_to=send_to,
        )
        source_sha256 = self._content_hash(
            clean_subject,
            clean_preheader,
            clean_body,
        )

        provider_sends = self._provider_sends_with_name(name)
        verified = [
            item
            for item in provider_sends
            if not self._verification_mismatches(item, payload)
        ]
        triggered = [
            item for item in provider_sends
            if item.get("status") == "triggered"
        ]
        mismatched_triggered = [
            item for item in triggered if item not in verified
        ]
        if mismatched_triggered:
            identifiers = ", ".join(
                str(item.get("id")) for item in mismatched_triggered
            )
            raise ProviderVerificationError(
                f"triggered Single Send content mismatch: {identifiers}"
            )
        verified_triggered = [
            item for item in verified
            if item.get("status") == "triggered"
        ]
        if len(verified_triggered) > 1:
            raise ProviderVerificationError(
                "multiple triggered Single Sends match the locked mailing"
            )

        keeper = min(verified, key=self._keeper_sort_key) if verified else None
        for item in provider_sends:
            if keeper is not None and item.get("id") == keeper.get("id"):
                continue
            self._cleanup_single_send(item)

        if keeper is None:
            if provider_sends:
                raise ProviderVerificationError(
                    "conflicting Single Sends were cleaned; "
                    "refusing creation on the same run"
                )
            if self.api.single_sends_by_name(name):
                raise ProviderVerificationError(
                    "provider cleanup left conflicting Single Sends"
                )
            created = self.api.create_single_send(payload)
            identifier = str(created.get("id") or "")
            if not identifier:
                raise ProviderVerificationError(
                    "SendGrid draft returned no immutable ID"
                )
            keeper = self._verify_created_single_send(identifier, payload)

        self._record_single_send(
            state=state,
            key=key,
            single_send=keeper,
            expected=payload,
            source_sha256=source_sha256,
        )
        return keeper

    def ensure_segment(
        self,
        *,
        purpose: MailingPurpose,
        year: int,
        month: int,
        query_dsl: str,
        parent_list_ids: list[str] | None = None,
    ) -> dict:
        if not query_dsl.strip():
            raise ValueError("SendGrid segment requires a query")
        name = mailing_name(year, month, purpose)
        state = self._load_state()
        key = self._purpose_key(purpose)
        existing = (state.get("segments") or {}).get(key)
        if existing:
            segment = self.api.segment(existing["id"])
            if segment.get("name") != name:
                raise ValueError("persisted SendGrid segment name mismatch")
            persisted_query = segment.get("query_dsl")
            if persisted_query and persisted_query != query_dsl:
                segment = self.api.update_segment(
                    existing["id"],
                    name=name,
                    query_dsl=query_dsl,
                    parent_list_ids=parent_list_ids,
                )
                state.setdefault("segments", {})[key].update({
                    "query_sha256": hashlib.sha256(query_dsl.encode()).hexdigest(),
                    "parent_list_ids": list(parent_list_ids or []),
                })
                self._save_state(state)
            return segment

        matches = [
            item
            for item in self.api.segments()
            if item.get("name") == name
        ]
        if len(matches) > 1:
            raise ProviderVerificationError(
                f"multiple SendGrid segments use locked name: {name}"
            )
        if matches:
            segment = self.api.segment(str(matches[0]["id"]))
            if (
                segment.get("query_dsl") != query_dsl
                or list(segment.get("parent_list_ids") or [])
                != list(parent_list_ids or [])
            ):
                segment = self.api.update_segment(
                    str(segment["id"]),
                    name=name,
                    query_dsl=query_dsl,
                    parent_list_ids=parent_list_ids,
                )
        else:
            segment = self.api.create_segment(
                name=name,
                query_dsl=query_dsl,
                parent_list_ids=parent_list_ids,
            )
        identifier = str(segment.get("id") or "")
        if not identifier:
            raise ValueError("SendGrid segment returned no immutable ID")
        state.setdefault("segments", {})[key] = {
            "id": identifier,
            "name": name,
            "query_sha256": hashlib.sha256(query_dsl.encode()).hexdigest(),
            "parent_list_ids": list(parent_list_ids or []),
        }
        self._save_state(state)
        return segment

    def single_send(self, purpose: MailingPurpose) -> dict:
        state = self._load_state()
        entry = (state.get("single_sends") or {}).get(
            self._purpose_key(purpose)
        )
        if not entry:
            raise KeyError(f"SendGrid draft is not registered: {purpose.value}")
        single_send = self.api.get_single_send(entry["id"])
        mismatches: list[str] = []
        if single_send.get("name") != entry.get("name"):
            mismatches.append("name")
        if self._normalized_send_to(
            single_send.get("send_to")
        ) != self._normalized_send_to(entry.get("send_to")):
            mismatches.append("send_to")
        try:
            rendered_sha256 = self._rendered_hash(single_send, provider=True)
        except (KeyError, TypeError):
            mismatches.append("rendered_content")
        else:
            if rendered_sha256 != entry.get("rendered_sha256"):
                mismatches.append("rendered_content")
        if mismatches:
            raise ProviderVerificationError(
                "recorded Single Send verification failed: "
                + ", ".join(mismatches)
            )
        return single_send

    def _schedule_verification_mismatches(
        self,
        *,
        single_send: dict,
        entry: dict,
        expected_send_at: str,
    ) -> list[str]:
        mismatches: list[str] = []
        if single_send.get("status") != "scheduled":
            mismatches.append("status")
        if single_send.get("send_at") != expected_send_at:
            mismatches.append("send_at")
        if single_send.get("name") != entry.get("name"):
            mismatches.append("name")
        if self._normalized_send_to(
            single_send.get("send_to")
        ) != self._normalized_send_to(entry.get("send_to")):
            mismatches.append("send_to")
        try:
            rendered_sha256 = self._rendered_hash(single_send, provider=True)
        except (KeyError, TypeError):
            mismatches.append("rendered_content")
        else:
            if rendered_sha256 != entry.get("rendered_sha256"):
                mismatches.append("rendered_content")
        return mismatches

    def schedule(
        self,
        purpose: MailingPurpose,
        send_at: datetime,
    ) -> dict:
        if send_at.tzinfo is None:
            raise ValueError("SendGrid schedule must be timezone aware")
        target = send_at.astimezone(timezone.utc)
        now = self.now_fn().astimezone(timezone.utc)
        if target <= now:
            raise ValueError("refusing to schedule a SendGrid mailing in the past")
        state = self._load_state()
        key = self._purpose_key(purpose)
        entry = (state.get("single_sends") or {}).get(key)
        if not entry:
            raise KeyError(f"SendGrid draft is not registered: {purpose.value}")
        single_send = self.api.get_single_send(entry["id"])
        status = single_send.get("status")
        if status == "triggered":
            return single_send
        if status not in {"draft", "scheduled"}:
            raise ValueError(f"unexpected Single Send status: {status}")

        formatted = target.strftime("%Y-%m-%dT%H:%M:%SZ")
        if status == "scheduled":
            if single_send.get("send_at") == formatted:
                mismatches = self._schedule_verification_mismatches(
                    single_send=single_send,
                    entry=entry,
                    expected_send_at=formatted,
                )
                if not mismatches:
                    return single_send
            self.api.unschedule_single_send(single_send["id"])
        self.api.schedule_single_send(single_send["id"], formatted)
        scheduled = self.api.get_single_send(single_send["id"])
        mismatches = self._schedule_verification_mismatches(
            single_send=scheduled,
            entry=entry,
            expected_send_at=formatted,
        )
        if mismatches:
            self._cleanup_single_send(scheduled)
            state.setdefault("single_sends", {}).pop(key, None)
            self._save_state(state)
            raise ProviderVerificationError(
                "Single Send schedule verification failed: "
                + ", ".join(mismatches)
            )
        entry.update({
            "provider_status": "scheduled",
            "send_at": formatted,
            "verified_at": self.now_fn().astimezone(timezone.utc).isoformat(),
        })
        self._save_state(state)
        return scheduled
