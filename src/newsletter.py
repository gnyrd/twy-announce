"""
Newsletter data layer -- path management, prompt loading, newsletter saving.
Uses twy_paths for filesystem root resolution.
"""
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from twy_paths import newsletter_path, newsletter_prompt_path as prompt_path
from twy_platform import locked_write

MOUNTAIN = ZoneInfo("America/Denver")


def load_prompt(year: int, month: int, audience: str) -> str | None:
    """Return prompt text, or None if file does not exist."""
    p = prompt_path(year, month, audience)
    if p.exists():
        return p.read_text()
    return None


def save_newsletter(year: int, month: int, audience: str, subject: str, body: str) -> Path:
    """Save authored newsletter to disk. Returns the path."""
    p = newsletter_path(year, month, audience)
    content = f"# {subject}\n\n{body}"
    locked_write(p, content)
    return p


def save_prompt(year: int, month: int, audience: str, text: str) -> Path:
    """Save prompt text to disk. Returns the path."""
    p = prompt_path(year, month, audience)
    locked_write(p, text)
    return p


def current_year_month() -> tuple[int, int]:
    """Current year and month in Mountain Time."""
    now = datetime.now(MOUNTAIN)
    return now.year, now.month
