"""Free class claims reach the newsletter audience, and nothing else moves."""

import sqlite3

import pytest

import sync_sendgrid_free_class_claims as mod


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "marvy.db"
    c = sqlite3.connect(path)
    c.executescript(
        """
        CREATE TABLE customers (id INTEGER PRIMARY KEY, first_name TEXT,
                                last_name TEXT, email TEXT);
        CREATE TABLE purchases (id INTEGER PRIMARY KEY, customer_id INTEGER,
                                customer_email TEXT, product_id INTEGER,
                                product_name TEXT, amount_paid REAL, created TEXT);
        """
    )
    c.executemany(
        "INSERT INTO customers VALUES (?,?,?,?)",
        [
            (1, "Niamh", "Sharkey", "Niamhsharkeyyoga@gmail.com"),
            (2, "Helen", "Yearley", "yearley.helen@gmail.com"),
            (3, "Paying", "Member", "member@example.com"),
            (4, "No", "Email", None),
        ],
    )
    c.executemany(
        "INSERT INTO purchases VALUES (?,?,?,?,?,?,?)",
        [
            (1, 1, None, 95861, "FREE CLASS: Yoga Habit August 2026", 0.0, "2026-08-10T09:35:22Z"),
            (2, 2, None, 95861, "FREE CLASS: Yoga Habit August 2026", 0.0, "2026-08-10T11:01:12Z"),
            (3, 3, None, 87290, "The Archive", 222.0, "2026-08-11T16:31:00Z"),
            (4, 4, None, 53021, "FREE CLASS: 20 min. Balance & Strength", 0.0, "2025-01-01T00:00:00Z"),
        ],
    )
    c.commit()
    c.close()
    return path


def test_only_free_class_purchases_count(db):
    emails = [x["email"] for x in mod.free_class_claimants(db)]
    assert emails == ["niamhsharkeyyoga@gmail.com", "yearley.helen@gmail.com"]
    assert "member@example.com" not in emails


def test_every_free_class_product_counts_not_just_the_habit_ones(db):
    c = sqlite3.connect(db)
    c.execute("UPDATE customers SET email = 'old@example.com' WHERE id = 4")
    c.commit()
    c.close()
    emails = [x["email"] for x in mod.free_class_claimants(db)]
    assert "old@example.com" in emails


def test_emails_are_lowercased_and_names_carried(db):
    claimants = mod.free_class_claimants(db)
    first = claimants[0]
    assert first["email"] == "niamhsharkeyyoga@gmail.com"
    assert first["first_name"] == "Niamh"
    assert first["last_name"] == "Sharkey"


def test_a_row_with_no_usable_email_is_skipped_not_fatal(db):
    assert all(x["email"] for x in mod.free_class_claimants(db))


def test_one_person_claiming_twice_appears_once(db):
    c = sqlite3.connect(db)
    c.execute(
        "INSERT INTO purchases VALUES (5,1,NULL,53021,'FREE CLASS: Ganesha',0.0,'2026-08-12T00:00:00Z')"
    )
    c.commit()
    c.close()
    emails = [x["email"] for x in mod.free_class_claimants(db)]
    assert emails.count("niamhsharkeyyoga@gmail.com") == 1


class FakeAPI:
    def __init__(self, present):
        self.present = [{"email": e} for e in present]
        self.upserted = []
        self.removed = []

    def list_contacts(self, list_id):
        return self.present

    def field_definitions(self):
        return [
            {"id": "e1_T", "name": "twy_source"},
            {"id": "e2_T", "name": "twy_source_detail"},
        ]

    def contacts_by_emails(self, emails):
        known = {str(c["email"]).lower() for c in self.present}
        return {email: {"email": email} for email in emails if email in known}

    def upsert_contacts(self, list_ids, contacts):
        self.upserted.append((list_ids, contacts))
        return "job-1"

    def remove_contacts_from_list(self, *a, **kw):  # pragma: no cover
        self.removed.append((a, kw))
        return "job-x"


def test_only_the_ones_not_already_subscribed_are_added():
    api = FakeAPI(present=["already@example.com"])
    claimants = [{"email": "already@example.com"}, {"email": "new@example.com"}]
    missing = mod.missing_from_list(api, "L1", claimants)
    assert [c["email"] for c in missing] == ["new@example.com"]


def test_membership_check_is_case_insensitive():
    api = FakeAPI(present=["Already@Example.com"])
    missing = mod.missing_from_list(api, "L1", [{"email": "already@example.com"}])
    assert missing == []


def test_nothing_is_ever_removed_from_the_audience():
    # Email: Subscribed is the whole newsletter audience. An exact sync would
    # treat every non-claimant as stale and delete them.
    # Prose explaining why is fine. A call is not.
    text = open((mod.__file__).replace(".pyc", ".py")).read()
    assert "sync_exact_list(" not in text
    assert "remove_contacts_from_list(" not in text


def test_suppressions_are_not_cleared():
    # Claiming is not treated as an opt-in that overrides an earlier opt-out.
    # That call was made for Habit registration and is not extended here.
    text = open((mod.__file__).replace(".pyc", ".py")).read()
    assert "remove_group_suppression" not in text


class MainAPI(FakeAPI):
    def __init__(self, present, suppressed):
        super().__init__(present)
        self.suppressed = suppressed
        self.jobs = []

    def user_email(self):
        return "stub@example.com"

    def search_group_suppressions(self, group_id, emails):
        return [e for e in emails if e in self.suppressed]

    def wait_contact_job(self, job_id, timeout_s=300):
        self.jobs.append(job_id)
        return {}


def _run_main(monkeypatch, api, claimants):
    monkeypatch.setattr(mod, "load_env", lambda: None)
    monkeypatch.setenv("SENDGRID_API_KEY", "key")
    monkeypatch.setattr(mod, "SendGridAPI", lambda key: api)
    monkeypatch.setattr(mod, "EXPECTED_ACCOUNT_EMAIL", "stub@example.com")

    class Registry:
        suppression_group_id = 35187

    monkeypatch.setattr(mod.SendGridRegistry, "load", staticmethod(lambda path: Registry()))
    monkeypatch.setattr(mod, "sendgrid_registry_path", lambda: "unused")
    monkeypatch.setattr(mod, "ensure_list", lambda api, registry, name: "LIST")
    monkeypatch.setattr(mod, "free_class_claimants", lambda: claimants)
    return mod.main()


def test_someone_who_unsubscribed_is_not_added_back(monkeypatch):
    api = MainAPI(present=[], suppressed={"gone@example.com"})
    rc = _run_main(monkeypatch, api, [
        {"email": "gone@example.com"},
        {"email": "keen@example.com"},
    ])
    assert rc == 0
    assert len(api.upserted) == 1
    assert [c["email"] for c in api.upserted[0][1]] == ["keen@example.com"]


def test_when_everyone_missing_has_unsubscribed_no_job_runs(monkeypatch):
    api = MainAPI(present=[], suppressed={"gone@example.com"})
    rc = _run_main(monkeypatch, api, [{"email": "gone@example.com"}])
    assert rc == 0
    assert api.upserted == []
    assert api.jobs == []


def test_a_quiet_day_starts_no_contact_job(monkeypatch):
    api = MainAPI(present=["keen@example.com"], suppressed=set())
    rc = _run_main(monkeypatch, api, [{"email": "keen@example.com"}])
    assert rc == 0
    assert api.upserted == []
