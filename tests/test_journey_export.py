"""Tests for the MailChimp Automation Flow exporter.

Pure-function coverage only. Nothing here touches the network or MailChimp.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from journey_export import (  # noqa: E402
    humanize_delay,
    html_to_md,
    render_flow,
    resolve_trigger,
    slugify,
)


# --- slugify: Dev Rule #5, underscores never hyphens -------------------------

def test_slugify_underscores_not_hyphens():
    assert slugify("Kula cleanse - Marketing Emails") == "kula_cleanse_marketing_emails"


def test_slugify_collapses_and_trims():
    assert slugify("  Optimal   Blueprint Series  ") == "optimal_blueprint_series"


def test_slugify_handles_empty():
    assert slugify("") == "unnamed"
    assert slugify(None) == "unnamed"


# --- resolve_trigger: read the trigger, never infer it -----------------------

TAGS = {2964430: {"name": "New Subscriber YLS Membership", "member_count": 2}}


def _trigger_step(tag_id, tag_name=""):
    return {
        "step_type": "trigger-tag_added",
        "display_text": "Contact tagged ",
        "trigger_settings": {"tag_id": tag_id},
        "trigger_details": {"tag": {"tag_name": tag_name}},
    }


def test_resolve_trigger_resolves_live_tag():
    t = resolve_trigger([_trigger_step(2964430, "New Subscriber YLS Membership")], TAGS)
    assert t["resolved_name"] == "New Subscriber YLS Membership"
    assert t["resolved_member_count"] == 2
    assert t["dangling"] is False


def test_resolve_trigger_flags_deleted_tag_as_dangling():
    """tag_id 3018794 is the real defect on journeys 4423 and 6174: the tag was
    deleted, so the flow cannot fire. It must never be reported as resolved."""
    t = resolve_trigger([_trigger_step(3018794)], TAGS)
    assert t["tag_id"] == 3018794
    assert t["resolved_name"] is None
    assert t["dangling"] is True


def test_resolve_trigger_empty_steps():
    t = resolve_trigger([], TAGS)
    assert t["tag_id"] is None
    assert t["dangling"] is False


def test_resolve_trigger_no_tag_id_is_not_dangling():
    """A signup-triggered journey has no tag at all. Absent is not broken."""
    step = {"step_type": "trigger-signup", "display_text": "Contact signs up",
            "trigger_settings": {}, "trigger_details": {}}
    assert resolve_trigger([step], TAGS)["dangling"] is False


# --- humanize_delay ----------------------------------------------------------

def test_humanize_delay_units():
    assert humanize_delay(86400) == "1 day"
    assert humanize_delay(172800) == "2 days"
    assert humanize_delay(3600) == "1 hour"
    assert humanize_delay(120) == "2 minutes"


def test_humanize_delay_non_multiple_falls_back_to_seconds():
    assert humanize_delay(90) == "90 seconds"


def test_humanize_delay_tolerates_junk():
    assert humanize_delay(None) == ""
    assert humanize_delay("soon") == "soon"


# --- html_to_md: whole body, not the newsletter editable region --------------

def test_html_to_md_keeps_full_body():
    html = "<html><body><h1>Hi</h1><p>Line</p></body></html>"
    md = html_to_md(html)
    assert "Hi" in md and "Line" in md


def test_html_to_md_does_not_require_newsletter_markers():
    """newsletter_back_sync.html_to_md raises without MAIN CONTENT markers.
    An archival snapshot must never depend on them."""
    assert html_to_md("<p>no markers here</p>").strip() == "no markers here"


def test_html_to_md_handles_empty():
    assert html_to_md("") == "\n"


# --- render_flow -------------------------------------------------------------

def _record(**over):
    r = {
        "id": 4423, "name": "YLM Welcome Email Sequence", "status": "paused",
        "step_count": 4, "email_count": 2, "last_started_at": "2026-01-08T18:59:16+00:00",
        "stats": {"started": 10, "completed": 8},
        "trigger": {"tag_id": 3018794, "resolved_name": None, "dangling": True},
    }
    r.update(over)
    return r


def test_render_flow_states_dangling_cause():
    steps = [_trigger_step(3018794),
             {"step_type": "action-send_email",
              "action_details": {"email": {"id": "abc", "settings": {"subject_line": "Welcome"}}}},
             {"step_type": "delay", "delay_time": 86400}]
    out = render_flow(_record(), steps)
    assert "DANGLING" in out
    assert "cannot fire" in out
    assert "SEND `abc`: Welcome" in out
    assert "WAIT 1 day" in out


def test_render_flow_shows_resolved_tag_with_member_count():
    rec = _record(trigger={"tag_id": 2964430,
                           "resolved_name": "New Subscriber YLS Membership",
                           "resolved_member_count": 2, "dangling": False})
    out = render_flow(rec, [_trigger_step(2964430)])
    assert "New Subscriber YLS Membership" in out
    assert "2 members" in out
    assert "DANGLING" not in out
