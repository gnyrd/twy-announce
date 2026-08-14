"""Read the Resend event log for facts the SendGrid ledger cannot know.

Journey email moved to Resend on 2026-08-14, and the consent ledger did not
move with it. SendGrid still answers for unsubscribes, blocks and invalids,
because that history exists nowhere else, but a bounce or a spam complaint on
a journey drip now happens at Resend and SendGrid never hears about it. Until
this module existed, a member whose address hard bounced on email 1 was sent
email 2 anyway.

Only two outcomes here are permanent enough to stop a sequence:

- A bounce Resend marks `Permanent`. A `Transient` bounce is a full mailbox or
  a greylisting server and says nothing about the address, so retrying is the
  correct behaviour and suppressing would be wrong.
- A complaint. Somebody pressed the spam button, which is a decision about
  wanting the mail, not a fact about the mailbox, and it is never retried.

`email.failed` is deliberately NOT suppressed: it means the send did not
leave, so nothing reached anybody and the address is unproven either way.
"""

from __future__ import annotations

import json


BOUNCED = "bounced"
COMPLAINED = "complained"

PERMANENT = "permanent"


def load_suppressions(path) -> dict:
    """{address: reason} for everyone Resend says must not be mailed again.

    An absent or unreadable log answers empty rather than raising. That is the
    same posture the reporting side takes, and it is deliberate: this is one
    input to a send decision, and a missing file must not stop the sequence
    for everybody. It fails OPEN, so pair it with the SendGrid checks rather
    than treating it as the whole gate.

    A malformed line is skipped rather than abandoning the rest of the file,
    because one bad write should not un-suppress every address after it.
    """
    suppressed: dict[str, str] = {}
    try:
        if not path.exists():
            return suppressed
        handle = path.open(encoding="utf-8")
    except OSError:
        return suppressed

    try:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except ValueError:
                continue
            kind = str(record.get("type") or "")
            data = (record.get("event") or {}).get("data") or {}
            if kind == "email.complained":
                reason = COMPLAINED
            elif kind == "email.bounced":
                bounce = data.get("bounce") or {}
                if str(bounce.get("type") or "").strip().lower() != PERMANENT:
                    continue
                reason = BOUNCED
            else:
                continue
            for address in data.get("to") or []:
                cleaned = str(address).strip().lower()
                if not cleaned:
                    continue
                # A complaint outranks a bounce: it is a decision the person
                # made, and it is the truthful answer when both are present.
                if suppressed.get(cleaned) == COMPLAINED:
                    continue
                suppressed[cleaned] = reason
    finally:
        handle.close()
    return suppressed
