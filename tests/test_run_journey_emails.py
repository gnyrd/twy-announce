"""run_journey_emails: one email each, once, or nothing at all."""
import importlib
from datetime import datetime, timedelta, timezone

import pytest

NOW = datetime(2026, 8, 20, 15, 0, tzinfo=timezone.utc)
BOUGHT = "2026-08-18T03:30:00+00:00"


def drip():
    return importlib.import_module("run_journey_emails")


def store():
    return importlib.import_module("journey_enrollment")


def journey(**overrides):
    payload = {
        "version": 1,
        "journey_id": "yoga_lifestyle_welcome_2024_05",
        "label": "Journey: Yoga Lifestyle: 2024_05",
        "marvelous_product_id": 52025,
        "active": True,
        "emails": [
            {"subject": "Welcome", "body": "Hello {{first_name}},", "interval_days": 0},
            {"subject": "Day two", "body": "More for you.", "interval_days": 1},
            {"subject": "Day three", "body": "Last one.", "interval_days": 1},
        ],
    }
    payload.update(overrides)
    return payload


def enrolled(connection, *, next_index=0, next_due_at=BOUGHT, email="buyer@example.com"):
    module = store()
    module.record_enrollments(connection, [
        module.PlannedEnrollment(
            journey_id="yoga_lifestyle_welcome_2024_05",
            email=email,
            customer_id="441",
            product_id="52025",
            purchase_id="9001",
            enrolled_at=BOUGHT,
            next_index=0,
            next_due_at=BOUGHT,
        )
    ])
    if next_index or next_due_at != BOUGHT:
        connection.execute(
            "UPDATE enrollments SET next_index = ?, next_due_at = ? WHERE email = ?",
            (next_index, next_due_at, email),
        )
        connection.commit()


class FakeAPI:
    def __init__(
        self,
        *,
        bounced=(),
        suppressed=(),
        blocked=(),
        invalid=(),
        globally_unsubscribed=(),
        fail=False,
    ):
        self.sent = []
        self.bounced = {a.lower() for a in bounced}
        self.suppressed = {a.lower() for a in suppressed}
        self.blocked = {a.lower() for a in blocked}
        self.invalid = {a.lower() for a in invalid}
        self.globally_unsubscribed = {a.lower() for a in globally_unsubscribed}
        self.fail = fail

    def get_bounce(self, email):
        return {"email": email} if email.lower() in self.bounced else None

    def get_block(self, email):
        return {"email": email} if email.lower() in self.blocked else None

    def get_invalid_email(self, email):
        return {"email": email} if email.lower() in self.invalid else None

    def get_global_unsubscribe(self, email):
        if email.lower() in self.globally_unsubscribed:
            return {"email": email}
        return None

    def search_group_suppressions(self, group_id, emails):
        return {e for e in emails if e.lower() in self.suppressed}

    def send_mail(self, payload):
        if self.fail:
            raise RuntimeError("SendGrid said no")
        self.sent.append(payload)


class FakeRegistry:
    sender_email = "tiffany@tiffanywoodyoga.com"
    suppression_group_id = 42


def connection(tmp_path):
    return store().connect(tmp_path / "journey_enrollments.db")


def go(tmp_path, conn, *, api=None, sender=None, journeys=None, now=NOW, **kwargs):
    module = drip()
    # Two roles, filled by one fake unless a test separates them. In
    # production they are two providers: SendGrid stays the consent and
    # deliverability ledger that ineligibility reads, and Resend carries
    # the message, because the SendGrid account has no Email API.
    provider = api or FakeAPI()
    return module.run(
        connection=conn,
        journeys_by_id=(
            {journey()["journey_id"]: journey()} if journeys is None else journeys
        ),
        api=provider,
        sender=sender or provider,
        registry=FakeRegistry(),
        legacy_denylist=kwargs.pop("legacy_denylist", set()),
        marvy_connection=kwargs.pop("marvy_connection", None),
        now=now,
        dry_run=kwargs.pop("dry_run", False),
        linker=kwargs.pop("linker", None),
        announce=kwargs.pop("announce", None),
        **kwargs,
    )


# --- decide: pure, so every branch is cheap to pin ------------------------


