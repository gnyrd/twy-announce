import importlib
import json
import sqlite3
from datetime import datetime, timezone

import pytest


def product_sync():
    return importlib.import_module("sync_sendgrid_products")


def purchase(module, **overrides):
    values = {
        "purchase_id": "1",
        "customer_id": "10",
        "email": "sub@example.com",
        "product_id": "100",
        "product_name": "Violet Flame Meditation",
        "recurring_type": None,
        "amount_paid": 0,
        "created": "2026-08-03T12:00:00Z",
    }
    values.update(overrides)
    return module.ProductPurchase(**values)


def test_historical_backfill_only_adds_current_subscribers():
    module = product_sync()
    purchases = [
        purchase(module),
        purchase(
            module,
            purchase_id="2",
            customer_id="20",
            email="unsub@example.com",
        ),
    ]

    plan, state = module.plan_historical_backfill(
        purchases,
        subscribed_emails={"sub@example.com"},
    )

    assert plan.contacts_by_product == {
        "100": ({"email": "sub@example.com"},),
    }
    assert plan.subscribe_emails == frozenset()
    assert plan.renewed_consent_emails == frozenset()
    assert state.processed_purchase_ids == frozenset({"1", "2"})
    assert state.acquired_pairs == frozenset({"10:100", "20:100"})
    assert state.product_list_names == {
        "100": "Product: Violet Flame Meditation",
    }


def state(module, **overrides):
    values = {
        "processed_purchase_ids": frozenset(),
        "acquired_pairs": frozenset(),
        "product_list_names": {},
    }
    values.update(overrides)
    return module.ProductSyncState(**values)


def test_new_free_acquisition_renews_consent_and_subscribes():
    module = product_sync()
    plan, next_state = module.plan_incremental_sync(
        [purchase(module)],
        state=state(module),
        subscribed_emails=set(),
        suppressed_emails={"sub@example.com"},
        cleaned_emails=set(),
        bounced_emails=set(),
    )

    assert plan.contacts_by_product == {
        "100": ({"email": "sub@example.com"},),
    }
    assert plan.subscribe_emails == frozenset({"sub@example.com"})
    assert plan.renewed_consent_emails == frozenset({"sub@example.com"})
    assert next_state.processed_purchase_ids == frozenset({"1"})
    assert next_state.acquired_pairs == frozenset({"10:100"})


def test_automatic_recurring_charge_does_not_renew_consent():
    module = product_sync()
    current = state(
        module,
        acquired_pairs=frozenset({"10:100"}),
        product_list_names={"100": "Product: Violet Flame Meditation"},
    )

    plan, next_state = module.plan_incremental_sync(
        [purchase(module, recurring_type="monthly", amount_paid="99.00")],
        state=current,
        subscribed_emails=set(),
        suppressed_emails={"sub@example.com"},
        cleaned_emails=set(),
        bounced_emails=set(),
    )

    assert plan.contacts_by_product == {}
    assert plan.subscribe_emails == frozenset()
    assert plan.renewed_consent_emails == frozenset()
    assert next_state.processed_purchase_ids == frozenset({"1"})


def test_new_paid_nonrecurring_purchase_renews_consent_for_repeat_product():
    module = product_sync()
    current = state(
        module,
        acquired_pairs=frozenset({"10:100"}),
        product_list_names={"100": "Product: Violet Flame Meditation"},
    )

    plan, _ = module.plan_incremental_sync(
        [purchase(module, amount_paid="25.00")],
        state=current,
        subscribed_emails=set(),
        suppressed_emails={"sub@example.com"},
        cleaned_emails=set(),
        bounced_emails=set(),
    )

    assert plan.subscribe_emails == frozenset({"sub@example.com"})
    assert plan.renewed_consent_emails == frozenset({"sub@example.com"})


