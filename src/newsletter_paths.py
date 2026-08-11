"""Newsletter paths and prompt files, for the announce side.

NOT the newsletter data layer. classes/dashboard/newsletter.py owns writing a
newsletter, and every writer lands there: Tweee via /api/newsletters and Tiff
via the draft editor. That module is where versioning and the write firehose
live.

This file used to be named newsletter.py, so `import newsletter` had two
answers decided by sys.path order, and both defined save_newsletter. On
2026-08-11 that cost a whole build in the wrong module. Renamed so the
ambiguity cannot recur.
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


def save_prompt(year: int, month: int, audience: str, text: str) -> Path:
    """Save prompt text to disk. Returns the path."""
    p = prompt_path(year, month, audience)
    locked_write(p, text)
    return p


def current_year_month() -> tuple[int, int]:
    """Current year and month in Mountain Time."""
    now = datetime.now(MOUNTAIN)
    return now.year, now.month
