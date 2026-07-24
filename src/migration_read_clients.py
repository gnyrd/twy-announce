"""Endpoint-locked read clients for the TWY Mailchimp to SendGrid migration."""

from __future__ import annotations

from dataclasses import dataclass
import re
import time
from typing import Any, Iterable
from urllib.parse import urlsplit

import requests


class EndpointNotAllowed(RuntimeError):
    pass


class ProviderReadError(RuntimeError):
    pass


@dataclass(frozen=True)
class ContactLookup:
    contacts: dict[str, dict]
    absent: frozenset[str]
    errors: dict[str, str]


def _chunks(values: list[str], size: int) -> Iterable[list[str]]:
    for start in range(0, len(values), size):
        yield values[start:start + size]


class _ReadClient:
    def __init__(self, secret: str, session=None, sleep_fn=time.sleep):
        if not secret:
            raise ValueError("provider credential is required")
        self._secret = secret
        self._session = session or requests.Session()
        self._sleep = sleep_fn
        self._audit: list[dict[str, Any]] = []

    @property
    def audit(self) -> tuple[dict[str, Any], ...]:
        return tuple(self._audit)

    def _redact(self, value: str) -> str:
        return value.replace(self._secret, "[REDACTED]")

    def _perform(
        self,
        method: str,
        url: str,
        *,
        headers: dict,
        json: dict | None = None,
        params: dict | None = None,
        allow_not_found: bool = False,
        attempts: int = 4,
    ) -> Any:
        response = None
        path = urlsplit(url).path.removeprefix("/v3") or "/"
        for attempt in range(attempts):
            response = self._session.request(
                method,
                url,
                headers=headers,
                json=json,
                params=params,
                timeout=45,
            )
            if response.status_code == 429 or response.status_code >= 500:
                if attempt == attempts - 1:
                    break
                retry_after = response.headers.get("Retry-After")
                self._sleep(float(retry_after) if retry_after else 2 ** attempt)
                continue
            break
        assert response is not None
        self._audit.append({
            "method": method,
            "path": path,
            "status": response.status_code,
        })
        if response.status_code == 404 and allow_not_found:
            return None
        if not 200 <= response.status_code < 300:
            raise ProviderReadError(
                self._redact(
                    f"{method} {path} returned {response.status_code}: {response.text}"
                )
            )
        return None if not response.text else response.json()