def test_incremental_sync_is_idempotent_and_keeps_first_product_label():
    module = product_sync()
    current = state(
        module,
        processed_purchase_ids=frozenset({"1"}),
        acquired_pairs=frozenset({"10:100"}),
        product_list_names={"100": "Product: Original Name"},
    )

    plan, next_state = module.plan_incremental_sync(
        [purchase(module, product_name="Renamed Product")],
        state=current,
        subscribed_emails={"sub@example.com"},
        suppressed_emails=set(),
        cleaned_emails=set(),
        bounced_emails=set(),
    )

    assert plan.contacts_by_product == {}
    assert next_state == current


def test_cleaned_or_bounced_address_is_never_made_deliverable():
    module = product_sync()
    purchases = [
        purchase(module),
        purchase(
            module,
            purchase_id="2",
            customer_id="20",
            email="bounce@example.com",
        ),
    ]

    plan, _ = module.plan_incremental_sync(
        purchases,
        state=state(module),
        subscribed_emails=set(),
        suppressed_emails={"sub@example.com", "bounce@example.com"},
        cleaned_emails={"sub@example.com"},
        bounced_emails={"bounce@example.com"},
    )

    assert plan.contacts_by_product == {}
    assert plan.subscribe_emails == frozenset()
    assert plan.renewed_consent_emails == frozenset()
    assert plan.blocked == {
        "bounce@example.com": "bounced",
        "sub@example.com": "cleaned",
    }


class RecordingAPI:
    def __init__(self):
        self.calls = []
        self.contacts = {}
        self.suppressions = {"sub@example.com"}
        self.list_exports = {
            "subscribed-list-id": [
                {"id": "contact-1", "email": "sub@example.com"},
            ]
        }

    def list_contacts(self, list_id):
        self.calls.append(("list_contacts", list_id))
        return self.list_exports.get(list_id, [])

    def create_list(self, name):
        self.calls.append(("create_list", name))
        return {"id": "product-list-id"}

    def remove_group_suppression(self, group_id, email):
        self.calls.append(("remove_group_suppression", group_id, email))
        self.suppressions.discard(email)

    def search_group_suppressions(self, group_id, emails):
        self.calls.append(("search_group_suppressions", group_id, tuple(emails)))
        return set(emails) & self.suppressions

    def upsert_contacts(self, list_ids, contacts):
        self.calls.append(("upsert_contacts", tuple(list_ids), tuple(
            contact["email"] for contact in contacts
        )))
        for contact in contacts:
            self.contacts.setdefault(contact["email"], set()).update(list_ids)
        return f"job-{len(self.calls)}"

    def wait_contact_job(self, job_id, timeout_s=300):
        self.calls.append(("wait_contact_job", job_id, timeout_s))
        return {"status": "completed"}

    def contacts_by_emails(self, emails):
        self.calls.append(("contacts_by_emails", tuple(emails)))
        return {
            email: {"email": email, "list_ids": sorted(self.contacts[email])}
            for email in emails
            if email in self.contacts
        }

    def get_bounce(self, email):
        self.calls.append(("get_bounce", email))
        return None


class RecordingRegistry:
    suppression_group_id = 42

    def __init__(self):
        self.ids = {"Email: Subscribed": "subscribed-list-id"}

    def list_id(self, name):
        if name not in self.ids:
            raise KeyError(name)
        return self.ids[name]

    def register_list(self, name, list_id):
        self.ids[name] = list_id


def test_dry_run_makes_no_provider_or_state_writes(tmp_path):
    module = product_sync()
    api = RecordingAPI()
    registry = RecordingRegistry()
    plan, next_state = module.plan_incremental_sync(
        [purchase(module)],
        state=state(module),
        subscribed_emails=set(),
        suppressed_emails={"sub@example.com"},
        cleaned_emails=set(),
        bounced_emails=set(),
    )

    result = module.apply_product_plan(
        api=api,
        registry=registry,
        plan=plan,
        next_state=next_state,
        state_path=tmp_path / "state.json",
        evidence_path=tmp_path / "evidence.jsonl",
        enrollments_db_path=tmp_path / "enrollments.db",
        dry_run=True,
    )

    assert api.calls == []
    assert not (tmp_path / "state.json").exists()
    assert not (tmp_path / "evidence.jsonl").exists()
    assert result == {
        "products": 1,
        "contacts": 1,
        "subscribed": 1,
        "renewed_consent": 1,
        "blocked": 0,
        "enrolled": 0,
        "dry_run": True,
    }


