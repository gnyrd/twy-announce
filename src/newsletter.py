"""
Newsletter data layer -- path management, prompt loading, newsletter saving.
Uses twy_paths for filesystem root resolution.
"""
from datetime import datetime
import logging
import os
from pathlib import Path
from zoneinfo import ZoneInfo

from twy_paths import (
    newsletter_path,
    newsletter_prompt_path as prompt_path,
    newsletter_versions_dir,
)
from twy_platform import locked_write
from twy_platform.edits import (
    NEWSLETTER,
    backup_before_write,
    firehose,
)

MOUNTAIN = ZoneInfo("America/Denver")


def load_prompt(year: int, month: int, audience: str) -> str | None:
    """Return prompt text, or None if file does not exist."""
    p = prompt_path(year, month, audience)
    if p.exists():
        return p.read_text()
    return None


def save_newsletter(
    year: int,
    month: int,
    audience: str,
    subject: str,
    body: str,
    *,
    caller: str = None,
) -> Path:
    """Write a newsletter, keeping the previous version and announcing the change.

    Tiff's words are the thing being overwritten here, so the previous copy goes
    to versions/newsletters/<YYYY-MM>/<audience>/ first. Before this, a save
    replaced them with nothing kept and nothing said.

    Both side effects fail soft, matching how class plans do it: a versioning or
    Slack problem is logged, never allowed to lose the write itself.
    """
    path = newsletter_path(year, month, audience)
    path.parent.mkdir(parents=True, exist_ok=True)

    previous = _read_newsletter(path)
    try:
        backup_before_write(
            path,
            newsletter_versions_dir(),
            f"{year:04d}-{month:02d}",
            str(audience).replace("-", "_"),
        )
    except Exception as exc:
        logging.exception("newsletter backup failed for %s: %s", path, exc)

    content = f"# {subject}\n\n{body}"
    locked_write(path, content)

    firehose(
        NEWSLETTER,
        f"{year:04d}-{month:02d} {audience}",
        {"subject": subject, "body": body},
        previous,
        channel=os.getenv("SLACK_TWEEE_WRITES_CHANNEL", "#tweee-writes"),
        post_fn=_post_to_writes_channel,
        caller=caller,
    )
    return path


def _read_newsletter(path: Path):
    """The stored subject and body, or None when there is no file yet.

    None is what tells the firehose this is a first save rather than an edit, so
    it posts a summary instead of a diff against nothing.
    """
    if not Path(path).exists():
        return None
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError:
        return None
    lines = text.split("\n")
    subject = ""
    rest = text
    if lines and lines[0].startswith("# "):
        subject = lines[0][2:].strip()
        rest = "\n".join(lines[1:]).lstrip("\n")
    return {"subject": subject, "body": rest}


def _post_to_writes_channel(channel: str, text: str) -> None:
    """Post through the shared Slack helper. Raises so the firehose can log it."""
    from twy_platform.slack import slack

    slack(text, channel=channel)


def save_prompt(year: int, month: int, audience: str, text: str) -> Path:
    """Save prompt text to disk. Returns the path."""
    p = prompt_path(year, month, audience)
    locked_write(p, text)
    return p


def current_year_month() -> tuple[int, int]:
    """Current year and month in Mountain Time."""
    now = datetime.now(MOUNTAIN)
    return now.year, now.month
