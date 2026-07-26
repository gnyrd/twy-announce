"""Fail closed SendGrid draft and segment state for TWY mailings."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Callable

from newsletter_rendering import render_newsletter
from sendgrid_mailings import (
    MailingPurpose,
    mailing_name,
    validate_sendgrid_name,
)
from twy_platform import locked_write


EXPECTED_ACCOUNT_EMAIL = "admin@tiffanywoodyoga.com"
EXPECTED_SENDER_EMAIL = "hello@tiffanywoodyoga.com"
UNSUBSCRIBE_GROUP_NAME = "Email: Unsubscribed"


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
    ):
        self.api = api
        self.registry = registry
        self.state_path = state_path
        self.now_fn = now_fn or (lambda: datetime.now(timezone.utc))

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

    @staticmethod
    def _purpose_key(purpose: MailingPurpose) -> str:
        return purpose.value

    def ensure_list(self, name: str) -> str:
        validate_sendgrid_name(name)
        try:
            return self.registry.list_id(name)
        except KeyError:
            created = self.api.create_list(name)
            identifier = str(created.get("id") or "")
            if not identifier:
                raise ValueError("SendGrid list returned no immutable ID")
            self.registry.register_list(name, identifier)
            return identifier

    def create_draft(
        self,
        *,
        purpose: MailingPurpose,
        year: int,
        month: int,
        subject: str,
        body_md: str,
        send_to: dict,
    ) -> dict:
        clean_subject = subject.strip()
        clean_body = body_md.strip()
        if not clean_subject or not clean_body:
            raise ValueError("SendGrid draft requires subject and body")
        name = mailing_name(year, month, purpose)
        state = self._load_state()
        key = self._purpose_key(purpose)
        existing = (state.get("single_sends") or {}).get(key)
        if existing:
            single_send = self.api.get_single_send(existing["id"])
            if single_send.get("name") != name:
                raise ValueError("persisted Single Send name mismatch")
            return single_send

        list_ids = list(send_to.get("list_ids") or [])
        segment_ids = list(send_to.get("segment_ids") or [])
        if send_to.get("all") or not (list_ids or segment_ids):
            raise ValueError("Single Send must use bounded recipients")

        rendered = render_newsletter(clean_body)
        payload = {
            "name": name,
            "send_to": {
                "list_ids": list_ids,
                "segment_ids": segment_ids,
                "all": False,
            },
            "email_config": {
                "subject": clean_subject,
                "html_content": rendered.html,
                "plain_content": rendered.plain_text,
                "generate_plain_content": False,
                "editor": "design",
                "suppression_group_id": self.registry.suppression_group_id,
                "sender_id": self.registry.sender_id,
            },
        }
        single_send = self.api.create_single_send(payload)
        identifier = str(single_send.get("id") or "")
        if not identifier:
            raise ValueError("SendGrid draft returned no immutable ID")
        state.setdefault("single_sends", {})[key] = {
            "id": identifier,
            "name": name,
            "source_sha256": hashlib.sha256(
                f"{clean_subject}\n{clean_body}".encode()
            ).hexdigest(),
            "send_to": payload["send_to"],
        }
        self._save_state(state)
        return single_send

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
                raise ValueError("persisted SendGrid segment query mismatch")
            return segment

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
        return self.api.get_single_send(entry["id"])

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
        single_send = self.single_send(purpose)
        status = single_send.get("status")
        if status == "triggered":
            return single_send
        if status not in {"draft", "scheduled"}:
            raise ValueError(f"unexpected Single Send status: {status}")

        formatted = target.strftime("%Y-%m-%dT%H:%M:%SZ")
        if status == "scheduled":
            if single_send.get("send_at") == formatted:
                return single_send
            self.api.unschedule_single_send(single_send["id"])
        return self.api.schedule_single_send(single_send["id"], formatted)