def test_apply_renews_consent_before_import_and_persists_after_verification(
    tmp_path,
):
    module = product_sync()
    api = RecordingAPI()
    registry = RecordingRegistry()
    plan, next_state = module.plan_incremental_sync(
        [purchase(module)],
        state=state(module),
        subscribed_emails=set(),
        suppressed_emails={"sub@example.com"},
        cleaned_emails=set(),
        bounced_emails=set(),
    )
    state_path = tmp_path / "state.json"
    evidence_path = tmp_path / "evidence.jsonl"

    result = module.apply_product_plan(
        api=api,
        registry=registry,
        plan=plan,
        next_state=next_state,
        state_path=state_path,
        evidence_path=evidence_path,
        enrollments_db_path=tmp_path / "enrollments.db",
        dry_run=False,
    )

    names = [call[0] for call in api.calls]
    assert names.index("remove_group_suppression") < names.index(
        "upsert_contacts"
    )
    assert names.index("wait_contact_job") < names.index(
        "contacts_by_emails"
    )
    assert registry.ids["Product: Violet Flame Meditation"] == (
        "product-list-id"
    )
    upsert = next(call for call in api.calls if call[0] == "upsert_contacts")
    assert set(upsert[1]) == {"subscribed-list-id", "product-list-id"}
    assert upsert[2] == ("sub@example.com",)
    assert state_path.stat().st_mode & 0o777 == 0o600
    assert evidence_path.stat().st_mode & 0o777 == 0o600
    persisted = json.loads(state_path.read_text())
    assert persisted["processed_purchase_ids"] == ["1"]
    evidence = [json.loads(line) for line in evidence_path.read_text().splitlines()]
    assert evidence[0]["renewed_consent_emails"] == ["sub@example.com"]
    assert evidence[0]["state_purchase_count"] == 1
    assert "processed_purchase_ids" not in evidence[0]
    assert result["dry_run"] is False


def test_apply_waits_for_suppression_removal_to_become_visible(
    tmp_path, monkeypatch
):
    module = product_sync()

    class EventuallyConsistentAPI(RecordingAPI):
        def __init__(self):
            super().__init__()
            self.suppression_searches = 0

        def remove_group_suppression(self, group_id, email):
            self.calls.append(("remove_group_suppression", group_id, email))

        def search_group_suppressions(self, group_id, emails):
            self.calls.append(
                ("search_group_suppressions", group_id, tuple(emails))
            )
            self.suppression_searches += 1
            if self.suppression_searches < 3:
                return set(emails)
            return set()

    sleeps = []
    monkeypatch.setattr(module.time, "sleep", sleeps.append)
    api = EventuallyConsistentAPI()
    registry = RecordingRegistry()
    plan, next_state = module.plan_incremental_sync(
        [purchase(module)],
        state=state(module),
        subscribed_emails=set(),
        suppressed_emails={"sub@example.com"},
        cleaned_emails=set(),
        bounced_emails=set(),
    )

    module.apply_product_plan(
        api=api,
        registry=registry,
        plan=plan,
        next_state=next_state,
        state_path=tmp_path / "state.json",
        evidence_path=tmp_path / "evidence.jsonl",
        enrollments_db_path=tmp_path / "enrollments.db",
        dry_run=False,
    )

    assert api.suppression_searches == 3
    assert sleeps == [1, 1]


