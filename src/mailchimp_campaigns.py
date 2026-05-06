"""MailChimp campaign management -- create and manage draft campaigns.

Campaigns are created with a MailChimp template (MAILCHIMP_TEMPLATE_CAMPAIGN_ID)
so they render with TWY's branded wrapper (blue bg, Arial, Free Reg button).
Body content is injected into the template's `main_content` editable section.

Fallback: if the MC template API path ever breaks again, a local copy of the
rendered template HTML lives at /root/twy/data/newsletters/twy_newsletter_template.html
-- splice body into its mc:edit="main_content" section and PUT raw html instead.
"""
import os
import re
import markdown as md
import requests


# Email-safe button (table-based, Outlook-compatible). Wraps any paragraph that
# contains only a link to habit.* or studio.* (the two action-URL hosts) so the
# CTA reads as a button instead of a phrase. Idempotent: matches the simple
# <p><a></a></p> pattern produced by markdown lib, not styled <table> output.
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
    r'studio\.tiffanywoodyoga\.com)[^"]*)"[^>]*>(?P<text>[^<]+)</a>\s*</p>',
    re.IGNORECASE,
)


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
    html = md.markdown(body_md, extensions=["extra", "nl2br", "sane_lists"])
    html = html.replace("<p>", '<p style="margin-bottom:1em">')
    html = _CTA_LINK_RE.sub(
        lambda m: _CTA_BUTTON_HTML.format(href=m.group("href"), text=m.group("text").strip()),
        html,
    )
    return html


def find_draft(title_contains: str, statuses: list[str] | None = None) -> dict | None:
    if statuses is None:
        statuses = ["save", "paused", "schedule"]
    for status in statuses:
        resp = requests.get(
            _mc_url("/campaigns"),
            auth=_mc_auth(),
            params={"status": status, "count": 100},
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

    if existing:
        campaign_id = existing["id"]
        web_id      = existing.get("web_id", "")
        if existing.get("status") == "schedule":
            requests.post(_mc_url(f"/campaigns/{campaign_id}/actions/unschedule"),
                          auth=_mc_auth(), timeout=15)
        requests.patch(
            _mc_url(f"/campaigns/{campaign_id}"),
            auth=_mc_auth(),
            json={"recipients": recipients, "settings": settings},
            timeout=15,
        )
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

    resp2 = requests.put(
        _mc_url(f"/campaigns/{campaign_id}/content"),
        auth=_mc_auth(),
        json={"template": {"id": template_id, "sections": {"main_content": body_html}}},
        timeout=15,
    )
    resp2.raise_for_status()
    return {"id": campaign_id, "web_id": web_id, "action": action}