class ReadOnlyMailchimpAPI(_ReadClient):
    STATUSES = (
        "subscribed",
        "unsubscribed",
        "cleaned",
        "pending",
        "transactional",
        "archived",
    )

    def __init__(
        self,
        server_prefix: str,
        api_key: str,
        audience_id: str,
        session=None,
        sleep_fn=time.sleep,
    ):
        super().__init__(api_key, session, sleep_fn)
        self.server_prefix = server_prefix
        self.audience_id = audience_id
        self.base_url = f"https://{server_prefix}.api.mailchimp.com/3.0"
        self.coverage_errors: dict[str, str] = {}

    def _allowed_path(self, path: str) -> bool:
        audience = re.escape(self.audience_id)
        patterns = (
            rf"/lists/{audience}",
            rf"/lists/{audience}/members",
            rf"/lists/{audience}/merge-fields",
            rf"/lists/{audience}/segments",
            rf"/lists/{audience}/segments/\d+",
            rf"/lists/{audience}/segments/\d+/members",
            r"/customer-journeys/journeys",
            r"/customer-journeys/journeys/\d+/steps",
        )
        return any(re.fullmatch(pattern, path) for pattern in patterns)

    def _request(self, method: str, path: str, **kwargs):
        if method != "GET" or not self._allowed_path(path):
            raise EndpointNotAllowed(f"Mailchimp endpoint not allowed: {method} {path}")
        return self._perform(
            method,
            f"{self.base_url}{path}",
            headers={"Authorization": f"apikey {self._secret}"},
            **kwargs,
        )

    def _paginate(self, path: str, result_key: str, params=None, page_size=1000):
        items: list[dict] = []
        offset = 0
        expected: int | None = None
        while True:
            query = dict(params or {})
            query.update({"count": page_size, "offset": offset})
            payload = self._request("GET", path, params=query)
            page = payload.get(result_key) or []
            page_total = int(payload.get("total_items", len(items) + len(page)))
            if expected is None:
                expected = page_total
            elif page_total != expected:
                raise ProviderReadError(
                    f"Mailchimp {path} total changed during pagination"
                )
            items.extend(page)
            if not page or len(items) >= expected:
                break
            offset += len(page)
        if expected is None or len(items) != expected:
            raise ProviderReadError(
                f"Mailchimp {path} pagination incomplete: {len(items)} of {expected}"
            )
        return items

    def members_for_status(self, status: str, page_size: int = 1000) -> list[dict]:
        if status not in self.STATUSES:
            raise ValueError(f"unsupported Mailchimp status: {status}")
        members = self._paginate(
            f"/lists/{self.audience_id}/members",
            "members",
            {"status": status},
            page_size,
        )
        mismatches = [
            member.get("email_address", "<unknown>")
            for member in members
            if member.get("status") not in {status, None}
        ]
        if mismatches:
            raise ProviderReadError(
                f"Mailchimp status mismatch for {status}: {len(mismatches)} members"
            )
        return members

    def collect_members(self) -> dict[str, list[dict]]:
        result: dict[str, list[dict]] = {}
        self.coverage_errors.clear()
        for status in self.STATUSES:
            try:
                result[status] = self.members_for_status(status)
            except ProviderReadError as error:
                if status != "archived":
                    raise
                result[status] = []
                self.coverage_errors[status] = str(error)
        return result

    def list_info(self) -> dict:
        return self._request("GET", f"/lists/{self.audience_id}")

    def merge_fields(self) -> list[dict]:
        return self._paginate(
            f"/lists/{self.audience_id}/merge-fields",
            "merge_fields",
        )

    def segments(self) -> list[dict]:
        return self._paginate(
            f"/lists/{self.audience_id}/segments",
            "segments",
        )

    def journey_steps(self, journey_id: int) -> dict:
        return self._request(
            "GET",
            f"/customer-journeys/journeys/{journey_id}/steps",
        )

    def inventory(self, journey_id: int) -> dict[str, Any]:
        return {
            "list": self.list_info(),
            "merge_fields": self.merge_fields(),
            "segments": self.segments(),
            "journey": {
                "id": journey_id,
                "steps": self.journey_steps(journey_id),
            },
        }