def test_failed_membership_verification_does_not_persist_state(tmp_path):
    module = product_sync()
    api = RecordingAPI()
    api.contacts_by_emails = lambda emails: {}
    registry = RecordingRegistry()
    plan, next_state = module.plan_incremental_sync(
        [purchase(module)],
        state=state(module),
        subscribed_emails=set(),
        suppressed_emails={"sub@example.com"},
        cleaned_emails=set(),
        bounced_emails=set(),
    )
    state_path = tmp_path / "state.json"

    with pytest.raises(RuntimeError, match="membership verification failed"):
        module.apply_product_plan(
            api=api,
            registry=registry,
            plan=plan,
            next_state=next_state,
            state_path=state_path,
            evidence_path=tmp_path / "evidence.jsonl",
            enrollments_db_path=tmp_path / "enrollments.db",
            dry_run=False,
        )

    assert not state_path.exists()


def test_membership_verification_batches_exact_email_lookups(tmp_path):
    module = product_sync()

    class LimitedLookupAPI(RecordingAPI):
        def contacts_by_emails(self, emails):
            assert len(emails) <= 100
            return super().contacts_by_emails(emails)

    contacts = tuple(
        {"email": f"student{index}@example.com"}
        for index in range(101)
    )
    plan = module.ProductSyncPlan(contacts_by_product={"100": contacts})
    next_state = state(
        module,
        processed_purchase_ids=frozenset({"1"}),
        product_list_names={"100": "Product: Large Audience"},
    )

    module.apply_product_plan(
        api=LimitedLookupAPI(),
        registry=RecordingRegistry(),
        plan=plan,
        next_state=next_state,
        state_path=tmp_path / "state.json",
        evidence_path=tmp_path / "evidence.jsonl",
        enrollments_db_path=tmp_path / "enrollments.db",
        dry_run=False,
    )


def test_historical_loader_reads_purchase_identity_and_product_facts(tmp_path):
    module = product_sync()
    database = tmp_path / "marvy.db"
    connection = sqlite3.connect(database)
    connection.execute(
        """
        CREATE TABLE purchases (
            id INTEGER,
            customer_id INTEGER,
            customer_email TEXT,
            product_id INTEGER,
            product_name TEXT,
            recurring_type TEXT,
            amount_paid REAL,
            created TEXT
        )
        """
    )
    connection.execute(
        "INSERT INTO purchases VALUES (1, 10, ?, 100, ?, NULL, 0, ?)",
        (
            " Student@Example.com ",
            "Violet Flame Meditation",
            "2026-08-03T12:00:00Z",
        ),
    )
    connection.commit()
    connection.close()

    purchases = module.load_historical_purchases(database)

    assert purchases == [
        module.ProductPurchase(
            purchase_id="1",
            customer_id="10",
            email="student@example.com",
            product_id="100",
            product_name="Violet Flame Meditation",
            recurring_type=None,
            amount_paid="0.0",
            created="2026-08-03T12:00:00Z",
        )
    ]


class PageResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class PurchaseClient:
    BASE_URL = "https://example.invalid"

    def __init__(self, pages):
        self.pages = pages
        self.requested_pages = []
        self.session = self

    def _get_auth_headers(self):
        return {"Authorization": "redacted"}

    def get(self, url, params, headers, timeout):
        page = params["page"]
        self.requested_pages.append(page)
        return PageResponse(self.pages[page])


def raw_purchase(identifier, *, customer=10, product=100):
    return {
        "id": identifier,
        "customer": {
            "id": customer,
            "email": f"student{customer}@example.com",
        },
        "product": {
            "id": product,
            "product_name": f"Product {product}",
            "recurring_type": None,
        },
        "amount_paid": "25.00",
        "created": f"2026-08-03T12:00:{identifier:02d}Z",
    }


def test_incremental_reader_stops_after_a_fully_known_page():
    module = product_sync()
    client = PurchaseClient(
        {
            1: {
                "results": [raw_purchase(3), raw_purchase(2)],
                "next": "page2",
            },
            2: {
                "results": [raw_purchase(1)],
                "next": "page3",
            },
        }
    )

    purchases = module.fetch_incremental_purchases(
        client,
        processed_purchase_ids={"1", "2"},
    )

    assert [purchase.purchase_id for purchase in purchases] == ["3"]
    assert client.requested_pages == [1, 2]


