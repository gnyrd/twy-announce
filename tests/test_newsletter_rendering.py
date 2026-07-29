import hashlib
import inspect
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


def _write_newsletter_template(tmp_path):
    root = tmp_path / "newsletters"
    root.mkdir(parents=True, exist_ok=True)
    (root / "twy_newsletter_template.html").write_text(
        '<html><head><title>*|MC:SUBJECT|*</title></head>'
        '<body style="background-color:#5d8399;">'
        '<div class="before">Header</div>'
        '<div mc:edit="main_content"><p>Newsletter content goes here.</p></div>'
        '<div class="after">'
        '<a href="*|UPDATE_PROFILE|*">Update your preferences</a>'
        '<a href="*|UNSUB|*">Unsubscribe</a>'
        '*|CURRENT_YEAR|* *|LIST:COMPANY|*'
        '</div>'
        '</body></html>'
    )

CORPUS_HASHES = {
    FIXTURE_DIR / "newsletter_lifestyle.md":
        "e6818fffd893a3bb51c1add69b25ea759ab47af60d321bd7f4eb6d72cca5cf05",
    FIXTURE_DIR / "newsletter_non_lifestyle.md":
        "3cc131d9da9ae97b85abc1ca7bef44e4f1d94a5eef11deff72ef17cf8c4868f9",
}



def test_render_newsletter_wraps_body_in_twy_template(monkeypatch, tmp_path):
    monkeypatch.setenv("TWY_DATA_DIR", str(tmp_path))
    _write_newsletter_template(tmp_path)

    rendered = render_newsletter(
        "Opening paragraph.\n\n[Register](https://habit.tiffanywoodyoga.com/)",
        use_template=True,
    )

    assert rendered.html.startswith("<html>")
    assert "background-color:#5d8399" in rendered.html
    assert "Opening paragraph." in rendered.html
    assert "Register" in rendered.html
    assert "Newsletter content goes here" not in rendered.html
    assert "*|" not in rendered.html
    assert "mc:" not in rendered.html
    assert "<%asm_preferences_raw_url%>" in rendered.html
    assert "<%asm_group_unsubscribe_raw_url%>" in rendered.html
    assert rendered.plain_text == (
        "Opening paragraph.\n"
        "Register (https://habit.tiffanywoodyoga.com/)\n"
    )


def test_render_newsletter_includes_hidden_preheader_only_in_html(monkeypatch, tmp_path):
    monkeypatch.setenv("TWY_DATA_DIR", str(tmp_path))
    _write_newsletter_template(tmp_path)
    assert "preheader" in inspect.signature(render_newsletter).parameters

    rendered = render_newsletter(
        "Opening paragraph.",
        use_template=True,
        preheader="A clear August note",
    )

    assert "A clear August note" in rendered.html
    assert "display:none" in rendered.html
    assert "A clear August note" not in rendered.plain_text
    assert rendered.plain_text == "Opening paragraph.\n"


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