def test_an_off_journey_pauses_rather_than_ending_anybody():
    module = drip()

    verdict = module.decide({"next_index": 0}, journey(active=False))

    assert verdict.action == "skip"
    assert verdict.reason == module.JOURNEY_OFF


def test_a_journey_that_no_longer_exists_also_pauses():
    module = drip()

    assert module.decide({"next_index": 0}, None).action == "skip"


def test_running_past_the_last_email_finishes_the_sequence():
    module = drip()

    verdict = module.decide({"next_index": 3}, journey())

    assert (verdict.action, verdict.reason) == ("finish", module.COMPLETED)


def test_an_ineligible_address_finishes_with_its_own_reason():
    module = drip()

    verdict = module.decide({"next_index": 1}, journey(), ineligible=module.BOUNCED)

    assert (verdict.action, verdict.reason) == ("finish", module.BOUNCED)


def test_a_finished_sequence_finishes_as_completed_not_as_a_bounce():
    module = drip()

    verdict = module.decide({"next_index": 3}, journey(), ineligible=module.BOUNCED)

    assert verdict.reason == module.COMPLETED


def test_the_ordinary_case_sends_the_email_at_the_stored_index():
    module = drip()

    verdict = module.decide({"next_index": 1}, journey())

    assert verdict.action == "send"
    assert verdict.email_index == 1
    assert verdict.email["subject"] == "Day two"


# --- next_step: a late tick must not push the sequence later --------------


def test_the_next_email_is_dated_from_enrollment_not_from_now():
    module = drip()
    enrollment = {"next_index": 0, "enrolled_at": BOUGHT}

    index, due = module.next_step(journey(), enrollment)

    assert index == 1
    assert due == "2026-08-19T03:30:00+00:00"


def test_a_tick_that_runs_days_late_does_not_delay_the_rest():
    """Cumulative offsets from enrollment, so lateness never compounds."""
    module = drip()

    index, due = module.next_step(journey(), {"next_index": 1, "enrolled_at": BOUGHT})

    assert (index, due) == (2, "2026-08-20T03:30:00+00:00")


def test_after_the_last_email_there_is_no_next_step():
    module = drip()

    assert module.next_step(journey(), {"next_index": 2, "enrolled_at": BOUGHT}) == (
        None,
        None,
    )


# --- the payload ----------------------------------------------------------


def test_the_payload_carries_the_recipient_subject_and_suppression_group():
    module = drip()

    payload = module.build_payload(
        {"subject": "Welcome", "body": "Hello there,"},
        recipient="buyer@example.com",
        registry=FakeRegistry(),
    )

    assert payload["personalizations"] == [{"to": [{"email": "buyer@example.com"}]}]
    assert payload["subject"] == "Welcome"
    assert payload["asm"]["group_id"] == 42
    assert payload["mail_settings"]["footer"]["enable"] is False
    assert payload["tracking_settings"]["subscription_tracking"]["enable"] is False
    assert {part["type"] for part in payload["content"]} == {
        "text/plain",
        "text/html",
    }


@pytest.mark.parametrize(
    "email", [{"subject": "", "body": "b"}, {"subject": "s", "body": "  "}]
)
def test_an_empty_subject_or_body_is_refused(email):
    module = drip()

    with pytest.raises(ValueError):
        module.build_payload(email, recipient="a@b.com", registry=FakeRegistry())


# --- eligibility, re-checked at send time --------------------------------


def test_the_legacy_mailchimp_denylist_answers_invalid():
    module = drip()

    assert module.ineligibility(
        "Gone@Example.com",
        api=FakeAPI(),
        registry=FakeRegistry(),
        legacy_denylist={"gone@example.com"},
    ) == module.INVALID


def test_a_blocked_address_is_ineligible():
    module = drip()

    assert module.ineligibility(
        "blk@example.com",
        api=FakeAPI(blocked=["blk@example.com"]),
        registry=FakeRegistry(),
        legacy_denylist=set(),
    ) == module.BLOCKED


def test_an_invalid_address_is_ineligible():
    module = drip()

    assert module.ineligibility(
        "nope@example.com",
        api=FakeAPI(invalid=["nope@example.com"]),
        registry=FakeRegistry(),
        legacy_denylist=set(),
    ) == module.INVALID