class ReadOnlySendGridAPI(_ReadClient):
    BASE_URL = "https://api.sendgrid.com/v3"
    _GET_PATTERNS = (
        r"/user/account",
        r"/marketing/lists",
        r"/marketing/field_definitions",
        r"/asm/groups",
        r"/asm/groups/\d+",
        r"/asm/groups/\d+/suppressions",
        r"/asm/suppressions/global",
        r"/suppression/bounces",
        r"/suppression/blocks",
        r"/suppression/invalid_emails",
        r"/suppression/spam_reports",
    )

    def __init__(self, api_key: str, session=None, sleep_fn=time.sleep):
        super().__init__(api_key, session, sleep_fn)

    def _assert_allowed(self, method: str, path: str):
        clean_path = urlsplit(path).path.removeprefix("/v3")
        if method == "POST" and clean_path == "/marketing/contacts/search/emails":
            return
        if method == "GET" and any(
            re.fullmatch(pattern, clean_path) for pattern in self._GET_PATTERNS
        ):
            return
        raise EndpointNotAllowed(f"SendGrid endpoint not allowed: {method} {clean_path}")

    def _request(self, method: str, path: str, **kwargs):
        self._assert_allowed(method, path)
        url = path if path.startswith("https://") else f"{self.BASE_URL}{path}"
        return self._perform(
            method,
            url,
            headers={
                "Authorization": f"Bearer {self._secret}",
                "Accept": "application/json",
            },
            **kwargs,
        )

    def account(self) -> dict:
        return self._request("GET", "/user/account")

    def contacts_by_emails(self, emails: Iterable[str]) -> ContactLookup:
        normalized = list(dict.fromkeys(
            email.strip().lower() for email in emails if email.strip()
        ))
        contacts: dict[str, dict] = {}
        absent: set[str] = set()
        errors: dict[str, str] = {}
        for batch in _chunks(normalized, 100):
            payload = self._request(
                "POST",
                "/marketing/contacts/search/emails",
                json={"emails": batch},
                allow_not_found=True,
            )
            if payload is None:
                absent.update(batch)
                continue
            result = payload.get("result") or {}
            for email in batch:
                entry = result.get(email)
                if not entry:
                    errors[email] = "missing result entry"
                elif entry.get("contact"):
                    contacts[email] = entry["contact"]
                elif entry.get("error") == "contact not found":
                    absent.add(email)
                else:
                    errors[email] = str(entry.get("error") or "unknown lookup error")
        return ContactLookup(contacts, frozenset(absent), errors)

    def _next_link_collection(self, path: str, result_key: str) -> list[dict]:
        items: list[dict] = []
        next_path: str | None = path
        seen: set[str] = set()
        while next_path:
            if next_path in seen:
                raise ProviderReadError(f"SendGrid pagination loop at {next_path}")
            seen.add(next_path)
            payload = self._request("GET", next_path)
            items.extend(payload.get(result_key) or [])
            next_path = (payload.get("_metadata") or {}).get("next")
        return items

    def _offset_collection(self, path: str, limit=500) -> list[dict]:
        items: list[dict] = []
        offset = 0
        while True:
            payload = self._request(
                "GET",
                path,
                params={"limit": limit, "offset": offset},
            )
            page = payload if isinstance(payload, list) else (
                payload.get("result") or payload.get("suppressions") or []
            )
            items.extend(page)
            if len(page) < limit:
                break
            offset += len(page)
        return items

    def marketing_lists(self) -> list[dict]:
        return self._next_link_collection(
            "/marketing/lists?page_size=1000",
            "result",
        )

    def custom_fields(self) -> list[dict]:
        payload = self._request("GET", "/marketing/field_definitions")
        return (payload.get("custom_fields") or []) + (
            payload.get("reserved_fields") or []
        )

    def groups(self) -> list[dict]:
        payload = self._request("GET", "/asm/groups")
        return payload if isinstance(payload, list) else payload.get("result") or []

    def group_suppressions(self, group_id: int) -> list[dict]:
        return self._offset_collection(f"/asm/groups/{group_id}/suppressions")

    def global_suppressions(self) -> list[dict]:
        return self._offset_collection("/asm/suppressions/global")

    def bounces(self) -> list[dict]:
        return self._offset_collection("/suppression/bounces")

    def blocks(self) -> list[dict]:
        return self._offset_collection("/suppression/blocks")

    def invalid_emails(self) -> list[dict]:
        return self._offset_collection("/suppression/invalid_emails")

    def spam_reports(self) -> list[dict]:
        return self._offset_collection("/suppression/spam_reports")

    @staticmethod
    def _emails(items: Iterable[dict]) -> set[str]:
        return {
            str(item.get("email") or item.get("recipient_email") or "").lower()
            for item in items
            if item.get("email") or item.get("recipient_email")
        }

    def safety_states(self, emails: Iterable[str]) -> dict[str, dict[str, Any]]:
        requested = sorted({email.strip().lower() for email in emails})
        lookup = self.contacts_by_emails(requested)
        global_suppressed = self._emails(self.global_suppressions())
        groups = self.groups()
        group_suppressed: set[str] = set()
        for group in groups:
            group_suppressed.update(
                self._emails(self.group_suppressions(int(group["id"])))
            )
        bounced = self._emails(self.bounces())
        blocked = self._emails(self.blocks())
        invalid = self._emails(self.invalid_emails())
        spam = self._emails(self.spam_reports())
        result: dict[str, dict[str, Any]] = {}
        for email in requested:
            result[email] = {
                "contact": lookup.contacts.get(email),
                "confirmed_absent": email in lookup.absent,
                "lookup_error": lookup.errors.get(email),
                "global_suppressed": email in global_suppressed,
                "group_suppressed": email in group_suppressed,
                "bounced": email in bounced,
                "blocked": email in blocked,
                "invalid": email in invalid,
                "spam_reported": email in spam,
            }
        return result

    def inventory(self) -> dict[str, Any]:
        return {
            "account": self.account(),
            "lists": self.marketing_lists(),
            "custom_fields": self.custom_fields(),
            "groups": self.groups(),
        }