def test_state_load_is_empty_when_absent_and_roundtrips_private_file(tmp_path):
    module = product_sync()
    state_path = tmp_path / "state.json"
    assert module.load_state(state_path) == state(module)

    expected = state(
        module,
        processed_purchase_ids=frozenset({"2", "1"}),
        acquired_pairs=frozenset({"20:200", "10:100"}),
        product_list_names={
            "200": "Product: Second",
            "100": "Product: First",
        },
    )
    module.save_state(state_path, expected)

    assert module.load_state(state_path) == expected
    assert state_path.stat().st_mode & 0o777 == 0o600


def test_run_backfill_dry_run_uses_current_subscriber_intersection(tmp_path):
    module = product_sync()
    database = tmp_path / "marvy.db"
    connection = sqlite3.connect(database)
    connection.execute(
        """
        CREATE TABLE purchases (
            id INTEGER,
            customer_id INTEGER,
            customer_email TEXT,
            product_id INTEGER,
            product_name TEXT,
            recurring_type TEXT,
            amount_paid REAL,
            created TEXT
        )
        """
    )
    connection.executemany(
        "INSERT INTO purchases VALUES (?, ?, ?, 100, ?, NULL, 0, ?)",
        [
            (
                1,
                10,
                "sub@example.com",
                "Violet Flame Meditation",
                "2026-08-03T12:00:00Z",
            ),
            (
                2,
                20,
                "unsub@example.com",
                "Violet Flame Meditation",
                "2026-08-03T12:01:00Z",
            ),
        ],
    )
    connection.commit()
    connection.close()
    api = RecordingAPI()

    result = module.run_sync(
        mode="backfill",
        dry_run=True,
        api=api,
        registry=RecordingRegistry(),
        marvelous_client=None,
        database_path=database,
        cleaned_path=tmp_path / "cleaned.json",
        state_path=tmp_path / "state.json",
        evidence_path=tmp_path / "evidence.jsonl",
        journeys_directory=tmp_path / "journeys",
        enrollments_db_path=tmp_path / "enrollments.db",
    )

    assert result["products"] == 1
    assert result["contacts"] == 1
    assert not any(
        call[0] in {"create_list", "upsert_contacts", "remove_group_suppression"}
        for call in api.calls
    )


def test_run_incremental_detects_new_purchase_without_full_scan(tmp_path):
    module = product_sync()
    state_path = tmp_path / "state.json"
    module.save_state(
        state_path,
        state(
            module,
            processed_purchase_ids=frozenset({"1"}),
            acquired_pairs=frozenset({"11:101"}),
            product_list_names={"101": "Product: Prior"},
        ),
    )
    client = PurchaseClient(
        {
            1: {
                "results": [raw_purchase(2), raw_purchase(1, customer=11, product=101)],
                "next": "page2",
            },
            2: {
                "results": [raw_purchase(1, customer=11, product=101)],
                "next": "page3",
            },
        }
    )
    cleaned_path = tmp_path / "cleaned.json"
    cleaned_path.write_text("[]\n")
    api = RecordingAPI()

    result = module.run_sync(
        mode="incremental",
        dry_run=True,
        api=api,
        registry=RecordingRegistry(),
        marvelous_client=client,
        database_path=tmp_path / "unused.db",
        cleaned_path=cleaned_path,
        state_path=state_path,
        evidence_path=tmp_path / "evidence.jsonl",
        journeys_directory=tmp_path / "journeys",
        enrollments_db_path=tmp_path / "enrollments.db",
    )

    assert result["products"] == 1
    assert result["subscribed"] == 1
    assert result["renewed_consent"] == 0
    assert client.requested_pages == [1, 2]
    assert not any(
        call[0] in {"create_list", "upsert_contacts", "remove_group_suppression"}
        for call in api.calls
    )