def test_a_global_unsubscribe_is_ineligible():
    module = drip()

    assert module.ineligibility(
        "g@example.com",
        api=FakeAPI(globally_unsubscribed=["g@example.com"]),
        registry=FakeRegistry(),
        legacy_denylist=set(),
    ) == module.UNSUBSCRIBED


def test_consent_outranks_deliverability_when_both_apply():
    module = drip()

    assert module.ineligibility(
        "both@example.com",
        api=FakeAPI(
            suppressed=["both@example.com"], bounced=["both@example.com"]
        ),
        registry=FakeRegistry(),
        legacy_denylist=set(),
    ) == module.UNSUBSCRIBED


def test_a_bouncing_address_is_ineligible():
    module = drip()

    assert module.ineligibility(
        "b@example.com",
        api=FakeAPI(bounced=["b@example.com"]),
        registry=FakeRegistry(),
        legacy_denylist=set(),
    ) == module.BOUNCED


def test_somebody_who_unsubscribed_is_ineligible():
    module = drip()

    assert module.ineligibility(
        "u@example.com",
        api=FakeAPI(suppressed=["u@example.com"]),
        registry=FakeRegistry(),
        legacy_denylist=set(),
    ) == module.UNSUBSCRIBED


def test_a_deliverable_address_has_no_reason_against_it():
    module = drip()

    assert module.ineligibility(
        "ok@example.com", api=FakeAPI(), registry=FakeRegistry(), legacy_denylist=set()
    ) == ""


# --- run: the whole tick -------------------------------------------------


def test_a_due_email_goes_out_and_the_person_moves_to_the_next_one(tmp_path):
    conn = connection(tmp_path)
    enrolled(conn)
    api = FakeAPI()

    counts = go(tmp_path, conn, api=api)
    row = store().enrollment_for(
        conn, "yoga_lifestyle_welcome_2024_05", "buyer@example.com"
    )

    assert counts["sent"] == 1
    assert len(api.sent) == 1
    assert api.sent[0]["subject"] == "Welcome"
    assert row["next_index"] == 1
    assert row["next_due_at"] == "2026-08-19T03:30:00+00:00"
    assert row["terminal_reason"] is None


def test_an_email_not_yet_due_is_left_alone(tmp_path):
    conn = connection(tmp_path)
    enrolled(conn, next_index=1, next_due_at="2026-09-01T03:30:00+00:00")
    api = FakeAPI()

    counts = go(tmp_path, conn, api=api)

    assert counts["due"] == 0
    assert api.sent == []


def test_the_same_email_never_goes_to_the_same_person_twice(tmp_path):
    conn = connection(tmp_path)
    enrolled(conn)
    api = FakeAPI()

    go(tmp_path, conn, api=api)
    # Rewind them by hand, the way a restored backup or a bad edit would.
    conn.execute("UPDATE enrollments SET next_index = 0, next_due_at = ?", (BOUGHT,))
    conn.commit()
    counts = go(tmp_path, conn, api=api)

    assert len(api.sent) == 1
    assert counts["reasons"]["already_claimed"] == 1


def test_a_provider_failure_confirms_nothing_and_raises(tmp_path):
    conn = connection(tmp_path)
    enrolled(conn)

    with pytest.raises(RuntimeError, match="could not be confirmed"):
        go(tmp_path, conn, api=FakeAPI(fail=True))

    module = store()
    row = module.enrollment_for(
        conn, "yoga_lifestyle_welcome_2024_05", "buyer@example.com"
    )
    unresolved = module.unresolved_sends(conn)

    assert row["next_index"] == 0, "a failed send must not advance anybody"
    assert len(unresolved) == 1
    assert unresolved[0]["sent_at"] is None


def test_an_unconfirmed_send_is_never_retried_by_itself(tmp_path):
    conn = connection(tmp_path)
    enrolled(conn)
    with pytest.raises(RuntimeError):
        go(tmp_path, conn, api=FakeAPI(fail=True))

    api = FakeAPI()
    counts = go(tmp_path, conn, api=api)

    assert api.sent == []
    assert counts["reasons"]["already_claimed"] == 1


