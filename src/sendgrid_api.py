"""Small, explicit client for the SendGrid APIs used by the TWY proof."""

from __future__ import annotations

import time
from typing import Any, Callable
from urllib.parse import quote

import requests


BASE_URL = "https://api.sendgrid.com/v3"


class SendGridAPIError(RuntimeError):
    pass


class SendGridJobFailed(RuntimeError):
    pass


class SendGridJobTimeout(RuntimeError):
    pass


class SendGridAPI:
    def __init__(
        self,
        api_key: str,
        session: requests.Session | None = None,
        sleep_fn: Callable[[float], None] = time.sleep,
    ):
        if not api_key:
            raise ValueError("SendGrid API key is required")
        self._api_key = api_key
        self._session = session or requests.Session()
        self._sleep = sleep_fn

    def _redact_text(self, text: str) -> str:
        return text.replace(self._api_key, "[REDACTED]")

    def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict | None = None,
        params: dict | None = None,
        allow_not_found: bool = False,
        attempts: int = 4,
    ) -> Any:
        url = path if path.startswith("https://") else f"{BASE_URL}{path}"
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Accept": "application/json",
        }
        if json is not None:
            headers["Content-Type"] = "application/json"

        response = None
        for attempt in range(attempts):
            response = self._session.request(
                method,
                url,
                headers=headers,
                json=json,
                params=params,
                timeout=30,
            )
            if response.status_code == 429 or response.status_code >= 500:
                if attempt == attempts - 1:
                    break
                retry_after = response.headers.get("Retry-After")
                delay = float(retry_after) if retry_after else float(2 ** attempt)
                self._sleep(delay)
                continue
            break

        assert response is not None
        if response.status_code == 404 and allow_not_found:
            return None
        if not 200 <= response.status_code < 300:
            body = self._redact_text(response.text)
            raise SendGridAPIError(
                f"SendGrid {method} {path} returned {response.status_code}: {body}"
            )
        if response.status_code == 204 or not response.text:
            return None
        return response.json()

    def account(self) -> dict:
        return self._request("GET", "/user/account")

    def create_list(self, name: str) -> dict:
        return self._request("POST", "/marketing/lists", json={"name": name})

    def list_contacts(self, list_id: str) -> list[dict]:
        payload = self._request(
            "POST",
            "/marketing/contacts/search",
            json={"query": f"CONTAINS(list_ids, '{list_id}')"},
        )
        contacts = payload.get("result", [])
        expected = payload.get("contact_count")
        if expected is not None and expected != len(contacts):
            raise SendGridAPIError(
                f"SendGrid contact search incomplete: expected {expected}, got {len(contacts)}"
            )
        return contacts

    def upsert_contacts(self, list_ids: list[str], contacts: list[dict]) -> str:
        payload = self._request(
            "PUT",
            "/marketing/contacts",
            json={"list_ids": list_ids, "contacts": contacts},
        )
        job_id = payload.get("job_id")
        if not job_id:
            raise SendGridAPIError("SendGrid contact upsert returned no job_id")
        return job_id

    def wait_contact_job(self, job_id: str, timeout_s: int = 120) -> dict:
        deadline = time.monotonic() + timeout_s
        while True:
            payload = self._request("GET", f"/marketing/contacts/imports/{job_id}")
            status = payload.get("status")
            if status == "completed":
                return payload
            if status in {"errored", "failed"}:
                raise SendGridJobFailed(f"SendGrid contact job {job_id} {status}")
            if time.monotonic() >= deadline:
                raise SendGridJobTimeout(f"SendGrid contact job {job_id} timed out")
            self._sleep(1.0)

    def add_global_unsubscribes(self, emails: list[str]) -> None:
        self._request(
            "POST",
            "/asm/suppressions/global",
            json={"recipient_emails": emails},
        )

    def get_global_unsubscribe(self, email: str) -> dict | None:
        payload = self._request(
            "GET",
            f"/asm/suppressions/global/{quote(email, safe='')}",
            allow_not_found=True,
        )
        return payload or None

    def get_bounce(self, email: str) -> dict | None:
        payload = self._request(
            "GET",
            f"/suppression/bounces/{quote(email, safe='')}",
            allow_not_found=True,
        )
        return payload or None

    def create_single_send(self, payload: dict) -> dict:
        return self._request("POST", "/marketing/singlesends", json=payload)

    def get_single_send(self, single_send_id: str) -> dict:
        return self._request("GET", f"/marketing/singlesends/{single_send_id}")

    def find_single_send_by_name(self, name: str) -> dict | None:
        path = "/marketing/singlesends?page_size=100"
        while path:
            payload = self._request("GET", path)
            for single_send in payload.get("result") or []:
                if single_send.get("name") == name:
                    return single_send
            path = payload.get("_metadata", {}).get("next")
        return None

    def schedule_single_send(self, single_send_id: str, send_at: str) -> dict:
        return self._request(
            "PUT",
            f"/marketing/singlesends/{single_send_id}/schedule",
            json={"send_at": send_at},
        )

    def single_send_stats(
        self,
        single_send_id: str,
        start_date: str,
    ) -> dict | None:
        return self._request(
            "GET",
            f"/marketing/stats/singlesends/{single_send_id}",
            params={"aggregated_by": "total", "start_date": start_date},
            allow_not_found=True,
        )

    def start_contact_export(self, list_ids: list[str]) -> dict:
        return self._request(
            "POST",
            "/marketing/contacts/exports",
            json={
                "list_ids": list_ids,
                "file_type": "csv",
                "notifications": {"email": False},
            },
        )

    def wait_contact_export(self, export_id: str, timeout_s: int = 180) -> dict:
        deadline = time.monotonic() + timeout_s
        while True:
            payload = self._request("GET", f"/marketing/contacts/exports/{export_id}")
            status = payload.get("status")
            if status == "ready":
                return payload
            if status == "failure":
                raise SendGridJobFailed(f"SendGrid contact export {export_id} failed")
            if time.monotonic() >= deadline:
                raise SendGridJobTimeout(f"SendGrid contact export {export_id} timed out")
            self._sleep(1.0)

    def snapshot(self, endpoint: str) -> Any:
        return self._request("GET", endpoint)
