#!/usr/bin/env python3
"""The `2026_09: Subscription: Confirmation` mailing (naming guide, 2026_09_21).

One email to the twelve addresses a bot pushed through the website
newsletter form from Tor exits on 2026-09-20 and 21, asking each whether
the signup was theirs. The link lands on the yoga-habit app's confirm route
(habit.tiffanywoodyoga.com/subscribe/confirm), signed for this batch.

Without --execute nothing is touched: the plan prints, with the rendered
link. With --execute it creates, in this order, each step idempotent:

  1. the contact field `twy_confirmed_at` (Text), if missing
  2. the list `Internal: Unconfirmed Signups: 2026_09_21`, registered in the
     SendGrid registry, and the twelve contacts added to it
  3. the Single Send draft, to that list plus `Internal: Send Copy`, NOT
     scheduled: scheduling is a separate, double-confirmed step (JP's rule).

The recipient set is fixed here on purpose. JP reviewed these twelve; a
thirteenth would need its own review.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "paths"))
from twy_paths import load_env, sendgrid_registry_path  # noqa: E402

load_env()

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, "/root/twy/yoga-habit")
from newsletter_rendering import render_newsletter  # noqa: E402
from sendgrid_api import SendGridAPI  # noqa: E402
from sendgrid_campaigns import SendGridCampaigns, SendGridRegistry  # noqa: E402
from sendgrid_mailings import INTERNAL_SEND_COPY, validate_sendgrid_name  # noqa: E402
import signup_token  # noqa: E402
import subscribe_confirm  # noqa: E402

MAILING_NAME = "2026_09: Subscription: Confirmation"
LIST_NAME = "Internal: Unconfirmed Signups: 2026_09_21"
BATCH = "2026_09_21"
SUBJECT = "Was this you?"
PREHEADER = "One tap and you're in. If it wasn't you, nothing to do."
CONFIRM_URL = "https://habit.tiffanywoodyoga.com/subscribe/confirm?e={{{{email}}}}&s={signature}"
TEMPLATE = Path(__file__).resolve().parent.parent / "templates" / "subscription_confirmation.md"
STATE_PATH = Path("/root/twy/data/sendgrid/subscription_confirmation_state.json")

# The twelve, as created by the bot (SendGrid contact search, 2026-09-21 07:46 MT).
RECIPIENTS = (
    "a.hi.l.l.g199.3@gmail.com",
    "rayvenkeli@icloud.com",
    "lorettacarter2005@hotmail.com",
    "jetelling@outlook.com",
    "m.s.p.0.76.7@gmail.com",
    "mboswell@webtv.net",
    "jacob.clare@weareother.ca",
    "ggropper@email.sc.edu",
    "lyn@porcelli.org",
    "info@pilloledibusiness.com",
    "john_ferguson2341@yahoo.com",
    "mlane1961@live.com",
)

_FOLLOW_BLOCK = re.compile(
    r"<!--\s*contribution:email_follow_ask\s*-->.*?<!--\s*/contribution:email_follow_ask\s*-->",
    re.DOTALL,
)


def confirm_url(secret: str) -> str:
    return CONFIRM_URL.format(signature=subscribe_confirm.signature(secret, BATCH))


def body_markdown(secret: str) -> str:
    text = TEMPLATE.read_text(encoding="utf-8")
    first_line, _, rest = text.partition("\n")
    return rest.strip().replace("{CONFIRM_URL}", confirm_url(secret))


def rendered(secret: str):
    """The HTML and plain text, the follow-ask footer left out: a
    confirmation asks one thing."""
    result = render_newsletter(body_markdown(secret), use_template=True, preheader=PREHEADER)
    html = _FOLLOW_BLOCK.sub("", result.html)
    return html, result.plain_text


def single_send_payload(registry: SendGridRegistry, list_id: str, secret: str) -> dict:
    html, plain = rendered(secret)
    return {
        "name": validate_sendgrid_name(MAILING_NAME),
        "send_to": {"list_ids": [list_id, registry.list_id(INTERNAL_SEND_COPY)], "all": False},
        "email_config": {
            "subject": SUBJECT,
            "html_content": html,
            "plain_content": plain,
            "generate_plain_content": False,
            "editor": "design",
            "suppression_group_id": registry.suppression_group_id,
            "sender_id": registry.sender_id,
        },
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--execute", action="store_true", help="create the field, the list and the draft at SendGrid")
    args = parser.parse_args(argv)

    secret = signup_token.secret()
    if not secret:
        print("SIGNUP_TOKEN_SECRET is not set; the link cannot be signed", file=sys.stderr)
        return 2
    print("mailing:", MAILING_NAME)
    print("list:   ", LIST_NAME)
    print("field:  ", subscribe_confirm.CONFIRMED_FIELD)
    print("link:   ", confirm_url(secret))
    print("to:     ", len(RECIPIENTS), "addresses plus", INTERNAL_SEND_COPY)
    html, plain = rendered(secret)
    anchor = re.search(r'<a [^>]*href="[^"]*subscribe/confirm[^"]*"[^>]*>', html)
    print("anchor: ", anchor.group(0)[:160] if anchor else "MISSING")
    print("follow footer present:", "contribution:email_follow_ask" in html)
    if not args.execute:
        print("dry run: nothing created")
        return 0

    api = SendGridAPI(os.environ["SENDGRID_API_KEY"])
    registry = SendGridRegistry.load(sendgrid_registry_path())
    campaigns = SendGridCampaigns(api=api, registry=registry, state_path=STATE_PATH)

    field_id = subscribe_confirm.ensure_confirmed_field(api)
    print("field id:", field_id)
    list_id = campaigns.ensure_list(LIST_NAME)
    print("list id: ", list_id)
    job = api.upsert_contacts([list_id], [{"email": email} for email in RECIPIENTS])
    api.wait_contact_job(job)
    print("list count:", api.list_contact_count(list_id))

    existing = api.single_sends_by_name(MAILING_NAME)
    if existing:
        print("single send exists:", [item.get("id") for item in existing], "nothing created")
        return 0
    created = api.create_single_send(single_send_payload(registry, list_id, secret))
    print("single send created:", created.get("id"), "status:", created.get("status"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
