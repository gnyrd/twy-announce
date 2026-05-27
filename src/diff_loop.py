"""
Newsletter diff loop -- inline in monthly prompt-gen cron.

Phase 1: archive_prior_month_sent() pulls each MC sent campaign for the prior month,
converts the HTML body to markdown, and overwrites the .md file on disk in the
`# {subject}\n\n{body}` format used by the rest of the TWY newsletter pipeline.

This runs as part of generate_newsletter_prompts.py on the 25th of the month,
processing the month that just wrapped (June 25 cron processes June's sends,
which by then are all complete: lifestyle/non-lifestyle June 1, PH1 +24h after
Habit class, PH2 +7d after).

Future phases (2-4) layered on top:
- diff_record() captures Tweee-submitted vs Tiff-sent deltas
- extract_patterns() pulls structural signals + phrase deltas
- apply_updates() rotates _REF_<MMM>_* constants + appends to _VOICE_GUARDRAILS
"""

import os, re, sys, json, requests, html2text
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(__file__))
from newsletter import newsletter_path
from mailchimp_campaigns import find_campaign_by_title, _mc_url, _mc_auth
from twy_paths import newsletters_dir

MOUNTAIN = ZoneInfo("America/Denver")

# Diff records get stored alongside the newsletters dir
NEWSLETTER_DIFFS_DIR = newsletters_dir().parent / "newsletter-diffs"

# Map internal audience keys to the campaign-title label segment used in MC.
# Production convention (as of June 2026). Title pattern:
#   "{year:04d}-{month:02d} — {label} — Yoga Habit"
# except PH1/PH2 which use:
#   "{year:04d}-{month:02d} — Yoga Habit — {label}"
AUDIENCE_TITLE_MAP = {
    "lifestyle":      ("Lifestyle",          "prefix"),
    "non_lifestyle":  ("Non-Lifestyle",      "prefix"),
    "non_opener":     ("Non-Opener Resend",  "prefix"),
    "gentle_nudge":   ("Gentle Nudge",       "prefix"),
    "reminder":       ("Day-Before Reminder","prefix"),
    "ph1":            ("Post-Class 1",       "suffix"),
    "ph2":            ("Post-Class 2",       "suffix"),
}


def _campaign_title(year: int, month: int, audience: str) -> str:
    label, position = AUDIENCE_TITLE_MAP[audience]
    if position == "prefix":
        return f"{year:04d}-{month:02d} — {label} — Yoga Habit"
    else:
        return f"{year:04d}-{month:02d} — Yoga Habit — {label}"


def _strip_mc_tokens(html: str) -> str:
    """Remove MailChimp template tokens like *|MC:SUBJECT|* before HTML→md conversion."""
    return re.sub(r"\*\|[A-Z_:]+\|\*", "", html)


def _strip_utm_from_links(md: str) -> str:
    """Strip ?utm_* query params from markdown link URLs.

    Matches `](url?utm_...)` and reduces to `](url)`. Preserves other query params.
    """
    def _fix(m):
        prefix, url, suffix = m.group(1), m.group(2), m.group(3)
        # Drop ?utm_*=... and &utm_*=... from URL
        cleaned = re.sub(r"[?&]utm_[^=&]+=[^&)]*", "", url)
        # If we removed the leading ?, restore ? for any remaining params
        if "?" not in cleaned and "&" in cleaned:
            cleaned = cleaned.replace("&", "?", 1)
        return f"{prefix}{cleaned}{suffix}"
    return re.sub(r"(\]\()([^)]+?)(\))", _fix, md)


_CONTENT_START_RE = re.compile(r"<!--\s*MAIN CONTENT\s*-->", re.IGNORECASE)
_CONTENT_END_RE   = re.compile(r"<!--\s*(?:DIVIDER|SOCIAL ICONS|FOOTER)\s*-->", re.IGNORECASE)
_INNER_DIV_RE     = re.compile(r"<div\b[^>]*>(.*?)</div>", re.IGNORECASE | re.DOTALL)


