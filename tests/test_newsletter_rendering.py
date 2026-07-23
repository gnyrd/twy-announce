import hashlib
from pathlib import Path

from newsletter_rendering import render_newsletter


BODY = """A small practice.

[See the class](https://habit.tiffanywoodyoga.com/?utm_content=proof)
"""

EXPECTED_SAMPLE_HTML = (
    '<p style="margin-bottom:1em">A small practice.</p>\n'
    '<table role="presentation" cellspacing="0" cellpadding="0" border="0" align="center"'
    ' style="margin:24px auto;border-collapse:collapse;"><tr><td bgcolor="#5d8399"'
    ' style="background-color:#5d8399;border-radius:6px;padding:14px 32px;'
    'text-align:center;"><a href="https://habit.tiffanywoodyoga.com/?utm_content=proof"'
    ' target="_blank" style="color:#ffffff;text-decoration:none;'
    'font-family:Arial,Helvetica,sans-serif;font-size:17px;font-weight:600;'
    'letter-spacing:0.3px;display:inline-block;"><span style="color:#ffffff;">'
    "See the class</span></a></td></tr></table>"
)

CORPUS_HASHES = {
    "/root/twy/data/newsletters/2026-06/lifestyle.md":
        "1e0a0a0f4b2847731a04331c9e69c0cc6c819b1bef63ad3bc341e859cf7fe79f",
    "/root/twy/data/newsletters/2026-06/non_lifestyle.md":
        "cab4534740c0184c12eeaa7bd8b4ef3f154e77cbbc0b33f64fb5bf2e40e15d6e",
    "/root/twy/data/newsletters/2026-07/lifestyle.md":
        "1639e51141eabc997a4a4cb0d08bfe3b0231ce20c3f838565a27f43c2f0ea777",
    "/root/twy/data/newsletters/2026-07/non_lifestyle.md":
        "e3466064ac68b11c7ce370d7e3f2365554cd7dd732bff75314d617515397aba2",
}


def test_render_newsletter_builds_html_and_text():
    rendered = render_newsletter(BODY)
    assert rendered.html == EXPECTED_SAMPLE_HTML
    assert "A small practice." in rendered.plain_text
    assert "https://habit.tiffanywoodyoga.com/?utm_content=proof" in rendered.plain_text


def test_render_newsletter_text_contains_no_html():
    assert "<" not in render_newsletter(BODY).plain_text


def test_render_newsletter_plain_text_removes_markdown_syntax():
    rendered = render_newsletter(
        "# Heading\n\n**Bold** and [a class](https://example.com/class)\n"
    )
    assert rendered.plain_text == (
        "Heading\n"
        "Bold and a class (https://example.com/class)\n"
    )


def test_real_newsletter_corpus_preserves_mailchimp_html():
    for filename, expected_hash in CORPUS_HASHES.items():
        body = Path(filename).read_text()
        actual = hashlib.sha256(render_newsletter(body).html.encode()).hexdigest()
        assert actual == expected_hash, filename
