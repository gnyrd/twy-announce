#!/usr/bin/env python3
"""JWT cache and refresh helpers for Marvelous embedded reports."""

from __future__ import annotations

import base64
import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).parent.parent
JWT_CACHE_FILE = PROJECT_ROOT / ".jwt_cache.json"
METABASE_QUERY_URL = "https://reports.heymarv.com/api/embed/card/{jwt_token}/query/json"

TOKEN_REFRESH_BUFFER_HOURS_DEFAULT = 24


class ReportJWTError(Exception):
    """Raised when JWT retrieval or report querying fails."""


@dataclass(frozen=True)
class ReportKey:
    category: str
    report_id: int

    @property
    def key(self) -> str:
        return f"{self.category}/{self.report_id}"


def _decode_jwt_payload(jwt_token: str) -> Optional[Dict[str, Any]]:
    try:
        parts = jwt_token.split('.')
        if len(parts) != 3:
            return None

        payload = parts[1]
        padding = (4 - len(payload) % 4) % 4
        payload += '=' * padding
        decoded = base64.urlsafe_b64decode(payload)
        return json.loads(decoded)
    except Exception:
        return None


def _is_token_valid(jwt_token: str, buffer_hours: int) -> bool:
    payload = _decode_jwt_payload(jwt_token)
    if not payload or 'exp' not in payload:
        return False

    exp_timestamp = int(payload['exp'])
    now_timestamp = int(time.time())
    return (exp_timestamp - now_timestamp) > (buffer_hours * 3600)


def _load_cache(cache_file: Path = JWT_CACHE_FILE) -> Dict[str, Any]:
    if not cache_file.exists():
        return {'version': 2, 'reports': {}}

    try:
        data = json.loads(cache_file.read_text())
    except Exception:
        return {'version': 2, 'reports': {}}

    # Normalize legacy schema: {jwt_token, report_id}
    if 'reports' not in data:
        reports: Dict[str, Any] = {}
        token = data.get('jwt_token')
        report_id = data.get('report_id')
        if token and report_id:
            reports[f'users/{int(report_id)}'] = {
                'jwt_token': token,
                'cached_at': int(time.time()),
            }
        data = {'version': 2, 'reports': reports}

    if 'reports' not in data or not isinstance(data['reports'], dict):
        data['reports'] = {}

    return data


def _save_cache(cache_data: Dict[str, Any], cache_file: Path = JWT_CACHE_FILE) -> None:
    cache_file.write_text(json.dumps(cache_data, indent=2))


def get_cached_report_jwt(
    report_id: int,
    category: str = 'users',
    buffer_hours: int = TOKEN_REFRESH_BUFFER_HOURS_DEFAULT,
    cache_file: Path = JWT_CACHE_FILE,
) -> Optional[str]:
    cache = _load_cache(cache_file)
    key = ReportKey(category=category, report_id=report_id).key
    entry = cache.get('reports', {}).get(key)
    if not entry:
        return None

    token = entry.get('jwt_token')
    if not token:
        return None

    if _is_token_valid(token, buffer_hours=buffer_hours):
        return token

    return None


def save_report_jwt(
    report_id: int,
    category: str,
    jwt_token: str,
    cache_file: Path = JWT_CACHE_FILE,
) -> None:
    cache = _load_cache(cache_file)
    key = ReportKey(category=category, report_id=report_id).key
    cache.setdefault('reports', {})[key] = {
        'jwt_token': jwt_token,
        'cached_at': int(time.time()),
    }
    _save_cache(cache, cache_file)


def _extract_embed_token(iframe_src: str) -> Optional[str]:
    if not iframe_src:
        return None
    match = re.search(r'/embed/question/([^?#]+)', iframe_src)
    if not match:
        return None
    return match.group(1)


