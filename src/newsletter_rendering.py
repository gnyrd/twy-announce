"""Provider-neutral rendering for TWY newsletters."""

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import re

from urllib.parse import urlparse

from bs4 import BeautifulSoup
import markdown as md
from twy_paths import newsletters_dir


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


def _template_path() -> Path:
    return newsletters_dir() / "twy_newsletter_template.html"


def _load_template_html() -> str | None:
    path = _template_path()
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8")


def _find_main_content(soup: BeautifulSoup):
    return soup.find(attrs={"mc:edit": "main_content"})


def _adapt_newsletter_template_for_sendgrid(template: str) -> str:
    html = re.sub(
        r"<!--\*\|IF:MC_PREVIEW_TEXT\|\*-->.*?<!--\*\|END:IF\|\*-->",
        "",
        template,
        flags=re.DOTALL,
    )
    html = re.sub(
        r"\*\|IFNOT:ARCHIVE_PAGE\|\*.*?\*\|END:IF\|\*",
        "",
        html,
        flags=re.DOTALL,
    )
    replacements = {
        "*|MC:SUBJECT|*": "Tiffany Wood Yoga",
        "*|MC_PREVIEW_TEXT|*": "",
        "*|UPDATE_PROFILE|*": "<%asm_preferences_raw_url%>",
        "*|UNSUB|*": "<%asm_group_unsubscribe_raw_url%>",
        "*|CURRENT_YEAR|*": str(datetime.now().year),
        "*|LIST:COMPANY|*": "Tiffany Wood Yoga",
        "*|LIST:DESCRIPTION|*": "",
        "*|HTML:LIST_ADDRESS_HTML|*": "",
        "*|FORWARD|*": "",
        "*|EMAIL|*": "",
    }
    for source, target in replacements.items():
        html = html.replace(source, target)
    return re.sub(r"\*\|[^|]+\|\*", "", html)


def _wrap_with_newsletter_template(body_html: str) -> str:
    template = _load_template_html()
    if not template:
        return body_html
    template = _adapt_newsletter_template_for_sendgrid(template)
    soup = BeautifulSoup(template, "html.parser")
    main_content = _find_main_content(soup)
    if main_content is None:
        return body_html
    body_soup = BeautifulSoup(body_html, "html.parser")
    main_content.clear()
    del main_content["mc:edit"]
    for child in list(body_soup.contents):
        main_content.append(child)
    return (
        str(soup)
        .replace(
            "&lt;%asm_preferences_raw_url%&gt;",
            "<%asm_preferences_raw_url%>",
        )
        .replace(
            "&lt;%asm_group_unsubscribe_raw_url%&gt;",
            "<%asm_group_unsubscribe_raw_url%>",
        )
    )


def _with_hidden_preheader(html: str, preheader: str) -> str:
    clean_preheader = preheader.strip()
    if not clean_preheader:
        return html
    soup = BeautifulSoup(html, "html.parser")
    hidden = soup.new_tag("div")
    hidden["style"] = (
        "display:none;max-height:0;overflow:hidden;opacity:0;"
        "color:transparent;mso-hide:all;"
    )
    hidden.string = clean_preheader
    if soup.body:
        soup.body.insert(0, hidden)
        return str(soup)
    return f"{hidden}{html}"


_TWY_ASSET_SUFFIX = ".tiffanywoodyoga.com"

# The template's content column. A body image is served at twice its display
# width for retina, so without a cap a 1128px screenshot renders at 1128px and
# tears the layout open.
_CONTENT_WIDTH = 600


def _make_images_responsive(html: str) -> str:
    """Fit every body image to the content column, on phones and in Outlook.

    Markdown gives a bare img with no dimensions. Modern clients honour the
    max-width, Outlook ignores CSS entirely and honours the width attribute, so
    both are set. height auto keeps the aspect ratio when the width shrinks.
    """
    # Reparsing rewrites the CTA button table even when nothing changes, and
    # the corpus hashes hold that markup byte for byte. A body with no image
    # goes back untouched.
    if "<img" not in html:
        return html
    soup = BeautifulSoup(html, "html.parser")
    for img in soup.find_all("img"):
        if img.get("data-twy-chrome") is not None:
            continue
        existing = str(img.get("style") or "").rstrip("; ")
        img["style"] = "; ".join(filter(None, [
            existing,
            "display:block",
            "width:100%",
            f"max-width:{_CONTENT_WIDTH}px",
            "height:auto",
        ]))
        img["width"] = str(_CONTENT_WIDTH)
        img.attrs.pop("height", None)
    return str(soup)


def _assert_asset_hosts(html: str) -> None:
    # Every email asset must live on a TWY host. The MailChimp migration
    # left images hotlinked from mcusercontent.com until 2026-08-09; this
    # fails the render loudly so an external asset can never ship again.
    soup = BeautifulSoup(html, "html.parser")
    for img in soup.find_all("img"):
        src = str(img.get("src") or "").strip()
        host = urlparse(src).netloc.lower()
        if host != _TWY_ASSET_SUFFIX[1:] and not host.endswith(_TWY_ASSET_SUFFIX):
            raise ValueError(f"email image on non-TWY host: {src[:120]}")


def render_newsletter(
    body_md: str,
    *,
    use_template: bool = False,
    preheader: str = "",
) -> RenderedNewsletter:
    body_html = _make_images_responsive(_render_html(body_md))
    html = (
        _wrap_with_newsletter_template(body_html)
        if use_template
        else body_html
    )
    html = _with_hidden_preheader(html, preheader)
    _assert_asset_hosts(html)
    plain_text = _render_plain_text(body_html)
    return RenderedNewsletter(html=html, plain_text=plain_text)
