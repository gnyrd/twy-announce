"""Generate a period's newsletter drafts from the Monthly Overview, without Tweee.

For each audience it assembles the same prompt Tweee would get, runs it through
an automated Anthropic call, validates the result, and (unless --dry-run) posts
the drafts to the Class Plans newsletter endpoint, the same chokepoint Tweee
posts to. That saves them as ordinary drafts. Nothing is approved or scheduled
here; a human still approves the complete set before anything sends.

  python3 src/run_newsletter_autogen.py --year 2026 --month 10 --dry-run
  python3 src/run_newsletter_autogen.py --year 2026 --month 10   # posts drafts
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(__file__)))
sys.path.insert(0, "/root/twy/paths")
sys.path.insert(0, "/root/twy/platform")

import twy_paths  # noqa: E402

twy_paths.load_env()

import anthropic  # noqa: E402
from generate_newsletter_prompts import (  # noqa: E402
    load_month_overview,
    load_plans_for_month,
)
import newsletter_autogen as autogen  # noqa: E402

DASHBOARD = os.environ.get("TWY_DASHBOARD_URL", "http://127.0.0.1:5003")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--year", type=int, required=True)
    parser.add_argument("--month", type=int, required=True)
    parser.add_argument("--audiences", default="lifestyle,non_lifestyle")
    parser.add_argument("--model", default="claude-opus-4-8")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="generate and validate but do not post the drafts",
    )
    args = parser.parse_args(argv)

    overview = load_month_overview(args.month)
    if not overview:
        print(f"no Monthly Overview for month {args.month}", file=sys.stderr)
        return 2
    plans = load_plans_for_month(args.year, args.month)

    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    generate_fn = autogen.anthropic_generate_fn(client, model=args.model)

    sections: dict[str, dict] = {}
    for audience in [a.strip() for a in args.audiences.split(",") if a.strip()]:
        prompt = autogen.assemble_prompt(audience, overview, plans, args.year, args.month)
        draft = autogen.generate_from_prompt(audience, prompt, generate_fn)
        sections[audience] = {
            "subject": draft["subject"],
            "preheader": draft["preheader"],
            "body": draft["body"],
        }
        print(
            f"{audience}: subject={draft['subject'][:60]!r} "
            f"preheader={len(draft['preheader'])}c body={len(draft['body'])}c"
        )

    if args.dry_run:
        print("DRY RUN: generated and validated, nothing posted or notified.")
        return 0

    url = f"{DASHBOARD}/api/newsletters/{args.month}"
    request = urllib.request.Request(
        url,
        data=json.dumps(sections).encode("utf-8"),
        headers={"Content-Type": "application/json", "User-Agent": "twy-autogen/1.0"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        print(f"POST {url} -> {response.status}")
        print(response.read().decode("utf-8")[:300])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