def test_one_bad_recipient_does_not_stop_the_others(tmp_path):
    conn = connection(tmp_path)
    enrolled(conn, email="first@example.com")
    enrolled(conn, email="second@example.com")

    class FailsOnce(FakeAPI):
        def send_mail(self, payload):
            to = payload["personalizations"][0]["to"][0]["email"]
            if to == "first@example.com":
                raise RuntimeError("SendGrid said no")
            self.sent.append(payload)

    api = FailsOnce()
    with pytest.raises(RuntimeError):
        go(tmp_path, conn, api=api)

    assert [p["personalizations"][0]["to"][0]["email"] for p in api.sent] == [
        "second@example.com"
    ]


def test_a_dry_run_sends_nothing_and_records_nothing(tmp_path):
    conn = connection(tmp_path)
    enrolled(conn)
    api = FakeAPI()

    counts = go(tmp_path, conn, api=api, dry_run=True)
    row = store().enrollment_for(
        conn, "yoga_lifestyle_welcome_2024_05", "buyer@example.com"
    )

    assert counts["sent"] == 1
    assert api.sent == []
    assert row["next_index"] == 0
    assert store().unresolved_sends(conn) == []


def test_turning_a_journey_off_pauses_everybody_without_ending_them(tmp_path):
    conn = connection(tmp_path)
    enrolled(conn)
    api = FakeAPI()

    counts = go(tmp_path, conn, api=api, journeys={})
    row = store().enrollment_for(
        conn, "yoga_lifestyle_welcome_2024_05", "buyer@example.com"
    )

    assert api.sent == []
    assert counts["skipped"] == 1
    assert row["terminal_reason"] is None, "an off journey must be reversible"
    assert row["next_due_at"] == BOUGHT


def test_somebody_who_unsubscribed_mid_sequence_stops_receiving_it(tmp_path):
    conn = connection(tmp_path)
    enrolled(conn)
    api = FakeAPI(suppressed=["buyer@example.com"])

    counts = go(tmp_path, conn, api=api)
    row = store().enrollment_for(
        conn, "yoga_lifestyle_welcome_2024_05", "buyer@example.com"
    )

    assert api.sent == []
    assert counts["finished"] == 1
    assert row["terminal_reason"] == "unsubscribed"
    assert row["next_due_at"] is None


def test_the_last_email_completes_the_sequence(tmp_path):
    conn = connection(tmp_path)
    enrolled(conn, next_index=2, next_due_at=BOUGHT)
    api = FakeAPI()

    go(tmp_path, conn, api=api)
    row = store().enrollment_for(
        conn, "yoga_lifestyle_welcome_2024_05", "buyer@example.com"
    )

    assert api.sent[0]["subject"] == "Day three"
    assert row["terminal_reason"] == "completed"
    assert row["next_due_at"] is None


def test_the_buyer_is_greeted_by_their_own_name(tmp_path):
    import sqlite3

    conn = connection(tmp_path)
    enrolled(conn)
    marvy = sqlite3.connect(":memory:")
    marvy.row_factory = sqlite3.Row
    marvy.execute("CREATE TABLE customers (id INTEGER, first_name TEXT, email TEXT)")
    marvy.execute(
        "INSERT INTO customers VALUES (441, 'Sarah', 'buyer@example.com')"
    )
    marvy.commit()
    api = FakeAPI()

    go(tmp_path, conn, api=api, marvy_connection=marvy)

    html = next(
        part["value"] for part in api.sent[0]["content"] if part["type"] == "text/html"
    )
    assert "Hello Sarah," in html


def test_a_buyer_with_no_record_still_gets_a_readable_greeting(tmp_path):
    conn = connection(tmp_path)
    enrolled(conn)
    api = FakeAPI()

    go(tmp_path, conn, api=api, marvy_connection=None)

    html = next(
        part["value"] for part in api.sent[0]["content"] if part["type"] == "text/html"
    )
    assert "Hello there," in html


