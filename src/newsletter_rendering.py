"""Provider-neutral rendering for TWY newsletters."""

from dataclasses import dataclass
import re

from bs4 import BeautifulSoup
import markdown as md


@dataclass(frozen=True)
class RenderedNewsletter:
    html: str
    plain_text: str


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
    r'studio\.tiffanywoodyoga\.com|calendar\.tiffanywoodyoga\.com)[^"]*)"[^>]*>'
    r'(?P<text>[^<]+)</a>\s*</p>',
    re.IGNORECASE,
)


def _render_html(body_md: str) -> str:
    html = md.markdown(body_md, extensions=["extra", "nl2br", "sane_lists"])
    html = html.replace("<p>", '<p style="margin-bottom:1em">')
    return _CTA_LINK_RE.sub(
        lambda match: _CTA_BUTTON_HTML.format(
            href=match.group("href"),
            text=match.group("text").strip(),
        ),
        html,
    )


def _render_plain_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for link in soup.find_all("a"):
        label = link.get_text(" ", strip=True)
        href = link.get("href", "").strip()
        replacement = f"{label} ({href})" if href else label
        link.replace_with(replacement)
    for line_break in soup.find_all("br"):
        line_break.replace_with("\n")
    lines = []
    for element in soup.contents:
        text = (
            element.get_text(" ", strip=True)
            if hasattr(element, "get_text")
            else str(element).strip()
        )
        lines.append(re.sub(r"[ \t]+", " ", text).strip())
    return "\n".join(line for line in lines if line) + "\n"


def render_newsletter(body_md: str) -> RenderedNewsletter:
    html = _render_html(body_md)
    plain_text = _render_plain_text(html)
    return RenderedNewsletter(html=html, plain_text=plain_text)
