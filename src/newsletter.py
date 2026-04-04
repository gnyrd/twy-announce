"""
Newsletter data layer -- path management, prompt loading, newsletter saving.
Uses twy_paths for filesystem root resolution.
"""
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from twy_paths import newsletters_dir

NEWSLETTERS_DIR = newsletters_dir()
MOUNTAIN = ZoneInfo("America/Denver")


def prompt_path(year: int, month: int, audience: str) -> Path:
    """Path to prompt file. audience: 'lifestyle' or 'non-lifestyle'."""
    return NEWSLETTERS_DIR / f"{year:04d}-{month:02d}" / f"prompt-{audience}.txt"


def newsletter_path(year: int, month: int, audience: str) -> Path:
    """Path to authored newsletter file."""
    return NEWSLETTERS_DIR / f"{year:04d}-{month:02d}" / f"{audience}.md"


def load_prompt(year: int, month: int, audience: str) -> str | None:
    """Return prompt text, or None if file does not exist."""
    p = prompt_path(year, month, audience)
    if p.exists():
        return p.read_text()
    return None


def save_newsletter(year: int, month: int, audience: str, subject: str, body: str) -> Path:
    """Save authored newsletter to disk. Returns the path."""
    p = newsletter_path(year, month, audience)
    p.parent.mkdir(parents=True, exist_ok=True)
    content = f"# {subject}\n\n{body}"
    p.write_text(content)
    return p


def save_prompt(year: int, month: int, audience: str, text: str) -> Path:
    """Save prompt text to disk. Returns the path."""
    p = prompt_path(year, month, audience)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    return p


def current_year_month() -> tuple[int, int]:
    """Current year and month in Mountain Time."""
    now = datetime.now(MOUNTAIN)
    return now.year, now.month
