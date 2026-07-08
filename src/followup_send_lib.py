"""Shared helpers for send_non_opener.py / send_gentle_nudge.py / send_reminder.py.

Each script wraps a thin `main()` that owns audience-specific date guard logic and
calls into these helpers for the MC mechanics. All MC writes funnel through
send_campaign(), which is the ONLY function in this codebase allowed to call
MC /actions/send. Audit it carefully.

Slack posting is opt-in via the `notify` flag — scripts pass notify=False under
--dry-run so test runs make zero Slack noise even on the warning paths.
"""
import os
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "paths"))
from twy_paths import load_env

load_env()

sys.path.insert(0, str(Path(__file__).parent))
from mailchimp_campaigns import monthly_campaign_title
from slack import post_slack

MAILCHIMP_API_KEY    = os.environ["MAILCHIMP_API_KEY"]
MC_SERVER            = os.environ["MAILCHIMP_SERVER_PREFIX"]
MAILCHIMP_AUDIENCE_ID = os.environ["MAILCHIMP_AUDIENCE_ID"]
SLACK_STATUS_CHANNEL = os.getenv("SLACK_STATUS_CHANNEL", "#status-newsletters")


def _mc_url(path: str) -> str:
    return f"https://{MC_SERVER}.api.mailchimp.com/3.0{path}"


def _mc_auth() -> tuple:
    return ("any", MAILCHIMP_API_KEY)


def _maybe_slack(text: str, notify: bool) -> None:
    """Post to status channel only if notify=True. Always echoes to stdout."""
    print(text)
    if not notify:
        return
    try:
        post_slack(SLACK_STATUS_CHANNEL, text)
    except Exception as e:
        print(f"slack post failed: {e}", file=sys.stderr)


def find_draft(year: int, month: int, audience_label: str) -> dict | None:
    """Find the auto-generated draft for {year}-{month} {audience_label} by exact title.

    Title format matches what submit_newsletter creates:
        '2026-06 — Non-Opener Resend — Yoga Habit'

    Returns the campaign dict (with id, web_id, status, recipients, settings) or
    None if no campaign with that title exists in any of: save / sending / sent /
    schedule. Read-only — never mutates.
    """
    title = monthly_campaign_title(year, month, audience_label)
    for status in ("save", "sending", "sent", "schedule"):
        offset = 0
        while True:
            r = requests.get(
                _mc_url("/campaigns"),
                auth=_mc_auth(),
                params={
                    "status": status,
                    "count": 100,
                    "offset": offset,
                    "fields": "campaigns.id,campaigns.web_id,campaigns.status,"
                              "campaigns.recipients,campaigns.settings.title,total_items",
                },
                timeout=30,
            )
            r.raise_for_status()
            cs = r.json().get("campaigns", []) or []
            for c in cs:
                if (c.get("settings", {}) or {}).get("title") == title:
                    return c
            if len(cs) < 100:
                break
            offset += 100
    return None


def get_segment_member_count(seg_id: int) -> int:
    """Read-only. Forces MC to re-evaluate dynamic (Aim) segments by fetching the
    segment endpoint directly. Returns current member_count."""
    r = requests.get(
        _mc_url(f"/lists/{MAILCHIMP_AUDIENCE_ID}/segments/{seg_id}"),
        auth=_mc_auth(),
        timeout=30,
    )
    r.raise_for_status()
    return int(r.json().get("member_count", 0))


def send_campaign(campaign_id: str) -> None:
    """*** THE ONLY FUNCTION THAT FIRES A REAL SEND. ***

    Calls MC POST /campaigns/{id}/actions/send. Returns None on success (204).
    Anything else raises. Caller is responsible for refusing to call this in
    --dry-run mode. There is no second guard inside this function — that's
    intentional: keep the dangerous primitive small and obvious.
    """
    r = requests.post(
        _mc_url(f"/campaigns/{campaign_id}/actions/send"),
        auth=_mc_auth(),
        timeout=60,
    )
    if r.status_code != 204:
        raise RuntimeError(f"MC send failed {r.status_code}: {r.text[:240]}")


def perform_send(
    audience_label: str,
    year: int,
    month: int,
    *,
    notify: bool,
    dry_run: bool,
) -> int:
    """Common send pipeline shared by the three scripts. Returns exit code.

    Steps (in order):
      1. find_draft  -> if None, warn (notify=notify), exit 1
      2. status save -> proceed; sent/sending -> noop; schedule -> noop (someone scheduled it manually)
      3. saved_segment_id present? -> if no, warn, exit 1
      4. segment member_count > 0 ? -> if no, warn ("NOT SENDING"), exit 1
      5. dry_run? -> print 'would send' and return 0 (NEVER calls send_campaign)
      6. real send -> send_campaign() -> notify success, return 0
    """
    draft = find_draft(year, month, audience_label)
    if draft is None:
        _maybe_slack(
            f":warning: send_followup({audience_label}, {year}-{month:02d}): no draft found",
            notify,
        )
        return 1

    if draft["status"] in ("sent", "sending"):
        print(f"[{audience_label}] draft {draft['id']} already {draft['status']} — nothing to do")
        return 0

    if draft["status"] == "schedule":
        print(f"[{audience_label}] draft {draft['id']} already scheduled — leaving alone")
        return 0

    seg_opts = (draft.get("recipients") or {}).get("segment_opts") or {}
    seg_id = seg_opts.get("saved_segment_id")
    if not seg_id:
        _maybe_slack(
            f":warning: send_followup({audience_label}): draft {draft['id']} has no saved_segment_id",
            notify,
        )
        return 1

    member_count = get_segment_member_count(seg_id)
    print(f"[{audience_label}] draft={draft['id']} segment={seg_id} members={member_count}")
    if member_count == 0:
        _maybe_slack(
            f":warning: send_followup({audience_label}): segment {seg_id} has 0 members — "
            f"NOT SENDING draft {draft['id']} ({draft.get('settings',{}).get('title')})",
            notify,
        )
        return 1

    if dry_run:
        print(
            f"[{audience_label}] DRY RUN — would send draft {draft['id']} to {member_count} members "
            f"(no MC call, no Slack post)"
        )
        return 0

    send_campaign(draft["id"])
    web_id = draft.get("web_id")
    archive_url = f"https://admin.mailchimp.com/campaigns/show?id={web_id}"
    _maybe_slack(
        f":outbox_tray: *{audience_label}* sent for {year}-{month:02d} — "
        f"{member_count} members. {archive_url}",
        notify,
    )
    return 0