def test_cli_requires_one_sync_mode_and_accepts_dry_run():
    module = product_sync()
    parser = module.build_parser()

    with pytest.raises(SystemExit):
        parser.parse_args([])
    assert parser.parse_args(["--backfill", "--dry-run"]).backfill is True
    assert parser.parse_args(["--incremental"]).incremental is True
    with pytest.raises(SystemExit):
        parser.parse_args(["--backfill", "--incremental"])


# ---------------------------------------------------------------------------
# Journey enrollment: buying the bound product starts the sequence


JOURNEY_NOW = datetime(2026, 8, 11, 18, 0, tzinfo=timezone.utc)


def welcome_journey(**overrides):
    payload = {
        "version": 1,
        "journey_id": "yoga_lifestyle_welcome_2024_05",
        "label": "Journey: Yoga Lifestyle: 2024_05",
        "marvelous_product_id": 100,
        "active": True,
        "emails": [
            {"subject": "Welcome", "body": "Hi", "interval_days": 0},
            {"subject": "Day two", "body": "More", "interval_days": 1},
        ],
    }
    payload.update(overrides)
    return payload


def journeys_for(journey_payload=None):
    payload = journey_payload or welcome_journey()
    return {int(payload["marvelous_product_id"]): payload}


def membership(module, **overrides):
    """A recurring membership purchase, which is what a journey binds to."""
    values = {
        "recurring_type": "monthly",
        "amount_paid": 99,
        "created": "2026-08-11T17:45:00Z",
    }
    values.update(overrides)
    return purchase(module, **values)


def plan_with_journey(module, purchases, journeys=None, **overrides):
    values = {
        "state": state(module),
        "subscribed_emails": set(),
        "suppressed_emails": set(),
        "cleaned_emails": set(),
        "bounced_emails": set(),
        "journeys_by_product": journeys_for() if journeys is None else journeys,
        "now": JOURNEY_NOW,
    }
    values.update(overrides)
    return module.plan_incremental_sync(purchases, **values)


def test_buying_the_bound_product_starts_its_journey():
    module = product_sync()

    plan, _ = plan_with_journey(module, [membership(module)])

    assert len(plan.enrollments) == 1
    enrollment = plan.enrollments[0]
    assert enrollment.journey_id == "yoga_lifestyle_welcome_2024_05"
    assert enrollment.email == "sub@example.com"
    assert enrollment.customer_id == "10"
    assert enrollment.next_due_at == "2026-08-11T17:45:00+00:00"


def test_the_monthly_charge_that_keeps_a_membership_does_not_re_welcome():
    module = product_sync()
    renewal = membership(module, purchase_id="2", created="2026-09-11T17:45:00Z")

    plan, _ = plan_with_journey(
        module,
        [renewal],
        state=state(
            module,
            processed_purchase_ids=frozenset({"1"}),
            acquired_pairs=frozenset({"10:100"}),
        ),
        now=datetime(2026, 9, 11, 18, 0, tzinfo=timezone.utc),
    )

    assert plan.enrollments == ()
    assert plan.subscribe_emails == frozenset()


def test_a_product_with_no_journey_enrolls_nobody():
    module = product_sync()

    plan, _ = plan_with_journey(module, [membership(module)], journeys={})

    assert plan.enrollments == ()
    assert plan.contacts_by_product


def test_an_inactive_journey_enrolls_nobody():
    module = product_sync()

    plan, _ = plan_with_journey(
        module,
        [membership(module)],
        journeys=journeys_for(welcome_journey(active=False)),
    )

    assert plan.enrollments == ()


def test_a_cleaned_buyer_is_never_enrolled():
    module = product_sync()

    plan, _ = plan_with_journey(
        module,
        [membership(module)],
        cleaned_emails={"sub@example.com"},
    )

    assert plan.enrollments == ()
    assert plan.blocked == {"sub@example.com": "cleaned"}


def test_a_bounced_buyer_is_never_enrolled():
    module = product_sync()

    plan, _ = plan_with_journey(
        module,
        [membership(module)],
        bounced_emails={"sub@example.com"},
    )

    assert plan.enrollments == ()
    assert plan.blocked == {"sub@example.com": "bounced"}