def test_an_unfillable_token_stops_the_send_rather_than_shipping(tmp_path):
    from journey_personalization import UnknownToken

    conn = connection(tmp_path)
    enrolled(conn)
    broken = journey()
    broken["emails"][0]["body"] = "Hello {{frist_name}},"
    api = FakeAPI()

    with pytest.raises(UnknownToken):
        go(tmp_path, conn, api=api, journeys={broken["journey_id"]: broken})

    assert api.sent == []
    assert store().unresolved_sends(conn) == []


def test_the_limit_caps_how_many_go_out_in_one_tick(tmp_path):
    conn = connection(tmp_path)
    enrolled(conn, email="a@example.com")
    enrolled(conn, email="b@example.com")
    api = FakeAPI()

    counts = go(tmp_path, conn, api=api, limit=1)

    assert counts["due"] == 1
    assert len(api.sent) == 1


def test_the_ledger_records_what_went_to_whom(tmp_path):
    conn = connection(tmp_path)
    enrolled(conn)

    go(tmp_path, conn)
    rows = store().sends_for(conn, "yoga_lifestyle_welcome_2024_05")

    assert len(rows) == 1
    assert rows[0]["status"] == "sent"
    assert rows[0]["subject"] == "Welcome"
    assert rows[0]["email_index"] == 0
    assert rows[0]["sent_at"] is not None


# --- name lookup ---------------------------------------------------------


def _marvy():
    import sqlite3

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE customers (id INTEGER, first_name TEXT, email TEXT)")
    conn.execute("INSERT INTO customers VALUES (441, 'Sarah', 'buyer@example.com')")
    conn.execute("INSERT INTO customers VALUES (999, 'Dee', 'other@example.com')")
    conn.commit()
    return conn


def test_the_name_comes_from_the_customer_id_first():
    module = drip()

    assert module.first_name_for(
        _marvy(), {"customer_id": "441", "email": "wrong@example.com"}
    ) == "Sarah"


def test_the_address_is_the_fallback_when_the_id_is_unknown():
    module = drip()

    assert module.first_name_for(
        _marvy(), {"customer_id": "0", "email": "Other@Example.com"}
    ) == "Dee"


def test_an_unknown_buyer_yields_no_name_rather_than_an_error():
    module = drip()

    assert module.first_name_for(
        _marvy(), {"customer_id": "0", "email": "nobody@example.com"}
    ) == ""


def test_no_customer_database_yields_no_name():
    module = drip()

    assert module.first_name_for(None, {"customer_id": "441"}) == ""


# ---------------------------------------------------------------------------
# Attribution and the Slack line


def test_the_send_carries_the_journey_identity(tmp_path):
    """Without this echo, an open three weeks later belongs to nothing."""
    conn = connection(tmp_path)
    enrolled(conn)
    api = FakeAPI()

    go(tmp_path, conn, api=api)

    assert api.sent[0]["custom_args"] == {
        "twy_campaign_id": "journey:yoga_lifestyle_welcome_2024_05:0"
    }


def test_each_email_in_the_sequence_gets_its_own_identity(tmp_path):
    conn = connection(tmp_path)
    enrolled(conn, next_index=1, next_due_at=BOUGHT)
    api = FakeAPI()

    go(tmp_path, conn, api=api)

    assert api.sent[0]["custom_args"]["twy_campaign_id"].endswith(":1")


def test_a_payload_built_without_an_identity_carries_no_custom_args():
    """The editor Send test path has no journey, so it must not invent one."""
    module = drip()

    payload = module.build_payload(
        {"subject": "s", "body": "b"},
        recipient="a@b.com",
        registry=FakeRegistry(),
    )

    assert "custom_args" not in payload


def test_the_slack_line_reads_as_a_sentence():
    module = drip()

    assert module.sent_announcement(
        journey(), email_index=1, recipient="jane@example.com"
    ) == "Yoga Lifestyle: 2024_05 sent 2 of 3 to jane@example.com"


def test_the_slack_line_counts_from_one_not_zero():
    module = drip()

    line = module.sent_announcement(
        journey(), email_index=0, recipient="jane@example.com"
    )

    assert "sent 1 of 3" in line


