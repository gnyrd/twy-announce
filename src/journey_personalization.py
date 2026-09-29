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

The welcome emails also name the next live classes (JP 2026-09-28).
{{next_class_1}}, {{next_class_2}} and so on take the class lines
next_live_classes supplies, and the copy around them sits between
{{#next_classes}} and {{/next_classes}}. That whole stretch is left out, markers
and all, whenever a class token inside it has no class behind it, so an email
never promises a class nobody has planned and reads exactly as it did before.
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

# The copy that names the next live classes. Everything between the two markers
# stays only when every {{next_class_N}} inside has a class behind it.
_CLASS_SECTION = re.compile(
    r"\{\{\s*#\s*next_classes\s*\}\}(.*?)\{\{\s*/\s*next_classes\s*\}\}",
    re.S | re.I,
)
_CLASS_TOKEN = re.compile(r"\{\{\s*next_class_(\d+)\s*\}\}", re.I)


class UnknownToken(ValueError):
    """A token nobody can fill. Raised rather than sent."""


def _class_values(classes) -> dict:
    """{{next_class_1}}, {{next_class_2}} and on, one per class line given."""
    return {
        f"next_class_{number}": str(line)
        for number, line in enumerate(classes or (), start=1)
    }


def _keep_or_drop_class_sections(text: str, class_values: dict) -> str:
    def keep_or_drop(match):
        inside = match.group(1)
        wanted = {f"next_class_{int(n)}" for n in _CLASS_TOKEN.findall(inside)}
        return inside if wanted <= set(class_values) else ""

    return _CLASS_SECTION.sub(keep_or_drop, text)


def first_name_or_fallback(first_name) -> str:
    """The name to greet somebody by. Their own, or the fallback.

    A single letter is somebody's actual first name in this data, four times
    over, so it passes through untouched. Only nothing at all falls back.
    """
    name = str(first_name or "").strip()
    return name or NO_NAME_FALLBACK


def _resolve(token: str, values: dict) -> str:
    key = token.lower()
    if key not in values and re.fullmatch(r"next_class_\d+", key):
        raise UnknownToken(
            f"{{{{{token}}}}} has no published class behind it right now. "
            "Keep class tokens between {{#next_classes}} and {{/next_classes}}, "
            "so those lines are left out when there is no class to name."
        )
    if key not in values:
        raise UnknownToken(
            f"{{{{{token}}}}} is not a token this system can fill. "
            f"Known tokens: {', '.join(sorted(values))}."
        )
    return values[key]


def personalize(text, *, first_name=None, classes=None) -> str:
    """Return the text with its tokens filled in.

    Case and inner spacing are forgiven, because Tiff types these by hand in the
    editor and {{ First_Name }} meaning something different from {{first_name}}
    would only ever be a trap.

    classes are the lines for {{next_class_1}} onward, soonest first. None or
    empty means there is no class to name, so every class section is left out.
    """
    class_values = _class_values(classes)
    values = {"first_name": first_name_or_fallback(first_name), **class_values}
    filled = _keep_or_drop_class_sections(str(text or ""), class_values)
    filled = _TOKEN.sub(lambda match: _resolve(match.group(1), values), filled)
    leftover = re.search(r"\{\{.*?\}\}", filled, re.S)
    if leftover:
        raise UnknownToken(
            f"{leftover.group(0)} survived substitution, so this would have "
            "reached a member as written."
        )
    return filled


def personalize_email(email: dict, *, first_name=None, classes=None) -> dict:
    """The same email with subject, preheader and body filled in.

    Every other key is carried through untouched, so a caller can hand this
    straight to the renderer without losing the interval or anything else.
    """
    filled = dict(email)
    for field in PERSONALIZED_FIELDS:
        if field in filled:
            filled[field] = personalize(
                filled[field], first_name=first_name, classes=classes
            )
    return filled


def tokens_in(text) -> set:
    """Every token name present, lowercased. For the editor and for reporting."""
    return {match.group(1).lower() for match in _TOKEN.finditer(str(text or ""))}
