"""Which Resend outcomes stop a sequence, and which must not.

The cost is asymmetric and that shapes every case here. Suppressing wrongly
silently denies a paying member the rest of a sequence they bought. Failing to
suppress a real hard bounce keeps mailing an address that does not exist,
which burns the sending reputation of a brand new domain.
"""

import json

from resend_events import BOUNCED, COMPLAINED, load_suppressions


def write(path, records):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record) + "\n")
    return path


def event(kind, address, bounce_type=None, svix_id="msg_1"):
    data = {"email_id": "e1", "to": [address], "subject": "s"}
    if bounce_type is not None:
        data["bounce"] = {"type": bounce_type, "subType": "General", "message": "m"}
    return {"svix_id": svix_id, "type": kind, "tags": {}, "event": {"data": data}}


def test_a_permanent_bounce_stops_the_sequence(tmp_path):
    log = write(tmp_path / "events.jsonl", [
        event("email.bounced", "gone@example.com", "Permanent"),
    ])
    assert load_suppressions(log) == {"gone@example.com": BOUNCED}


def test_a_transient_bounce_does_not(tmp_path):
    """A full mailbox says nothing about the address. Retrying is correct."""
    log = write(tmp_path / "events.jsonl", [
        event("email.bounced", "full@example.com", "Transient"),
    ])
    assert load_suppressions(log) == {}


def test_a_complaint_stops_the_sequence(tmp_path):
    log = write(tmp_path / "events.jsonl", [
        event("email.complained", "cross@example.com"),
    ])
    assert load_suppressions(log) == {"cross@example.com": COMPLAINED}


def test_a_failed_send_does_not_suppress(tmp_path):
    """It never left, so nothing reached anybody and the address is unproven."""
    log = write(tmp_path / "events.jsonl", [
        event("email.failed", "unknown@example.com"),
    ])
    assert load_suppressions(log) == {}


def test_delivered_and_opened_never_suppress(tmp_path):
    log = write(tmp_path / "events.jsonl", [
        event("email.sent", "fine@example.com"),
        event("email.delivered", "fine@example.com"),
        event("email.opened", "fine@example.com"),
        event("email.clicked", "fine@example.com"),
    ])
    assert load_suppressions(log) == {}


def test_a_complaint_outranks_a_bounce_for_the_same_address(tmp_path):
    """Both orderings agree, because the answer is a decision they made."""
    forward = write(tmp_path / "a.jsonl", [
        event("email.bounced", "both@example.com", "Permanent", "m1"),
        event("email.complained", "both@example.com", svix_id="m2"),
    ])
    backward = write(tmp_path / "b.jsonl", [
        event("email.complained", "both@example.com", svix_id="m1"),
        event("email.bounced", "both@example.com", "Permanent", "m2"),
    ])
    assert load_suppressions(forward) == {"both@example.com": COMPLAINED}
    assert load_suppressions(backward) == {"both@example.com": COMPLAINED}


def test_addresses_are_matched_case_and_space_insensitively(tmp_path):
    log = write(tmp_path / "events.jsonl", [
        event("email.bounced", "  Gone@Example.COM ", "Permanent"),
    ])
    assert load_suppressions(log) == {"gone@example.com": BOUNCED}


def test_a_missing_log_answers_empty_rather_than_raising(tmp_path):
    """It fails OPEN on purpose, so it is paired with the SendGrid checks."""
    assert load_suppressions(tmp_path / "nope.jsonl") == {}


def test_one_malformed_line_does_not_lose_the_rest(tmp_path):
    """A bad write must not silently un-suppress everyone after it."""
    path = tmp_path / "events.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        handle.write("{not json\n")
        handle.write(json.dumps(event("email.bounced", "gone@example.com", "Permanent")) + "\n")
    assert load_suppressions(path) == {"gone@example.com": BOUNCED}


def test_a_permanent_bounce_is_recognised_whatever_the_case(tmp_path):
    log = write(tmp_path / "events.jsonl", [
        event("email.bounced", "gone@example.com", "permanent"),
    ])
    assert load_suppressions(log) == {"gone@example.com": BOUNCED}