def test_the_recipient_is_linked_to_their_marvelous_record():
    module = drip()

    def linker(email):
        return f"<https://app.heymarvelous.com/customers/441|Jane>"

    line = module.sent_announcement(
        journey(), email_index=1, recipient="jane@example.com", linker=linker
    )

    assert line == (
        "Yoga Lifestyle: 2024_05 sent 2 of 3 to "
        "<https://app.heymarvelous.com/customers/441|Jane>"
    )


def test_a_real_send_announces_itself(tmp_path):
    conn = connection(tmp_path)
    enrolled(conn)
    posted = []

    go(tmp_path, conn, announce=posted.append, linker=lambda e: "Jane")

    assert posted == ["Yoga Lifestyle: 2024_05 sent 1 of 3 to Jane"]


def test_a_dry_run_announces_nothing(tmp_path):
    conn = connection(tmp_path)
    enrolled(conn)
    posted = []

    go(tmp_path, conn, dry_run=True, announce=posted.append)

    assert posted == []


def test_a_finish_is_not_announced_as_a_send(tmp_path):
    conn = connection(tmp_path)
    enrolled(conn)
    posted = []

    go(
        tmp_path,
        conn,
        api=FakeAPI(suppressed=["buyer@example.com"]),
        announce=posted.append,
    )

    assert posted == []


def test_a_failed_slack_post_does_not_undo_a_real_send(tmp_path):
    """The email left. Losing the announcement must not rewind the member."""
    conn = connection(tmp_path)
    enrolled(conn)
    api = FakeAPI()

    def broken(_message):
        raise RuntimeError("Slack delivery was not confirmed")

    with pytest.raises(RuntimeError, match="could not be confirmed"):
        go(tmp_path, conn, api=api, announce=broken)

    row = store().enrollment_for(
        conn, "yoga_lifestyle_welcome_2024_05", "buyer@example.com"
    )
    ledger = store().sends_for(conn, "yoga_lifestyle_welcome_2024_05")

    assert len(api.sent) == 1, "the email still went out"
    assert ledger[0]["status"] == "sent"
    assert row["next_index"] == 1, "and the member still advanced"


def test_a_resend_hard_bounce_stops_the_next_email(tmp_path):
    """The gap the 2026-08-14 cutover opened, closed.

    The send leaves through Resend, the ledger stays on SendGrid, so a hard
    bounce happens somewhere SendGrid cannot see. Before this, a member whose
    address bounced on email 1 was sent email 2 regardless.
    """
    conn = connection(tmp_path)
    enrolled(conn)
    api = FakeAPI()

    counts = go(
        tmp_path,
        conn,
        api=api,
        resend_suppressions={"buyer@example.com": "bounced"},
    )
    row = store().enrollment_for(
        conn, "yoga_lifestyle_welcome_2024_05", "buyer@example.com"
    )

    assert api.sent == []
    assert counts["finished"] == 1
    assert row["terminal_reason"] == "bounced"


def test_a_resend_complaint_reads_as_unsubscribed(tmp_path):
    """Pressing spam is a decision about wanting the mail, not a bad mailbox."""
    conn = connection(tmp_path)
    enrolled(conn)
    api = FakeAPI()

    go(
        tmp_path,
        conn,
        api=api,
        resend_suppressions={"buyer@example.com": "complained"},
    )
    row = store().enrollment_for(
        conn, "yoga_lifestyle_welcome_2024_05", "buyer@example.com"
    )

    assert api.sent == []
    assert row["terminal_reason"] == "unsubscribed"


def test_an_unrelated_resend_suppression_does_not_stop_this_member(tmp_path):
    conn = connection(tmp_path)
    enrolled(conn)
    api = FakeAPI()

    counts = go(
        tmp_path,
        conn,
        api=api,
        resend_suppressions={"somebody-else@example.com": "bounced"},
    )

    assert counts["sent"] == 1
    assert len(api.sent) == 1


def test_no_resend_suppressions_at_all_still_sends(tmp_path):
    """A missing event log fails open, so the sequence must not stall."""
    conn = connection(tmp_path)
    enrolled(conn)
    api = FakeAPI()

    counts = go(tmp_path, conn, api=api, resend_suppressions={})

    assert counts["sent"] == 1
