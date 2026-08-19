import pytest

import newsletter_autogen as autogen
from newsletter_autogen import AutogenError


SAMPLE = """subject: October Yoga Lifestyle: Heart's Mirror

preheader: What the mat reflects back when you slow down.

body: Hi sweethearts,

This month is about honesty.

{CLASS_TITLE}

{REGISTER_CTA}

With love,
Tiff"""


def test_parse_pulls_subject_preheader_and_body():
    subject, preheader, body = autogen._parse_generated(SAMPLE)
    assert subject == "October Yoga Lifestyle: Heart's Mirror"
    assert preheader == "What the mat reflects back when you slow down."
    assert body.startswith("Hi sweethearts,")
    assert "{REGISTER_CTA}" in body
    assert "With love,\nTiff" in body
    # the labels themselves are not part of the body
    assert "preheader:" not in body


def test_parse_is_case_insensitive_on_labels():
    subject, preheader, body = autogen._parse_generated(
        "Subject: A\n\nPreheader: B\n\nBody: C body here"
    )
    assert (subject, preheader) == ("A", "B")
    assert body == "C body here"


def test_parse_tolerates_markdown_bold_labels():
    subject, preheader, body = autogen._parse_generated(
        "**subject:** October Heart's Mirror\n\n"
        "**preheader:** A preview line.\n\n"
        "**body:**\n\nHi sweethearts,\n\nthe real body."
    )
    assert subject == "October Heart's Mirror"
    assert preheader == "A preview line."
    assert body.startswith("Hi sweethearts,")
    assert body.endswith("the real body.")


def test_generate_from_prompt_returns_a_validated_draft():
    draft = autogen.generate_from_prompt(
        "lifestyle", "PROMPT", generate_fn=lambda _prompt: SAMPLE
    )
    assert draft["audience"] == "lifestyle"
    assert draft["subject"] == "October Yoga Lifestyle: Heart's Mirror"
    assert draft["preheader"].startswith("What the mat")
    assert draft["body"].startswith("Hi sweethearts,")


def test_generate_rejects_a_forbidden_dash():
    bad = "subject: A\n\npreheader: B\n\nbody: text with an em dash — here"
    with pytest.raises(AutogenError, match="prohibited punctuation"):
        autogen.generate_from_prompt("lifestyle", "PROMPT", generate_fn=lambda _p: bad)


def test_generate_rejects_empty_output():
    with pytest.raises(AutogenError, match="no subject or body"):
        autogen.generate_from_prompt(
            "lifestyle", "PROMPT", generate_fn=lambda _p: "no labels, just prose"
        )


def test_assemble_prompt_rejects_an_unknown_audience():
    with pytest.raises(AutogenError, match="no autogen assembler"):
        autogen.assemble_prompt("bogus", {}, {}, 2026, 10)


def test_assemble_prompt_normalizes_the_audience_hyphen():
    # non-lifestyle and non_lifestyle resolve to the same assembler
    assert "non_lifestyle" in autogen._ASSEMBLERS
    calls = {}
    autogen._ASSEMBLERS["non_lifestyle"] = lambda o, p, y, m: calls.update(hit=(y, m)) or "PROMPT"
    try:
        assert autogen.assemble_prompt("non-lifestyle", {}, {}, 2026, 10) == "PROMPT"
        assert calls["hit"] == (2026, 10)
    finally:
        import habit_newsletter_prompt as hnp
        autogen._ASSEMBLERS["non_lifestyle"] = hnp.assemble_non_lifestyle_prompt