def test_two_buyers_of_the_same_product_each_enroll():
    module = product_sync()
    second = membership(
        module, purchase_id="2", customer_id="11", email="other@example.com"
    )

    plan, _ = plan_with_journey(module, [membership(module), second])

    assert sorted(item.email for item in plan.enrollments) == [
        "other@example.com",
        "sub@example.com",
    ]


def test_the_historical_backfill_never_mails_the_back_catalogue():
    module = product_sync()

    plan, _ = module.plan_historical_backfill(
        [membership(module)],
        subscribed_emails={"sub@example.com"},
    )

    assert plan.enrollments == ()


def test_apply_records_the_enrollment_only_after_the_contact_verifies(tmp_path):
    module = product_sync()
    api = RecordingAPI()
    registry = RecordingRegistry()
    plan, next_state = plan_with_journey(module, [membership(module)])
    db_path = tmp_path / "enrollments.db"

    result = module.apply_product_plan(
        api=api,
        registry=registry,
        plan=plan,
        next_state=next_state,
        state_path=tmp_path / "state.json",
        evidence_path=tmp_path / "evidence.jsonl",
        enrollments_db_path=db_path,
        dry_run=False,
    )

    enrollment = importlib.import_module("journey_enrollment")
    connection = enrollment.connect(db_path)
    try:
        row = enrollment.enrollment_for(
            connection, "yoga_lifestyle_welcome_2024_05", "sub@example.com"
        )
    finally:
        connection.close()

    assert result["enrolled"] == 1
    assert row["purchase_id"] == "1"
    evidence = [
        json.loads(line)
        for line in (tmp_path / "evidence.jsonl").read_text().splitlines()
    ]
    assert evidence[0]["enrolled_pairs"] == [
        "yoga_lifestyle_welcome_2024_05:sub@example.com"
    ]


def test_a_dry_run_enrolls_nobody(tmp_path):
    module = product_sync()
    plan, next_state = plan_with_journey(module, [membership(module)])
    db_path = tmp_path / "enrollments.db"

    result = module.apply_product_plan(
        api=RecordingAPI(),
        registry=RecordingRegistry(),
        plan=plan,
        next_state=next_state,
        state_path=tmp_path / "state.json",
        evidence_path=tmp_path / "evidence.jsonl",
        enrollments_db_path=db_path,
        dry_run=True,
    )

    assert result["enrolled"] == 1
    assert not db_path.exists()


def test_a_failed_verification_leaves_nobody_enrolled(tmp_path):
    module = product_sync()
    api = RecordingAPI()
    api.contacts_by_emails = lambda emails: {}
    plan, next_state = plan_with_journey(module, [membership(module)])
    db_path = tmp_path / "enrollments.db"

    with pytest.raises(RuntimeError, match="membership verification failed"):
        module.apply_product_plan(
            api=api,
            registry=RecordingRegistry(),
            plan=plan,
            next_state=next_state,
            state_path=tmp_path / "state.json",
            evidence_path=tmp_path / "evidence.jsonl",
            enrollments_db_path=db_path,
            dry_run=False,
        )

    assert not db_path.exists()


def test_a_replayed_apply_reports_nobody_newly_enrolled(tmp_path):
    module = product_sync()
    plan, next_state = plan_with_journey(module, [membership(module)])
    db_path = tmp_path / "enrollments.db"
    common = {
        "registry": RecordingRegistry(),
        "plan": plan,
        "next_state": next_state,
        "state_path": tmp_path / "state.json",
        "evidence_path": tmp_path / "evidence.jsonl",
        "enrollments_db_path": db_path,
        "dry_run": False,
    }

    first = module.apply_product_plan(api=RecordingAPI(), **common)
    second = module.apply_product_plan(api=RecordingAPI(), **common)

    assert (first["enrolled"], second["enrolled"]) == (1, 0)
