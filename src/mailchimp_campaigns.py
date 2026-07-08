"""MailChimp utility -- create and manage draft campaigns.

Canonical MC helper module for all TWY codebases. Previously duplicated in
both /root/twy/announce/src/mailchimp_campaigns.py (lite) and
/root/twy/classes/dashboard/mailchimp.py (full). Now unified here.

Campaigns are created with a MailChimp template (MAILCHIMP_TEMPLATE_CAMPAIGN_ID)
so they render with TWY's branded wrapper (blue bg, Arial, Free Reg button).
Body content is injected into the template's `main_content` editable section.

Fallback: if the MC template API path ever breaks again, a local copy of the
rendered template HTML lives at /root/twy/data/newsletters/twy_newsletter_template.html
-- splice body into its mc:edit="main_content" section and PUT raw html instead.
"""
import os
import re
import time
import markdown as md
import requests


def _mc_post_with_retry(path: str, *, json: dict | None = None, timeout: int = 60, attempts: int = 4):
    """POST to MC with retry on 429 (rate limit). Returns the final Response."""
    backoff = 2.0
    last = None
    for i in range(attempts):
        try:
            r = requests.post(_mc_url(path), auth=_mc_auth(), json=json, timeout=timeout)
        except requests.exceptions.RequestException as e:
            if i == attempts - 1:
                raise
            time.sleep(backoff)
            backoff *= 2
            continue
        if r.status_code != 429:
            return r
        last = r
        time.sleep(backoff)
        backoff *= 2
    return last


# Email-safe button (table-based, Outlook-compatible). Wraps any paragraph that
# contains only a link to habit.* / studio.* / calendar.* (the three action-URL
# hosts) so the CTA reads as a button instead of a phrase. Idempotent: matches
# the simple <p><a></a></p> pattern produced by markdown lib, not styled
# <table> output.
_CTA_BUTTON_HTML = (
    '<table role="presentation" cellspacing="0" cellpadding="0" border="0" align="center"'
    ' style="margin:24px auto;border-collapse:collapse;">'
    '<tr><td bgcolor="#5d8399"'
    ' style="background-color:#5d8399;border-radius:6px;padding:14px 32px;text-align:center;">'
    '<a href="{href}" target="_blank"'
    ' style="color:#ffffff;text-decoration:none;font-family:Arial,Helvetica,sans-serif;'
    'font-size:17px;font-weight:600;letter-spacing:0.3px;display:inline-block;">'
    '<span style="color:#ffffff;">{text}</span>'
    '</a></td></tr></table>'
)

_CTA_LINK_RE = re.compile(
    r'<p[^>]*>\s*<a\s+[^>]*?href="(?P<href>[^"]*(?:habit\.tiffanywoodyoga\.com|'
    r'studio\.tiffanywoodyoga\.com|calendar\.tiffanywoodyoga\.com)[^"]*)"[^>]*>(?P<text>[^<]+)</a>\s*</p>',
    re.IGNORECASE,
)


# ----------------------------------------------------------------------------
# Campaign title builders (audit F08). The campaign title is the sole dedup
# key in MC (find_draft / find_campaign_by_title match on it), so callers must
# build titles through these two functions instead of hand-building f-strings.
# The separator is space + em-dash (U+2014) + space. That em-dash is DATA:
# it must stay byte-identical to the titles already in MailChimp, so never
# normalize it to a hyphen. Written as an escape to keep this source ASCII.
# ----------------------------------------------------------------------------

def monthly_campaign_title(year: int, month: int, label: str) -> str:
    """Title for the monthly-newsletter family: 'YYYY-MM (em-dash) <Label> (em-dash) Yoga Habit'."""
    return f"{year}-{month:02d} \u2014 {label} \u2014 Yoga Habit"


def followup_campaign_title(year: int, month: int, label: str) -> str:
    """Title for the post-class follow-up family: 'YYYY-MM (em-dash) Yoga Habit (em-dash) <Label>'."""
    return f"{year:04d}-{month:02d} \u2014 Yoga Habit \u2014 {label}"


def _mc_url(path: str) -> str:
    return f"https://{os.getenv('MAILCHIMP_SERVER_PREFIX')}.api.mailchimp.com/3.0{path}"


def _mc_auth() -> tuple[str, str]:
    return ("anystring", os.getenv("MAILCHIMP_API_KEY", ""))


def _template_id() -> int:
    tid = os.getenv("MAILCHIMP_TEMPLATE_CAMPAIGN_ID", "")
    if not tid:
        raise ValueError("MAILCHIMP_TEMPLATE_CAMPAIGN_ID must be set")
    return int(tid)