def _extract_main_content(html: str) -> str:
    """Extract the editable body block from MC HTML, between the
    <!-- MAIN CONTENT --> and <!-- DIVIDER --> markers used in TWY's MC template.

    Then peel off the `<tr><td><div>...</div></td></tr>` wrapper because
    html2text collapses paragraphs inside table cells. We want just the inner
    block (the <p> elements + their text).

    Falls back to the marker-delimited block or full HTML if extraction fails.
    """
    start_m = _CONTENT_START_RE.search(html)
    if not start_m:
        return html  # fall back to full HTML
    start = start_m.end()
    end_m = _CONTENT_END_RE.search(html, pos=start)
    end = end_m.start() if end_m else len(html)
    block = html[start:end]

    # Peel the table-cell + div wrapper to get the innermost content
    inner_m = _INNER_DIV_RE.search(block)
    if inner_m:
        return inner_m.group(1)
    return block


def _convert_html_body_to_md(html: str) -> str:
    """MC HTML → markdown. Extracts main-content block, strips MC tokens,
    runs html2text, strips utm params."""
    html = _extract_main_content(html)
    html = _strip_mc_tokens(html)
    h = html2text.HTML2Text()
    h.body_width = 0  # no line wrapping
    h.ignore_emphasis = False
    h.ignore_links = False
    h.ignore_images = True
    h.unicode_snob = True  # keep unicode chars as-is (em-dashes etc.)
    md = h.handle(html).strip()
    md = _strip_utm_from_links(md)
    # Strip standalone --- horizontal rules (chrome artifact from MC's table-based
    # button markup; Tiff doesn't author HR's in her content)
    md = re.sub(r"\n[ \t]*---[ \t]*(?=\n)", "", md)
    # Collapse runs of 3+ blank lines down to 2
    md = re.sub(r"\n{3,}", "\n\n", md)
    # Strip trailing whitespace on each line (html2text leaves `  ` hard-breaks
    # that aren't load-bearing for our purposes)
    md = "\n".join(line.rstrip() for line in md.split("\n"))
    return md.strip()


def _parse_md_file(path: Path) -> tuple[str, str]:
    """Parse a TWY-format .md file. Returns (subject, body).

    Expected format: first line is `# {subject}`, then blank line(s), then body.
    """
    if not path.exists():
        return "", ""
    text = path.read_text()
    lines = text.split("\n", 2)
    subject = lines[0].lstrip("# ").strip() if lines else ""
    body = lines[2].strip() if len(lines) > 2 else ""
    return subject, body


def _diff_record_path(year: int, month: int, audience: str) -> Path:
    return NEWSLETTER_DIFFS_DIR / f"{year:04d}-{month:02d}" / f"{audience}.diff.json"


def _write_diff_record(
    audience: str, year: int, month: int,
    tweee_subject: str, tweee_body: str,
    tiff_subject: str, tiff_body: str,
) -> Path:
    """Phase 2: capture the Tweee-submitted vs Tiff-sent delta as a JSON record.

    Phase 3 will populate removed_phrases / added_phrases / structural_signals.
    For now we save the raw sides + boolean change flags.
    """
    record = {
        "audience": audience,
        "month": f"{year:04d}-{month:02d}",
        "captured_at": datetime.now(MOUNTAIN).isoformat(),
        "tweee_submitted": {"subject": tweee_subject, "body_md": tweee_body},
        "tiff_sent": {"subject": tiff_subject, "body_md": tiff_body},
        "subject_changed": tweee_subject.strip() != tiff_subject.strip(),
        "body_changed": tweee_body.strip() != tiff_body.strip(),
        # Phase 3 fields, populated when extract_patterns() runs:
        "removed_phrases": [],
        "added_phrases": [],
        "structural_signals": [],
    }
    path = _diff_record_path(year, month, audience)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, indent=2, ensure_ascii=False))
    return path


# Phase 4 (slot-rotation auto-apply) was removed in the KISS refactor. The
# habit_newsletter_prompt assemblers now read recent .md files directly from
# disk at prompt-build time via _format_recent_references(), so there's
# nothing to rotate in Python source code anymore. Archival (Phase 1) +
# diff capture (Phase 2) + pattern extraction (Phase 3) + Slack review post
# (Phase 3) are still in place.

