"""
MailChimp campaign management -- create and manage draft campaigns.
"""
import os
import re
import markdown as md
import requests


def _mc_url(path: str) -> str:
    return f"https://{os.getenv('MAILCHIMP_SERVER_PREFIX')}.api.mailchimp.com/3.0{path}"


def _mc_auth() -> tuple[str, str]:
    return ("anystring", os.getenv("MAILCHIMP_API_KEY", ""))


def _fetch_template_html() -> str | None:
    template_id = os.getenv("MAILCHIMP_TEMPLATE_CAMPAIGN_ID", "")
    if not template_id:
        return None
    resp = requests.get(
        _mc_url(f"/campaigns/{template_id}/content"),
        auth=_mc_auth(),
        timeout=15,
    )
    if resp.ok:
        return resp.json().get("html")
    return None


def _inject_content(template_html: str, body_html: str) -> str:
    pattern = r'(id="d47"[^>]*>)(.*?)(</div>)'
    match = re.search(pattern, template_html, re.DOTALL)
    if match:
        return template_html[:match.start(1)] + match.group(1) + body_html + match.group(3) + template_html[match.end():]
    return body_html


def _md_to_html(body_md: str) -> str:
    html = md.markdown(body_md)
    html = html.replace("<p>", '<p style="margin-bottom:1em">')
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

    body_html     = _md_to_html(body_md)
    template_html = _fetch_template_html()
    final_html    = _inject_content(template_html, body_html) if template_html else body_html

    recipients = {"list_id": list_id}
    if segment_id:
        recipients["segment_opts"] = {"saved_segment_id": segment_id}

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
            json={
                "recipients": recipients,
                "settings": {
                    "subject_line": subject,
                    "title": campaign_title or subject,
                    "from_name": from_name,
                    "reply_to": reply_to,
                },
            },
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
                "settings": {
                    "subject_line": subject,
                    "title": campaign_title or subject,
                    "from_name": from_name,
                    "reply_to": reply_to,
                },
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
        json={"html": final_html},
        timeout=15,
    )
    resp2.raise_for_status()
    return {"id": campaign_id, "web_id": web_id, "action": action}