def refresh_report_jwt(
    report_id: int,
    category: str = 'users',
    headless: bool = True,
    cache_file: Path = JWT_CACHE_FILE,
) -> str:
    """Login with Playwright, open report page, extract embed JWT, and cache it."""
    load_dotenv(PROJECT_ROOT / '.env')

    username = os.getenv('MARVELOUS_TWY_USERNAME')
    password = os.getenv('MARVELOUS_TWY_PASSWORD')
    secondary_password = os.getenv('MARVELOUS_SECONDARY_PASSWORD')

    if not username or not password or not secondary_password:
        raise ReportJWTError(
            'Missing MARVELOUS_TWY_USERNAME / MARVELOUS_TWY_PASSWORD / MARVELOUS_SECONDARY_PASSWORD in .env'
        )

    try:
        from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout
    except Exception as e:
        raise ReportJWTError(f'Playwright import failed: {e}')

    report_url = f'https://app.heymarvelous.com/reports/{category}/{report_id}'
    login_url = f'https://app.heymarvelous.com/login?redirect=%2Freports%2F{category}%2F{report_id}'

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context()
        page = context.new_page()

        try:
            page.goto(login_url, wait_until='domcontentloaded', timeout=60000)

            # Primary login
            page.get_by_role('textbox', name='Email').click()
            page.get_by_role('textbox', name='Email').fill(username)
            page.get_by_role('textbox', name='Password').click()
            page.get_by_role('textbox', name='Password').fill(password)
            page.get_by_role('button', name='Log in').click()

            # Secondary unlock (if presented)
            page.wait_for_timeout(2000)
            unlock_buttons = page.get_by_role('button', name='Unlock')
            if unlock_buttons.count() > 0:
                page.get_by_role('textbox', name='Password').click()
                page.get_by_role('textbox', name='Password').fill(secondary_password)
                unlock_buttons.first.click()

            page.goto(report_url, wait_until='domcontentloaded', timeout=60000)
            page.wait_for_selector("iframe[src*='reports.heymarv.com/embed/question/']", timeout=90000)

            iframe_src = page.locator("iframe[src*='reports.heymarv.com/embed/question/']").first.get_attribute('src') or ''
            token = _extract_embed_token(iframe_src)
            if not token:
                raise ReportJWTError('Could not extract embed token from report iframe src')

            save_report_jwt(report_id=report_id, category=category, jwt_token=token, cache_file=cache_file)
            return token

        except PlaywrightTimeout as e:
            raise ReportJWTError(f'Timed out refreshing report JWT for {category}/{report_id}: {e}')
        except Exception as e:
            if isinstance(e, ReportJWTError):
                raise
            raise ReportJWTError(f'Failed refreshing report JWT for {category}/{report_id}: {e}')
        finally:
            context.close()
            browser.close()


def get_report_jwt(
    report_id: int,
    category: str = 'users',
    force_refresh: bool = False,
    buffer_hours: int = TOKEN_REFRESH_BUFFER_HOURS_DEFAULT,
    cache_file: Path = JWT_CACHE_FILE,
) -> str:
    if not force_refresh:
        cached = get_cached_report_jwt(
            report_id=report_id,
            category=category,
            buffer_hours=buffer_hours,
            cache_file=cache_file,
        )
        if cached:
            return cached

    return refresh_report_jwt(
        report_id=report_id,
        category=category,
        cache_file=cache_file,
    )


def query_report_rows(jwt_token: str, timeout: int = 45) -> List[Dict[str, Any]]:
    url = METABASE_QUERY_URL.format(jwt_token=jwt_token)
    resp = requests.get(url, timeout=timeout)
    resp.raise_for_status()
    payload = resp.json()
    if not isinstance(payload, list):
        raise ReportJWTError('Unexpected report payload type (expected list)')
    return payload


def fetch_report_rows(
    report_id: int,
    category: str = 'users',
    force_refresh: bool = False,
    buffer_hours: int = TOKEN_REFRESH_BUFFER_HOURS_DEFAULT,
    cache_file: Path = JWT_CACHE_FILE,
) -> List[Dict[str, Any]]:
    """Fetch report rows using cached JWT; refresh/retry once on auth failure."""
    jwt_token = get_report_jwt(
        report_id=report_id,
        category=category,
        force_refresh=force_refresh,
        buffer_hours=buffer_hours,
        cache_file=cache_file,
    )

    try:
        return query_report_rows(jwt_token)
    except requests.HTTPError as e:
        status = e.response.status_code if e.response is not None else None
        if status in (401, 403):
            refreshed = get_report_jwt(
                report_id=report_id,
                category=category,
                force_refresh=True,
                buffer_hours=buffer_hours,
                cache_file=cache_file,
            )
            return query_report_rows(refreshed)
        raise

