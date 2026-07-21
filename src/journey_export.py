#!/usr/bin/env python3
"""Export MailChimp Automation Flows (Customer Journeys) to disk.

Why this exists
---------------
MailChimp ships no native journey export. Its full account-export ZIP covers
audiences, reports, templates, gallery, SMS, events, appointments and
ecommerce, and its email section is scoped to "all regular emails on the
Campaigns page". Automation Flows are not in it. The journey API is read-only:
the only documented endpoint is the per-step trigger POST. The archive endpoint
that does exist (POST /automations/{id}/actions/archive) is Classic Automations
only, and this account has zero classic automations.

So if a journey is deleted in the UI, nothing brings it back. This snapshot is
the only backup that exists. Run it before retiring or deleting any journey.

What it captures
----------------
Per journey: metadata, the ordered step list (trigger, delays, sends), the
resolved trigger tag, and for every send step the full campaign object plus its
rendered HTML and a markdown conversion.

The trigger tag is resolved against the audience's static segments, because a
journey can point at a DELETED tag. Both copies of YLM Welcome Email Sequence
(4423, 6174) do exactly that: tag_id 3018794 is absent from the audience, and
that dangling reference is what MailChimp surfaces as "errors to resolve".

Read-only. This module never writes to MailChimp and never deletes anything.

Run modes
---------
  python3 journey_export.py                      # all journeys, today's stamp
  python3 journey_export.py --journey 4423       # one journey
  python3 journey_export.py --dry-run            # report, write nothing
  python3 journey_export.py --stamp 2026-07-21   # override the dated dir

Exit codes: 0 all good, 1 one or more journeys failed (twy-run alerts on this).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, "/root/twy/paths")
import twy_paths  # noqa: E402
from twy_paths import mc_journey_export_dir  # noqa: E402

twy_paths.load_env()

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import requests  # noqa: E402
from markdownify import markdownify  # noqa: E402
from mailchimp_campaigns import _mc_auth, _mc_url  # noqa: E402

TIMEOUT = 60


def _get(path: str, params: dict | None = None):
    """GET against the MC API. Raises on non-2xx."""
    if not path.startswith("/"):
        path = "/" + path  # _mc_url concatenates onto ".../3.0"
    r = requests.get(_mc_url(path), auth=_mc_auth(), params=params or {}, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()


def slugify(name: str) -> str:
    """Lowercase underscore slug. Dev Rule #5: no hyphens as separators."""
    s = re.sub(r"[^a-zA-Z0-9]+", "_", name or "unnamed").strip("_").lower()
    return re.sub(r"_+", "_", s) or "unnamed"


def html_to_md(html: str) -> str:
    """Full-body HTML to markdown.

    Deliberately NOT newsletter_back_sync.html_to_md: that one first slices out
    the MAIN CONTENT / DIVIDER editable region, which is right for a newsletter
    round-trip and wrong for an archival snapshot. Here we want the whole
    rendered email, wrapper included.
    """
    md = markdownify(html or "", heading_style="ATX", bullets="-", strip=["span"])
    md = re.sub(r" +\n", "\n", md)
    md = re.sub(r"\n{3,}", "\n\n", md)
    return md.strip() + "\n"


_TAG_CACHE: dict[str, dict] = {}


def fetch_tags(list_id: str) -> dict[int, dict]:
    """Every static segment (tag) on the audience, keyed by id.

    Cached per list_id: every journey on the account resolves against the same
    audience, so without this the tag list is refetched once per journey.
    """
    if list_id in _TAG_CACHE:
        return _TAG_CACHE[list_id]
    tags: dict[int, dict] = {}
    offset = 0
    while True:
        d = _get(f"lists/{list_id}/segments",
                 {"count": 1000, "offset": offset, "type": "static"})
        segs = d.get("segments", [])
        for s in segs:
            tags[s["id"]] = {"name": s.get("name"), "member_count": s.get("member_count")}
        if not segs or len(tags) >= d.get("total_items", 0):
            break
        offset += len(segs)
    _TAG_CACHE[list_id] = tags
    return tags


def resolve_trigger(steps: list[dict], tags: dict[int, dict]) -> dict:
    """Read the real trigger off step 0 and resolve its tag against the audience.

    Never infer a trigger from the journey name. A name-based guess is what
    masks a dangling tag, which is the whole defect this export is meant to
    preserve evidence of.
    """
    if not steps:
        return {"step_type": None, "tag_id": None, "resolved": None, "dangling": False}
    st = steps[0]
    settings = st.get("trigger_settings") or {}
    details = (st.get("trigger_details") or {}).get("tag") or {}
    tag_id = settings.get("tag_id")
    resolved = tags.get(tag_id) if tag_id is not None else None
    return {
        "step_type": st.get("step_type"),
        "display_text": st.get("display_text"),
        "tag_id": tag_id,
        "tag_name_api": details.get("tag_name") or "",
        "resolved_name": resolved["name"] if resolved else None,
        "resolved_member_count": resolved["member_count"] if resolved else None,
        "dangling": tag_id is not None and resolved is None,
    }


