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

FIXTURE_DIR = Path(__file__).parent / "fixtures"
CORPUS_HASHES = {
    FIXTURE_DIR / "newsletter_lifestyle.md":
        "e6818fffd893a3bb51c1add69b25ea759ab47af60d321bd7f4eb6d72cca5cf05",
    FIXTURE_DIR / "newsletter_non_lifestyle.md":
        "3cc131d9da9ae97b85abc1ca7bef44e4f1d94a5eef11deff72ef17cf8c4868f9",
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


def test_stable_newsletter_corpus_preserves_provider_html():
    for filename, expected_hash in CORPUS_HASHES.items():
        body = Path(filename).read_text()
        actual = hashlib.sha256(render_newsletter(body).html.encode()).hexdigest()
        assert actual == expected_hash, filename