def _md_to_html(body_md: str) -> str:
    """Convert markdown to email-safe HTML with proper paragraph spacing."""
    html = md.markdown(body_md, extensions=["extra", "nl2br", "sane_lists"])
    html = html.replace("<p>", '<p style="margin-bottom:1em">')
    html = _CTA_LINK_RE.sub(
        lambda m: _CTA_BUTTON_HTML.format(href=m.group("href"), text=m.group("text").strip()),
        html,
    )
    return html


def find_draft(title_contains: str, statuses: list[str] | None = None) -> dict | None:
    """Find an existing campaign whose title contains the given string.

    Returns the first match, or None if not found.
    statuses: list of MC statuses to search (default: ['save', 'paused', 'schedule'])
    """
    if statuses is None:
        statuses = ["save", "paused", "schedule"]

    for status in statuses:
        resp = requests.get(
            _mc_url("/campaigns"),
            auth=_mc_auth(),
            params={"status": status, "count": 1000, "sort_field": "create_time", "sort_dir": "DESC"},
            timeout=15,
        )
        if not resp.ok:
            continue
        for campaign in resp.json().get("campaigns", []):
            title = campaign.get("settings", {}).get("title", "")
            if title_contains.lower() in title.lower():
                return campaign
    return None


def create_or_update_draft(
    subject: str,
    body_md: str,
    list_id: str,
    segment_id: int | None = None,
    campaign_title: str = "",
    from_name: str = "Tiff",
    reply_to: str = "tiffany@tiffanywoodyoga.com",
) -> dict:
    """Create or update a MailChimp draft campaign.

    If a campaign with campaign_title already exists (in save/paused/schedule status),
    update it (unscheduling first if needed). Otherwise create a new one with the
    TWY template applied.

    Returns dict with 'id', 'web_id', 'action' ('created' or 'updated'),
    'rescheduled' (bool), 'send_time' (str | None — the prior scheduled time if any).
    """
    api_key = os.getenv("MAILCHIMP_API_KEY", "")
    server  = os.getenv("MAILCHIMP_SERVER_PREFIX", "")
    if not api_key or not server:
        raise ValueError("MAILCHIMP_API_KEY and MAILCHIMP_SERVER_PREFIX must be set")

    template_id = _template_id()
    body_html   = _md_to_html(body_md)

    recipients = {"list_id": list_id}
    if segment_id:
        recipients["segment_opts"] = {"saved_segment_id": segment_id}

    settings = {
        "subject_line": subject,
        "title": campaign_title or subject,
        "from_name": from_name,
        "reply_to": reply_to,
        "template_id": template_id,
    }

    existing = find_draft(campaign_title) if campaign_title else None
    prev_send_time = None

    if existing:
        # Tiff's edits in MC are the source of truth. Never re-PATCH settings
        # or re-PUT body from the .md once a draft exists — unschedule only,
        # so the caller can reschedule (idempotent). To force a content refresh
        # from the .md, delete the campaign in MC first.
        campaign_id = existing["id"]
        web_id      = existing.get("web_id", "")
        if existing.get("status") == "schedule":
            prev_send_time = existing.get("send_time")
            requests.post(_mc_url(f"/campaigns/{campaign_id}/actions/unschedule"),
                          auth=_mc_auth(), timeout=15)
        action = "updated"
    else:
        resp = requests.post(
            _mc_url("/campaigns"),
            auth=_mc_auth(),
            json={
                "type": "regular",
                "recipients": recipients,
                "settings": settings,
            },
            timeout=15,
        )
        resp.raise_for_status()
        campaign    = resp.json()
        campaign_id = campaign["id"]
        web_id      = campaign.get("web_id", "")
        action      = "created"

        # Initial content push only on creation. Never overwrite existing
        # campaigns to preserve Tiff's edits.
        resp2 = requests.put(
            _mc_url(f"/campaigns/{campaign_id}/content"),
            auth=_mc_auth(),
            json={"template": {"id": template_id, "sections": {"main_content": body_html}}},
            timeout=15,
        )
        resp2.raise_for_status()

    rescheduled = False
    if prev_send_time:
        r = requests.post(
            _mc_url(f"/campaigns/{campaign_id}/actions/schedule"),
            auth=_mc_auth(),
            json={"schedule_time": prev_send_time},
            timeout=15,
        )
        r.raise_for_status()
        rescheduled = True

    return {"id": campaign_id, "web_id": web_id, "action": action, "rescheduled": rescheduled, "send_time": prev_send_time}