def export_journey(journey: dict, out_root: Path, *, dry_run: bool) -> dict:
    """Export one journey. Returns its manifest row."""
    jid = journey["id"]
    name = journey.get("journey_name") or "(unnamed)"
    # A journey that was never built has no steps resource at all and 404s.
    # That is an empty draft, not a failure, so it exports as a zero-step record.
    steps_missing = False
    try:
        steps = (_get(f"customer-journeys/journeys/{jid}/steps") or {}).get("steps", [])
    except requests.HTTPError as e:
        if e.response is not None and e.response.status_code == 404:
            steps, steps_missing = [], True
        else:
            raise
    list_id = journey.get("list_id") or os.getenv("MAILCHIMP_AUDIENCE_ID", "")
    tags = fetch_tags(list_id) if list_id else {}
    trigger = resolve_trigger(steps, tags)

    dirname = f"journey_{jid}_{slugify(name)}"
    jdir = out_root / dirname
    emails = []

    for idx, st in enumerate(steps):
        if st.get("step_type") != "action-send_email":
            continue
        email = (st.get("action_details") or {}).get("email") or {}
        cid = email.get("id")
        if not cid:
            emails.append({"step_index": idx, "campaign_id": None,
                           "error": "send step carries no campaign id"})
            continue
        row = {
            "step_index": idx,
            "campaign_id": cid,
            "web_id": email.get("web_id"),
            "subject_line": (email.get("settings") or {}).get("subject_line"),
            "title": (email.get("settings") or {}).get("title"),
            "status": email.get("status"),
            "emails_sent": email.get("emails_sent"),
            "send_time": email.get("send_time"),
            "long_archive_url": email.get("long_archive_url"),
        }
        try:
            html = (_get(f"campaigns/{cid}/content") or {}).get("html") or ""
        except requests.HTTPError as e:
            row["error"] = f"content fetch failed: {e}"
            emails.append(row)
            continue
        row["html_bytes"] = len(html)
        if not dry_run:
            jdir.mkdir(parents=True, exist_ok=True)
            stem = f"step_{idx:02d}_{cid}"
            (jdir / f"{stem}.html").write_text(html, encoding="utf-8")
            (jdir / f"{stem}.md").write_text(html_to_md(html), encoding="utf-8")
        emails.append(row)

    record = {
        "id": jid,
        "name": name,
        "status": journey.get("status"),
        "list_id": list_id,
        "list_name": journey.get("list_name"),
        "stats": journey.get("stats") or {},
        "first_started_at": journey.get("first_started_at"),
        "last_started_at": journey.get("last_started_at"),
        "created_at": journey.get("created_at"),
        "updated_at": journey.get("updated_at"),
        "step_count": len(steps),
        "steps_missing": steps_missing,
        "email_count": len(emails),
        "trigger": trigger,
        "emails": emails,
        "dir": dirname,
    }

    if not dry_run:
        jdir.mkdir(parents=True, exist_ok=True)
        (jdir / "journey.json").write_text(
            json.dumps({"journey": record, "raw_steps": steps}, indent=1),
            encoding="utf-8")
        (jdir / "flow.md").write_text(render_flow(record, steps), encoding="utf-8")
    return record


def humanize_delay(seconds) -> str:
    """Seconds to a readable wait. MC stores delay_time in seconds."""
    try:
        n = int(seconds)
    except (TypeError, ValueError):
        return str(seconds or "")
    for size, unit in ((86400, "day"), (3600, "hour"), (60, "minute")):
        if n >= size and n % size == 0:
            v = n // size
            return f"{v} {unit}" + ("s" if v != 1 else "")
    return f"{n} seconds"