# ----------------------------------------------------------------------------
# Phase 3: pattern extraction
# ----------------------------------------------------------------------------

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?…])\s+(?=[A-Z\"“])|\n{2,}")

_OPPOSITIONAL_OPENER_PATTERNS = [
    re.compile(r"^Not because\s+\w+", re.IGNORECASE),
    re.compile(r"^Some\s+\w+\s+(?:make|are|practice)", re.IGNORECASE),
    re.compile(r"^Sometimes\s+\w+", re.IGNORECASE),
    re.compile(r"^What if\s+", re.IGNORECASE),
    re.compile(r"^Have you (?:ever|been)\s+", re.IGNORECASE),
]

_SOMATIC_MARKETING_PHRASES = [
    "the body realizes",
    "the body finally trusts",
    "your body finally trusts itself",
    "stop bracing",
    "stop forcing",
    "let yourself bloom",
    "tired of performing",
    "tired of tightening",
    "tired of waiting to feel",
    "what's left after you stop",
    "stop forcing it",
]


def _split_sentences(text: str) -> list[str]:
    """Coarse sentence split. Imperfect but good enough for diffing."""
    parts = _SENTENCE_SPLIT_RE.split(text)
    return [p.strip() for p in parts if p and p.strip()]


def _normalize(s: str) -> str:
    """Lowercase + collapse whitespace for set-based comparison."""
    return re.sub(r"\s+", " ", s.strip().lower())


def diff_phrases(tweee_body: str, tiff_body: str) -> tuple[list[str], list[str]]:
    """Return (removed, added) sentence lists.

    A sentence is "removed" if it appears (modulo case/whitespace) in tweee_body
    but not tiff_body, and vice versa for "added".
    """
    t_sents = _split_sentences(tweee_body)
    f_sents = _split_sentences(tiff_body)
    t_norm = {_normalize(s) for s in t_sents}
    f_norm = {_normalize(s) for s in f_sents}
    removed = [s for s in t_sents if _normalize(s) not in f_norm]
    added = [s for s in f_sents if _normalize(s) not in t_norm]
    return removed, added


def detect_structural_signals(
    tweee_subject: str, tweee_body: str,
    tiff_subject: str, tiff_body: str,
) -> list[str]:
    """Rule-based detection of specific Tweee→Tiff transformations."""
    signals = []

    # h1_removed: Tweee body began with a markdown header; Tiff body did not.
    # (Tweee no longer submits H1 bodies post-API-validator, but historical
    # diffs against pre-validator submissions still catch this.)
    if tweee_body.lstrip().startswith("#") and not tiff_body.lstrip().startswith("#"):
        signals.append("h1_removed")

    # rhetorical_question_subject_replaced: Tweee subject ended with `?`
    if tweee_subject.rstrip().endswith("?") and not tiff_subject.rstrip().endswith("?"):
        signals.append("rhetorical_question_subject_replaced")

    # oppositional_opener_replaced: Tweee body's first sentence matched a
    # contrast/hook pattern; Tiff's first sentence did not.
    t_sents = _split_sentences(tweee_body)
    f_sents = _split_sentences(tiff_body)
    tweee_first = t_sents[0] if t_sents else ""
    tiff_first = f_sents[0] if f_sents else ""
    tweee_opp = any(p.match(tweee_first) for p in _OPPOSITIONAL_OPENER_PATTERNS)
    tiff_opp = any(p.match(tiff_first) for p in _OPPOSITIONAL_OPENER_PATTERNS)
    if tweee_opp and not tiff_opp:
        signals.append("oppositional_opener_replaced")

    # somatic_marketing_phrase_removed: each curated phrase present in Tweee,
    # absent in Tiff, gets its own signal.
    tweee_lower = tweee_body.lower()
    tiff_lower = tiff_body.lower()
    for phrase in _SOMATIC_MARKETING_PHRASES:
        if phrase in tweee_lower and phrase not in tiff_lower:
            signals.append(f"somatic_marketing_phrase_removed:{phrase}")

    return signals


def extract_patterns(diff_record: dict) -> dict:
    """Augment a diff record with phrase deltas + structural signals. Mutates and returns."""
    tweee = diff_record["tweee_submitted"]
    tiff = diff_record["tiff_sent"]
    removed, added = diff_phrases(tweee["body_md"], tiff["body_md"])
    signals = detect_structural_signals(
        tweee["subject"], tweee["body_md"],
        tiff["subject"], tiff["body_md"],
    )
    diff_record["removed_phrases"] = removed
    diff_record["added_phrases"] = added
    diff_record["structural_signals"] = signals
    return diff_record


def extract_patterns_for_month(year: int, month: int) -> dict:
    """Run extract_patterns() on every existing diff record for the month.

    Re-writes each diff JSON in place with the augmented fields. Returns a
    summary dict {audience: {signals, removed_count, added_count}}.
    """
    diffs_dir = NEWSLETTER_DIFFS_DIR / f"{year:04d}-{month:02d}"
    if not diffs_dir.exists():
        return {"error": f"no diffs dir: {diffs_dir}"}

    summary = {}
    for audience in AUDIENCE_TITLE_MAP:
        path = diffs_dir / f"{audience}.diff.json"
        if not path.exists():
            continue
        with path.open() as f:
            rec = json.load(f)
        extract_patterns(rec)
        path.write_text(json.dumps(rec, indent=2, ensure_ascii=False))
        summary[audience] = {
            "signals": rec["structural_signals"],
            "removed_count": len(rec["removed_phrases"]),
            "added_count": len(rec["added_phrases"]),
        }
    return summary


_SIGNOFF_RE = re.compile(
    r"^\s*[—-]?\s*(?:Tiff|Tiffany|With love[,.]?|Love[,.]?|Hi[,.]?|Hello[,.]?|Hey love[,.]?|Hi there[,.]?)\s*$",
    re.IGNORECASE,
)
_LINK_DOMINATED_RE = re.compile(r"\[[^\]]+\]\([^)]+\)")
_CLASS_INFO_RE = re.compile(
    r"^\s*\w+\s+\d+(?:,\s+\d{4})?\s*[|]\s*\d{2}:\d{2}|^\s*\d{1,2}:\d{2}\s*MT",
)


def _is_meaningful_phrase(phrase: str) -> bool:
    """Filter out noise from candidate removed-phrase lists. Phrases that fail
    these tests are not useful as 'tends to avoid' guidance."""
    p = phrase.strip()

    # Too short
    if len(p) < 30:
        return False
    # Fewer than 5 words
    if len(p.split()) < 5:
        return False
    # Bare signoff / greeting line
    if _SIGNOFF_RE.match(p):
        return False
    # Mostly a markdown link (>50% of chars inside link syntax)
    link_chars = sum(len(m.group(0)) for m in _LINK_DOMINATED_RE.finditer(p))
    if link_chars > len(p) * 0.5:
        return False
    # Class info line ("May 16 | 09:00 MT | 60 min | Free on Zoom")
    if _CLASS_INFO_RE.match(p):
        return False
    return True


def post_review_candidates(year: int, month: int, slack_post_fn=None) -> str:
    """Format a #review-newsletters post with extracted patterns. If slack_post_fn
    is provided, also post it. Otherwise return the text only (for dry-run).
    """
    diffs_dir = NEWSLETTER_DIFFS_DIR / f"{year:04d}-{month:02d}"
    if not diffs_dir.exists():
        return f"(no diffs at {diffs_dir})"

    month_label = datetime(year, month, 1).strftime("%B %Y")
    next_month_label = datetime(year + (1 if month == 12 else 0),
                                 1 if month == 12 else month + 1, 1).strftime("%B %Y")

    lines = [f":memo: *Newsletter prompt update candidates ({month_label} -> {next_month_label} prompts)*", ""]

    # Aggregate signals across audiences
    signal_audiences = {}  # signal → [audiences]
    removed_corpus = {}    # phrase → [audiences]
    audience_summaries = []

    for audience in AUDIENCE_TITLE_MAP:
        path = diffs_dir / f"{audience}.diff.json"
        if not path.exists():
            continue
        with path.open() as f:
            rec = json.load(f)
        sigs = rec.get("structural_signals", [])
        removed = rec.get("removed_phrases", [])
        added_count = len(rec.get("added_phrases", []))

        for sig in sigs:
            signal_audiences.setdefault(sig, []).append(audience)
        for phrase in removed:
            removed_corpus.setdefault(phrase, []).append(audience)

        if rec.get("subject_changed") or rec.get("body_changed") or sigs:
            audience_summaries.append((audience, rec, sigs, len(removed), added_count))

    if not audience_summaries:
        lines.append("_No edit deltas detected — Tweee's submissions shipped as-is. No prompt changes proposed._")
        text = "\n".join(lines)
        if slack_post_fn:
            slack_post_fn(text)
        return text

    # References to rotate (any audience that had body changes)
    lines.append("*References to rotate* (Tiff's sent version becomes the new _REF_<month>_<audience> exemplar):")
    for audience, rec, sigs, rcount, acount in audience_summaries:
        preview = rec["tiff_sent"]["body_md"][:160].replace("\n", " ") + ("..." if len(rec["tiff_sent"]["body_md"]) > 160 else "")
        lines.append(f"  - `_REF_<{year}-{month:02d}>_{audience.upper()}` -> _{preview}_")
    lines.append("")

    # Structural signals
    if signal_audiences:
        lines.append("*Structural signals detected:*")
        for sig, auds in signal_audiences.items():
            lines.append(f"  - `{sig}` ({len(auds)} of {len(AUDIENCE_TITLE_MAP)}: {', '.join(auds)})")
        lines.append("")

    # Candidate "tends to avoid" additions from removed phrases (filtered)
    meaningful_removed = {p: auds for p, auds in removed_corpus.items() if _is_meaningful_phrase(p)}
    if meaningful_removed:
        lines.append("*Candidate \"tends to avoid\" additions* (sentences Tweee submitted that Tiff cut; multi-audience repeats are stronger signal):")
        # Sort by audience count desc, then phrase length asc (shorter = more general)
        sorted_phrases = sorted(meaningful_removed.items(), key=lambda kv: (-len(kv[1]), len(kv[0])))
        for phrase, auds in sorted_phrases[:10]:  # top 10
            preview = phrase[:140] + ("..." if len(phrase) > 140 else "")
            lines.append(f"  - ({len(auds)}x) _{preview}_")
        if len(sorted_phrases) > 10:
            lines.append(f"  ... and {len(sorted_phrases) - 10} more")
        lines.append("")
    filtered_out = len(removed_corpus) - len(meaningful_removed)
    if filtered_out:
        lines.append(f"_({filtered_out} additional removed phrases filtered as noise: signoffs, bare CTAs, class info lines, very short fragments.)_")
        lines.append("")

    lines.append("_References are read directly from `/root/twy/data/newsletters/YYYY-MM/<audience>.md` at prompt-build time. The archival step above just overwrote those `.md` files with Tiff actually-sent versions; next month's prompts will embed them. The pre-overwrite (Tweee submitted) version is preserved in the diff records under `/root/twy/data/newsletter-diffs/YYYY-MM/<audience>.diff.json` -> `tweee_submitted.body_md`._")

    text = "\n".join(lines)
    if slack_post_fn:
        slack_post_fn(text)
    return text


def _write_summary(year: int, month: int, audience_results: dict, diff_paths: dict) -> Path:
    """Aggregate summary record across all audiences for the month."""
    summary = {
        "month": f"{year:04d}-{month:02d}",
        "captured_at": datetime.now(MOUNTAIN).isoformat(),
        "audiences": {},
    }
    for audience, status in audience_results.items():
        entry = {"status": status}
        diff_path = diff_paths.get(audience)
        if diff_path and diff_path.exists():
            with diff_path.open() as f:
                rec = json.load(f)
            entry["subject_changed"] = rec["subject_changed"]
            entry["body_changed"] = rec["body_changed"]
        summary["audiences"][audience] = entry
    out = NEWSLETTER_DIFFS_DIR / f"{year:04d}-{month:02d}" / "summary.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, indent=2, ensure_ascii=False))
    return out


def _fetch_campaign(cid: str) -> dict:
    """Fetch campaign metadata + content from MC. Returns dict with subject, html."""
    meta_r = requests.get(_mc_url(f"/campaigns/{cid}"), auth=_mc_auth(), timeout=30)
    meta_r.raise_for_status()
    meta = meta_r.json()
    cont_r = requests.get(_mc_url(f"/campaigns/{cid}/content"), auth=_mc_auth(), timeout=30)
    cont_r.raise_for_status()
    content = cont_r.json()
    return {
        "subject": meta.get("settings", {}).get("subject_line", ""),
        "html": content.get("html", "") or "",
        "status": meta.get("status", ""),
        "send_time": meta.get("send_time", ""),
    }


def archive_prior_month_sent(year: int, month: int) -> dict:
    """Phases 1+2: for each audience in the given month, capture diff record
    of Tweee-submitted vs Tiff-sent, then overwrite .md with Tiff's sent version.

    Args:
        year, month: the month that just wrapped (e.g. on June 25 cron, pass year=2026, month=6).

    Returns: dict {audience: status}.
        Possible statuses: "archived", "no_mc_campaign", "not_sent", "error: <msg>"
    """
    results = {}
    diff_paths = {}
    for audience in AUDIENCE_TITLE_MAP:
        title = _campaign_title(year, month, audience)
        try:
            campaign = find_campaign_by_title(title)
        except Exception as e:
            results[audience] = f"error: campaign lookup failed: {e}"
            continue
        if not campaign:
            results[audience] = "no_mc_campaign"
            continue

        try:
            data = _fetch_campaign(campaign["id"])
        except Exception as e:
            results[audience] = f"error: fetch failed: {e}"
            continue

        if data["status"] != "sent":
            results[audience] = f"not_sent (status={data['status']})"
            continue

        # Phase 2: capture diff BEFORE overwriting .md
        nl_path = newsletter_path(year, month, audience)
        tweee_subject, tweee_body = _parse_md_file(nl_path)
        tiff_subject = data["subject"]
        tiff_body = _convert_html_body_to_md(data["html"])

        diff_paths[audience] = _write_diff_record(
            audience, year, month,
            tweee_subject, tweee_body,
            tiff_subject, tiff_body,
        )

        # Phase 1: overwrite .md with Tiff's sent version
        nl_path.parent.mkdir(parents=True, exist_ok=True)
        nl_path.write_text(f"# {tiff_subject}\n\n{tiff_body}\n")

        results[audience] = "archived"

    # Phase 2: write aggregate summary
    _write_summary(year, month, results, diff_paths)

    return results


def archive_with_explicit_titles(year: int, month: int, titles: dict) -> dict:
    """Test helper: archive + diff-capture with a custom audience-to-title-pattern map.

    Used to test against months that used different audience-label conventions
    than the AUDIENCE_TITLE_MAP defaults. titles is {audience: full_title_string}.
    """
    results = {}
    diff_paths = {}
    for audience, title in titles.items():
        try:
            campaign = find_campaign_by_title(title)
        except Exception as e:
            results[audience] = f"error: campaign lookup failed: {e}"
            continue
        if not campaign:
            results[audience] = f"no_mc_campaign ({title!r})"
            continue

        try:
            data = _fetch_campaign(campaign["id"])
        except Exception as e:
            results[audience] = f"error: fetch failed: {e}"
            continue

        if data["status"] != "sent":
            results[audience] = f"not_sent (status={data['status']})"
            continue

        nl_path = newsletter_path(year, month, audience)
        tweee_subject, tweee_body = _parse_md_file(nl_path)
        tiff_subject = data["subject"]
        tiff_body = _convert_html_body_to_md(data["html"])

        diff_paths[audience] = _write_diff_record(
            audience, year, month,
            tweee_subject, tweee_body,
            tiff_subject, tiff_body,
        )

        nl_path.parent.mkdir(parents=True, exist_ok=True)
        nl_path.write_text(f"# {tiff_subject}\n\n{tiff_body}\n")

        results[audience] = "archived"

    _write_summary(year, month, results, diff_paths)

    return results


if __name__ == "__main__":
    # Sanity check entry point: call as `python3 diff_loop.py <year> <month>`
    import sys
    if len(sys.argv) >= 3:
        y, m = int(sys.argv[1]), int(sys.argv[2])
        print(f"Archiving {y:04d}-{m:02d}...")
        results = archive_prior_month_sent(y, m)
        for aud, status in results.items():
            print(f"  {aud:15s} {status}")
    else:
        print(f"Usage: {sys.argv[0]} <year> <month>")
