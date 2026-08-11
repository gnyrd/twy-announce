#!/usr/bin/env python3
"""Fill a journey email's merge tokens in, or refuse to send it.

The eight welcome emails carry {{first_name}} in their greetings. MailChimp
resolved that from its own FNAME field; nothing on this side did, so the token
would have reached a member's inbox verbatim. This module is what stands between
those two facts.

It fails closed on a token it does not know. A greeting that reads "Hello there,"
because somebody has no first name is a small cost. A greeting that reads
"Hello {{frist_name}}," is the kind of thing a member forwards to a friend, so a
typo stops the send rather than shipping.

Substitution covers the subject and preheader as well as the body, which is why
this is separate from render_newsletter: that one only ever sees a body.
"""
from __future__ import annotations

import re

# Every use in the imported welcome sequence is a greeting: "Hello {{first_name}},"
# or "Hi {{first_name}},". Both read correctly with this in place, and it stays
# harmless if the token ever moves mid-sentence.
NO_NAME_FALLBACK = "there"

# What the editor's Preview and Send test put where a member's name would go.
# Raw {{first_name}} reads as a broken email and a real name reads as copy, so
# this is deliberately neither: it says a name gets filled in here. Named by JP
# 2026-08-11.
EDITOR_PLACEHOLDER = "auto_substituted_name"

PERSONALIZED_FIELDS = ("subject", "preheader", "body")

_TOKEN = re.compile(r"\{\{\s*([A-Za-z0-9_]+)\s*\}\}")


class UnknownToken(ValueError):
    """A token nobody can fill. Raised rather than sent."""


def first_name_or_fallback(first_name) -> str:
    """The name to greet somebody by. Their own, or the fallback.

    A single letter is somebody's actual first name in this data, four times
    over, so it passes through untouched. Only nothing at all falls back.
    """
    name = str(first_name or "").strip()
    return name or NO_NAME_FALLBACK


def _resolve(token: str, values: dict) -> str:
    key = token.lower()
    if key not in values:
        raise UnknownToken(
            f"{{{{{token}}}}} is not a token this system can fill. "
            f"Known tokens: {', '.join(sorted(values))}."
        )
    return values[key]


def personalize(text, *, first_name=None) -> str:
    """Return the text with its tokens filled in.

    Case and inner spacing are forgiven, because Tiff types these by hand in the
    editor and {{ First_Name }} meaning something different from {{first_name}}
    would only ever be a trap.
    """
    values = {"first_name": first_name_or_fallback(first_name)}
    filled = _TOKEN.sub(lambda match: _resolve(match.group(1), values), str(text or ""))
    leftover = re.search(r"\{\{.*?\}\}", filled, re.S)
    if leftover:
        raise UnknownToken(
            f"{leftover.group(0)} survived substitution, so this would have "
            "reached a member as written."
        )
    return filled


def personalize_email(email: dict, *, first_name=None) -> dict:
    """The same email with subject, preheader and body filled in.

    Every other key is carried through untouched, so a caller can hand this
    straight to the renderer without losing the interval or anything else.
    """
    filled = dict(email)
    for field in PERSONALIZED_FIELDS:
        if field in filled:
            filled[field] = personalize(filled[field], first_name=first_name)
    return filled


def tokens_in(text) -> set:
    """Every token name present, lowercased. For the editor and for reporting."""
    return {match.group(1).lower() for match in _TOKEN.finditer(str(text or ""))}