def render_flow(record: dict, steps: list[dict]) -> str:
    """Human-readable rendering of the flow, top to bottom."""
    t = record["trigger"]
    if t.get("dangling"):
        trig = (f"tag id {t['tag_id']} DANGLING, no such tag in the audience. "
                "This is why the flow cannot fire.")
    elif t.get("resolved_name"):
        trig = f"tag \"{t['resolved_name']}\" ({t['resolved_member_count']} members)"
    else:
        trig = t.get("display_text") or "unknown"

    lines = [
        f"# {record['name']}",
        "",
        f"- Journey id: {record['id']}",
        f"- Status: {record['status']}",
        f"- Trigger: {trig}",
        f"- Steps: {record['step_count']} ({record['email_count']} emails)",
        f"- Started: {record['stats'].get('started')}, "
        f"completed: {record['stats'].get('completed')}",
        f"- Last enrollment: {record['last_started_at'] or 'never'}",
        "",
        "## Flow",
        "",
    ]
    for idx, st in enumerate(steps):
        kind = st.get("step_type")
        if kind == "action-send_email":
            email = (st.get("action_details") or {}).get("email") or {}
            subject = (email.get("settings") or {}).get("subject_line") or "(no subject)"
            lines.append(f"{idx:02d}. SEND `{email.get('id')}`: {subject}")
        elif kind == "delay":
            d = st.get("delay_time")
            label = humanize_delay(d) if d is not None else (st.get("display_subtext") or "")
            lines.append(f"{idx:02d}. WAIT {label}")
        else:
            lines.append(f"{idx:02d}. {kind} {st.get('display_subtext') or ''}".rstrip())
    lines.append("")
    return "\n".join(lines)


def render_readme(rows: list[dict], stamp: str) -> str:
    lines = [
        "# MailChimp Automation Flow export",
        "",
        f"Snapshot taken {stamp}. Read-only capture of every Automation Flow "
        "(Customer Journey) on the TWY account.",
        "",
        "MailChimp has no native journey export and its journey API is "
        "read-only, so this directory is the only backup of these flows that "
        "exists. Keep it before retiring or deleting anything.",
        "",
        "| Journey | id | Status | Emails | Trigger | Started | Last enrollment |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in sorted(rows, key=lambda x: (x["status"] or "", x["name"] or "")):
        t = r["trigger"]
        if t.get("dangling"):
            trig = f"**DANGLING tag {t['tag_id']}**"
        elif t.get("resolved_name"):
            trig = f"{t['resolved_name']} ({t['resolved_member_count']})"
        else:
            trig = t.get("display_text") or "none"
        lines.append(
            f"| [{r['name']}]({r['dir']}/flow.md) | {r['id']} | {r['status']} | "
            f"{r['email_count']} | {trig} | {r['stats'].get('started')} | "
            f"{(r['last_started_at'] or '')[:10] or 'never'} |")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="Export MailChimp Automation Flows to disk.")
    ap.add_argument("--journey", type=int, help="export a single journey id")
    ap.add_argument("--stamp", help="override the YYYY-MM-DD export dir name")
    ap.add_argument("--dry-run", action="store_true", help="report only, write nothing")
    args = ap.parse_args()

    stamp = args.stamp or date.today().isoformat()
    out_root = mc_journey_export_dir(stamp)

    journeys = (_get("customer-journeys/journeys", {"count": 100}) or {}).get("journeys", [])
    if args.journey:
        journeys = [j for j in journeys if j["id"] == args.journey]
        if not journeys:
            print(f"journey {args.journey} not found", file=sys.stderr)
            return 1

    print(f"exporting {len(journeys)} journeys to {out_root}"
          f"{' (dry run)' if args.dry_run else ''}")

    rows, failed = [], []
    for j in journeys:
        try:
            row = export_journey(j, out_root, dry_run=args.dry_run)
        except Exception as e:  # fail soft: one bad journey must not lose the rest
            failed.append((j.get("id"), repr(e)))
            print(f"  FAILED {j.get('id')} {j.get('journey_name')}: {e}", file=sys.stderr)
            continue
        rows.append(row)
        flag = "  DANGLING TRIGGER" if row["trigger"].get("dangling") else ""
        if row.get("steps_missing"):
            flag += "  (empty draft, no steps)"
        print(f"  {row['id']:<6} {row['status']:<8} {row['email_count']:>2} emails  "
              f"{row['name']}{flag}")

    if not args.dry_run and rows:
        out_root.mkdir(parents=True, exist_ok=True)
        (out_root / "manifest.json").write_text(
            json.dumps({"stamp": stamp, "journey_count": len(rows),
                        "failed": failed, "journeys": rows}, indent=1),
            encoding="utf-8")
        (out_root / "README.md").write_text(render_readme(rows, stamp), encoding="utf-8")

    dangling = [r["id"] for r in rows if r["trigger"].get("dangling")]
    print(f"done: {len(rows)} exported, {len(failed)} failed"
          f"{', dangling triggers: ' + str(dangling) if dangling else ''}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
