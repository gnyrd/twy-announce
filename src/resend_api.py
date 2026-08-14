"""Resend transport for TWY journey drips.

Why this exists: the TWY SendGrid account owns Marketing Campaigns but not the
Email API, so `/mail/send` answers `401 Maximum credits exceeded` and the
welcome journey has never delivered a single email. JP declined the SendGrid
Email plan on 2026-08-14 (19 USD/mo for 50k sends, against TWY volume of
roughly 8 emails per new member). Resend carries journey drips ONLY. The
newsletters stay on SendGrid Single Sends, which are a separately provisioned
product and are healthy.

`send_mail` deliberately accepts the SendGrid-shaped payload that
`run_journey_emails.build_payload` already produces, and translates it here.
The message stays composed in exactly one place, so a change to subject,
body, preheader or reply-to lands once rather than twice.
"""

from __future__ import annotations

import re
import time
from typing import Any, Callable

import requests


BASE_URL = "https://api.resend.com"

# Cloudflare fronts api.resend.com and bans the default Python-urllib
# User-Agent, answering every endpoint with 403 and a bare non-JSON body
# "error code: 1010" that reads exactly like a rejected key. requests sets its
# own User-Agent so this is belt and braces, but the header is explicit so
# nobody has to rediscover that (2026-08-14, cost about 20 minutes).
USER_AGENT = "twy-announce/1.0"

# Resend tag names and values accept ASCII letters, numbers, underscores and
# dashes only. TWY campaign ids look like
# "journey:yoga_lifestyle_welcome_2024_05:0", so the colons are replaced.
_TAG_SAFE = re.compile(r"[^A-Za-z0-9_-]")


class ResendAPIError(RuntimeError):
    pass


def _tag_value(value: str) -> str:
    return _TAG_SAFE.sub("_", str(value))


def _address(entry: dict | None) -> str:
    """Render a SendGrid {email, name} pair as a Resend address string."""
    if not entry:
        return ""
    email = str(entry.get("email") or "").strip()
    name = str(entry.get("name") or "").strip()
    if not email:
        return ""
    return f"{name} <{email}>" if name else email


def to_resend_payload(payload: dict, *, from_address: str) -> dict:
    """Translate one SendGrid /mail/send body into a Resend /emails body.

    from_address is passed in rather than read off the SendGrid payload
    because Resend will only accept a sender on its own verified domain,
    mail.tiffanywoodyoga.com. The reply-to is NOT rewritten: it stays on the
    apex so replies keep landing in Tiff Google Workspace inbox.

    Dropped on purpose, because they have no Resend equivalent and are not
    wanted here: `asm` (the SendGrid unsubscribe group, and the journey footer
    no longer renders unsubscribe links per JP 2026-08-14), `mail_settings`
    and `tracking_settings` (Resend tracking is configured on the domain, and
    both click and open tracking are off there).
    """
    personalizations = payload.get("personalizations") or [{}]
    recipients = [
        str(item.get("email") or "").strip()
        for item in (personalizations[0].get("to") or [])
        if str(item.get("email") or "").strip()
    ]
    if not recipients:
        raise ResendAPIError("a Resend send needs at least one recipient")

    subject = str(payload.get("subject") or "").strip()
    if not subject:
        raise ResendAPIError("a Resend send needs a subject")

    html = ""
    text = ""
    for part in payload.get("content") or []:
        if part.get("type") == "text/html":
            html = str(part.get("value") or "")
        elif part.get("type") == "text/plain":
            text = str(part.get("value") or "")
    if not html and not text:
        raise ResendAPIError("a Resend send needs an html or text body")

    body: dict[str, Any] = {
        "from": from_address,
        "to": recipients,
        "subject": subject,
    }
    if html:
        body["html"] = html
    if text:
        body["text"] = text

    reply_to = _address(payload.get("reply_to"))
    if reply_to:
        body["reply_to"] = reply_to

    tags = [
        {"name": _tag_value(name), "value": _tag_value(value)}
        for name, value in sorted((payload.get("custom_args") or {}).items())
    ]
    if tags:
        body["tags"] = tags
    return body


class ResendAPI:
    """Minimal Resend client, shaped like SendGridAPI so callers can swap."""

    def __init__(
        self,
        api_key: str,
        *,
        from_address: str,
        sending_domain: str = "mail.tiffanywoodyoga.com",
        session: requests.Session | None = None,
        sleep_fn: Callable[[float], None] = time.sleep,
    ):
        if not api_key:
            raise ValueError("Resend API key is required")
        if not from_address:
            raise ValueError("Resend sender address is required")
        # Fail loud rather than let Resend reject every send at run time, and
        # rather than silently send from a domain that is not authenticated.
        if not from_address.rstrip(">").strip().endswith("@" + sending_domain):
            raise ValueError(
                f"Resend sender must be on {sending_domain}, got {from_address}"
            )
        self._api_key = api_key
        self._from = from_address
        self._session = session or requests.Session()
        self._sleep = sleep_fn

    def _redact_text(self, text: str) -> str:
        return text.replace(self._api_key, "[REDACTED]")

    def send_mail(self, payload: dict, *, attempts: int = 4) -> dict:
        """Send one journey email. Accepts the SendGrid-shaped payload."""
        body = to_resend_payload(payload, from_address=self._from)
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        }
        last_error = ""
        for attempt in range(attempts):
            response = self._session.post(
                f"{BASE_URL}/emails",
                json=body,
                headers=headers,
                timeout=30,
            )
            if response.status_code < 300:
                return response.json()
            last_error = self._redact_text(
                f"{response.status_code} {response.text[:400]}"
            )
            # 429 is rate limiting and 5xx is theirs; anything else is ours and
            # will fail again identically, so do not burn retries on it.
            if response.status_code != 429 and response.status_code < 500:
                break
            if attempt < attempts - 1:
                self._sleep(2 ** attempt)
        raise ResendAPIError(f"Resend send failed: {last_error}")