# ----------------------------------------------------------------------------
# Saved-segment + scheduling helpers (used by submit_newsletter for the new
# audiences: non_opener, gentle_nudge, reminder).
# ----------------------------------------------------------------------------

def _list_segments_paginated(list_id: str):
    """Yield every segment in the list, paginated.

    Pulls only id/name/type fields — full segment objects (including conditions and
    member_count) make MC's response slow enough to time out on large lists. Callers
    that need full details should fetch individual segments by id.
    """
    page = 100
    offset = 0
    while True:
        r = requests.get(
            _mc_url(f"/lists/{list_id}/segments"),
            auth=_mc_auth(),
            params={
                "count": page,
                "offset": offset,
                "fields": "segments.id,segments.name,segments.type,total_items",
            },
            timeout=60,
        )
        r.raise_for_status()
        segs = r.json().get("segments", []) or []
        for s in segs:
            yield s
        if len(segs) < page:
            return
        offset += page


def find_segment_by_name(list_id: str, name: str) -> dict | None:
    """Return the first segment in list whose name matches exactly, else None."""
    for s in _list_segments_paginated(list_id):
        if s.get("name") == name:
            return s
    return None


def find_or_create_saved_segment(
    list_id: str,
    name: str,
    conditions: list,
    match: str = "all",
) -> int:
    """Idempotent. Return segment id for a saved (dynamic) segment with the given conditions.

    If a segment with this name already exists, return its id without modifying it.
    Caller is responsible for naming segments uniquely per definition (e.g. encode the
    referenced campaign id in the name) so renaming forces a fresh segment.
    """
    existing = find_segment_by_name(list_id, name)
    if existing:
        return existing["id"]
    payload = {"name": name, "options": {"match": match, "conditions": conditions}}
    r = requests.post(
        _mc_url(f"/lists/{list_id}/segments"),
        auth=_mc_auth(),
        json=payload,
        timeout=15,
    )
    r.raise_for_status()
    return r.json()["id"]


def find_or_create_empty_tag(list_id: str, name: str) -> int:
    """Idempotent. Return segment id for a static segment / tag, creating it empty if absent.

    Used to pre-create the 'Habit Registered - YYYY-MM' tag so the reminder segment can
    reference it before the daily sync_habit_registrations cron has populated it.
    """
    existing = find_segment_by_name(list_id, name)
    if existing:
        return existing["id"]
    payload = {"name": name, "static_segment": []}
    r = requests.post(
        _mc_url(f"/lists/{list_id}/segments"),
        auth=_mc_auth(),
        json=payload,
        timeout=15,
    )
    r.raise_for_status()
    return r.json()["id"]


def find_campaign_by_title(
    title: str,
    statuses: tuple = ("sent", "schedule", "sending", "save", "paused"),
) -> dict | None:
    """Find a campaign by EXACT title match across the given statuses.

    Returns the campaign dict (with id and web_id) or None. Searches statuses in order;
    first match wins.
    """
    for status in statuses:
        offset = 0
        while True:
            r = requests.get(
                _mc_url("/campaigns"),
                auth=_mc_auth(),
                params={"status": status, "count": 100, "offset": offset},
                timeout=15,
            )
            if not r.ok:
                break
            j = r.json()
            campaigns = j.get("campaigns", []) or []
            for c in campaigns:
                if c.get("settings", {}).get("title") == title:
                    return c
            if len(campaigns) < 100:
                break
            offset += 100
    return None


def schedule_campaign(campaign_id: str, schedule_time: str) -> None:
    """Schedule (or reschedule) a campaign for the given UTC ISO time. Idempotent.

    schedule_time format: 'YYYY-MM-DDTHH:MM:SS+00:00'. MailChimp requires minutes to be
    one of :00, :15, :30, :45. Retries on 429 (account-level rate limit) so a burst of
    schedule actions in one submit_newsletter call doesn't fail the later ones.
    """
    body = {"schedule_time": schedule_time}
    r = _mc_post_with_retry(f"/campaigns/{campaign_id}/actions/schedule", json=body)
    if r.status_code == 400 and "already scheduled" in r.text:
        u = _mc_post_with_retry(f"/campaigns/{campaign_id}/actions/unschedule")
        if u.status_code != 204:
            raise RuntimeError(f"unschedule failed {u.status_code} {u.text[:160]}")
        r = _mc_post_with_retry(f"/campaigns/{campaign_id}/actions/schedule", json=body)
    if r.status_code != 204:
        raise RuntimeError(f"schedule failed {r.status_code} {r.text[:160]}")
