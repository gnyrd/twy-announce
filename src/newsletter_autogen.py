"""Auto-generate the initial newsletter copy from the Monthly Overview, so the
Tweee step is not a manual one.

This replaces the human running Tweee by hand: it feeds the same assembled
prompt (which already draws on the Overview and the class plans) to an automated
model call, parses the subject, preheader and body out of the result, and hands
them back for the caller to save as an ordinary DRAFT. Nothing here approves or
sends anything. The section still passes through the existing draft, approval and
scheduling machinery, and a human still approves the complete set before it goes.

The model call is injected as generate_fn so the parse and the validation are
testable without a network, and production wires anthropic_generate_fn in.
"""
from __future__ import annotations

import re

import habit_newsletter_prompt as hnp
from twy_platform.text import find_prohibited


class AutogenError(ValueError):
    """The automated draft could not be produced, so nothing was saved."""


# Overview-driven monthly newsletters. The class-anchored sections (reminder,
# gentle_nudge, non_opener, ph1, ph2) need the month's class plans and are added
# once those are authored.
_ASSEMBLERS = {
    "lifestyle": hnp.assemble_lifestyle_prompt,
    "non_lifestyle": hnp.assemble_non_lifestyle_prompt,
}

# Tolerant of a bare label ("subject:") or a markdown-bold one ("**subject:**"),
# because the model uses either from run to run.
_LABEL = re.compile(
    r"^\s*\*{0,2}\s*(subject|preheader|body)\s*:\s*(.*)$", re.IGNORECASE
)

_FORMAT_SYSTEM = (
    "Output exactly three fields and nothing else, each on its own line, "
    "labelled in plain lowercase with no markdown: 'subject:', then 'preheader:', "
    "then 'body:' followed by the full newsletter body. Do not bold the labels."
)


def _parse_generated(text: str) -> tuple[str, str, str]:
    """Pull subject, preheader and body out of a labelled model response.

    The prompt asks the model for exactly those three fields. Subject and
    preheader are single lines; everything after the body label is the body.
    """
    subject = preheader = ""
    body_lines: list[str] = []
    in_body = False
    for line in text.splitlines():
        match = _LABEL.match(line)
        key = match.group(1).lower() if match else None
        if key == "subject":
            subject = match.group(2).strip().strip("*").strip()
            in_body = False
        elif key == "preheader":
            preheader = match.group(2).strip().strip("*").strip()
            in_body = False
        elif key == "body":
            first = match.group(2).strip().strip("*").strip()
            body_lines = [first] if first else []
            in_body = True
        elif in_body:
            body_lines.append(line)
    return subject, preheader, "\n".join(body_lines).strip()


def assemble_prompt(audience: str, overview: dict, plans: dict, year: int, month: int) -> str:
    """The Tweee prompt for one audience, assembled from the Overview and plans."""
    key = audience.replace("-", "_")
    if key not in _ASSEMBLERS:
        raise AutogenError(f"no autogen assembler for audience {audience!r}")
    return _ASSEMBLERS[key](overview, plans, year, month)


def generate_from_prompt(audience: str, prompt: str, generate_fn) -> dict:
    """Run one assembled prompt through generate_fn and return a validated draft.

    Raises rather than returning junk: a draft with no subject or body, or one
    carrying a forbidden dash, must not reach the save path.
    """
    text = generate_fn(prompt)
    subject, preheader, body = _parse_generated(text)
    if not subject or not body:
        raise AutogenError(f"{audience}: generation produced no subject or body")
    offenders = find_prohibited("\n".join([subject, preheader, body]))
    if offenders:
        raise AutogenError(
            f"{audience}: generated copy contains prohibited punctuation {offenders}"
        )
    return {
        "audience": audience,
        "subject": subject,
        "preheader": preheader,
        "body": body,
    }


def anthropic_generate_fn(client, model: str = "claude-opus-4-8", max_tokens: int = 4000):
    """A generate_fn that calls the Anthropic Messages API through client."""
    def _fn(prompt: str) -> str:
        response = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=_FORMAT_SYSTEM,
            messages=[{"role": "user", "content": prompt}],
        )
        return "".join(
            block.text for block in response.content
            if getattr(block, "type", None) == "text"
        )
    return _fn
