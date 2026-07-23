from datetime import datetime, timezone

import pytest

from sendgrid_proof import (
    ProofRunner,
    normalize_recipient_copy,
    run_phase,
    validate_recipient_copy,
)
from sendgrid_proof_models import EvidenceStore, ProofConfig, RecipientSafetyError


NOW = datetime(2026, 7, 23, 18, 0, 0, tzinfo=timezone.utc)
BODY = """A proof newsletter.

[Visit TWY](https://habit.tiffanywoodyoga.com/?utm_content=proof)
"""


class FakeAPI:
    def __init__(self):
        self.created_lists = []
        self.upserts = []
        self.global_unsubscribes = []
        self.single_sends = {}
        self.schedules = []
        self.contact_export_requests = []
        self.list_members = [
            {"email": "admin@tiffanywoodyoga.com"},
            {"email": "jpgan6@gmail.com"},
        ]

    def account(self):
        return {"type": "trial"}

    def snapshot(self, endpoint):
        if endpoint == "/verified_senders":
            return {"results": [
                {"id": 6, "from_email": "other@example.com"},
                {"id": 7, "from_email": "tiffany@tiffanywoodyoga.com"},
            ]}
        if endpoint == "/asm/groups":
            return [{"id": 42, "name": "TWY Proof"}]
        if endpoint.startswith("/suppression/unsubscribes"):
            return [{
                "email": "unsubscribed@twy-sendgrid-proof.invalid",
                "created": 1,
            }]
        if endpoint.startswith("/suppression/"):
            return []
        return {}

    def create_list(self, name):
        self.created_lists.append(name)
        return {"id": "list-1", "name": name}

    def upsert_contacts(self, list_ids, contacts):
        self.upserts.append((list_ids, contacts))
        return f"job-{len(self.upserts)}"

    def wait_contact_job(self, job_id, timeout_s=120):
        return {"job_id": job_id, "status": "completed"}

    def add_global_unsubscribes(self, emails):
        self.global_unsubscribes.extend(emails)

    def get_global_unsubscribe(self, email):
        return {"recipient_email": email}

    def get_bounce(self, email):
        return None

    def list_contacts(self, list_id):
        return self.list_members

    def find_single_send_by_name(self, name):
        return self.single_sends.get(name)

    def create_single_send(self, payload):
        result = {"id": f"ss-{len(self.single_sends) + 1}", "status": "draft", **payload}
        self.single_sends[payload["name"]] = result
        return result

    def get_single_send(self, single_send_id):
        for item in self.single_sends.values():
            if item["id"] == single_send_id:
                return item
        raise KeyError(single_send_id)

    def schedule_single_send(self, single_send_id, send_at):
        self.schedules.append((single_send_id, send_at))
        for item in self.single_sends.values():
            if item["id"] == single_send_id:
                item["status"] = "triggered" if send_at == "now" else "scheduled"
                item["send_at"] = send_at
                return item
        raise KeyError(single_send_id)

    def single_send_stats(self, single_send_id, start_date):
        return {"results": [{"id": single_send_id, "stats": {"requests": 2}}]}

    def start_contact_export(self, list_ids):
        self.contact_export_requests.append(list_ids)
        return {"id": f"export-{len(self.contact_export_requests)}"}

    def wait_contact_export(self, export_id, timeout_s=180):
        return {
            "id": export_id,
            "status": "ready",
            "urls": [f"https://signed/{export_id}.csv?token=x"],
        }

    def download_contact_export(self, url):
        if "export-2" in url:
            return (
                b"email\n"
                b"admin@tiffanywoodyoga.com\n"
                b"jpgan6@gmail.com\n"
                b"cleaned@twy-sendgrid-proof.invalid\n"
                b"subscribed@twy-sendgrid-proof.invalid\n"
                b"unsubscribed@twy-sendgrid-proof.invalid\n"
            )
        return (
            b"email\n"
            b"admin@tiffanywoodyoga.com\n"
            b"jpgan6@gmail.com\n"
        )


def make_runner(tmp_path, api=None):
    return ProofRunner(
        api=api or FakeAPI(),
        config=ProofConfig(),
        store=EvidenceStore(tmp_path),
        run_id="20260723T180000Z",
        sender_email="tiffany@tiffanywoodyoga.com",
        now_fn=lambda: NOW,
    )


def test_preflight_records_sender_group_and_account(tmp_path):
    runner = make_runner(tmp_path)
    manifest = runner.preflight()
    assert manifest.object_ids["sender_id"] == "7"
    assert manifest.object_ids["suppression_group_id"] == "42"
    assert manifest.capabilities["account_access"].status == "proven"
    assert (tmp_path / "preflight.json").exists()


def test_recipient_copy_validation_rejects_non_ascii_and_semicolons():
    with pytest.raises(ValueError):
        validate_recipient_copy("not ascii \u2014")
    with pytest.raises(ValueError):
        validate_recipient_copy("not allowed;")
    validate_recipient_copy(BODY)


def test_recipient_copy_normalizes_typographic_punctuation():
    source = "Tiffany\u2019s\u00a0newsletter \u2014 proof"
    normalized = normalize_recipient_copy(source)
    assert normalized == "Tiffany's newsletter - proof"
    validate_recipient_copy(normalized)


def test_seed_keeps_synthetic_contacts_out_of_deliverable_list(tmp_path):
    api = FakeAPI()
    manifest = make_runner(tmp_path, api).seed()
    assert manifest.object_ids["list_id"] == "list-1"
    assert api.upserts[0][0] == ["list-1"]
    assert {contact["email"] for contact in api.upserts[0][1]} == {
        "admin@tiffanywoodyoga.com",
        "jpgan6@gmail.com",
    }
    assert api.upserts[0][1] == [
        {"email": "admin@tiffanywoodyoga.com"},
        {"email": "jpgan6@gmail.com"},
    ]
    assert api.upserts[1][0] == []
    assert all(contact["email"].endswith(".invalid") for contact in api.upserts[1][1])
    assert api.upserts[1][1] == [
        {"email": "cleaned@twy-sendgrid-proof.invalid"},
        {"email": "subscribed@twy-sendgrid-proof.invalid"},
        {"email": "unsubscribed@twy-sendgrid-proof.invalid"},
    ]
    assert api.global_unsubscribes == ["unsubscribed@twy-sendgrid-proof.invalid"]
    assert manifest.capabilities["cleaned_mapping"].status == "unavailable"


def test_immediate_send_rechecks_live_list_membership(tmp_path):
    api = FakeAPI()
    runner = make_runner(tmp_path, api)
    runner.preflight()
    runner.seed()
    sent = runner.send("immediate", BODY)
    assert sent["status"] == "triggered"
    assert api.schedules == [("ss-1", "now")]
    config = sent["email_config"]
    assert config["editor"] == "design"
    assert config["generate_plain_content"] is False
    assert config["sender_id"] == 7
    assert config["suppression_group_id"] == 42
    assert config["subject"] == "TWY SendGrid Migration Proof - Immediate"


def test_send_aborts_if_live_membership_drifted(tmp_path):
    api = FakeAPI()
    runner = make_runner(tmp_path, api)
    runner.preflight()
    runner.seed()
    api.list_members.append({"email": "third@example.com"})
    with pytest.raises(RecipientSafetyError):
        runner.send("immediate", BODY)
    assert api.schedules == []


def test_scheduled_send_is_exactly_600_seconds_in_future(tmp_path):
    api = FakeAPI()
    runner = make_runner(tmp_path, api)
    runner.preflight()
    runner.seed()
    sent = runner.send("scheduled", BODY)
    assert api.schedules == [("ss-1", "2026-07-23T18:10:00Z")]
    assert sent["email_config"]["subject"] == "TWY SendGrid Migration Proof - Scheduled"


def test_existing_deterministic_send_is_reused_after_crash(tmp_path):
    api = FakeAPI()
    runner = make_runner(tmp_path, api)
    runner.preflight()
    runner.seed()
    name = "TWY SendGrid Migration Proof 20260723T180000Z Immediate"
    api.single_sends[name] = {
        "id": "existing-1",
        "name": name,
        "status": "draft",
        "email_config": {},
    }
    runner.send("immediate", BODY)
    assert len(api.single_sends) == 1
    assert api.schedules == [("existing-1", "now")]


def test_triggered_send_is_never_sent_again(tmp_path):
    api = FakeAPI()
    runner = make_runner(tmp_path, api)
    runner.preflight()
    runner.seed()
    name = "TWY SendGrid Migration Proof 20260723T180000Z Immediate"
    api.single_sends[name] = {
        "id": "existing-1",
        "name": name,
        "status": "triggered",
        "email_config": {},
    }
    result = runner.send("immediate", BODY)
    assert result["id"] == "existing-1"
    assert api.schedules == []


def test_collect_export_and_report_cover_created_state(tmp_path):
    runner = make_runner(tmp_path)
    runner.preflight()
    runner.seed()
    runner.send("immediate", BODY)
    runner.collect()
    runner.export()
    report = runner.report()
    assert "cleaned_mapping" in report
    assert (tmp_path / "stats.json").exists()
    assert (tmp_path / "suppressions.json").exists()
    suppressions = (tmp_path / "suppressions.json").read_text()
    assert "unsubscribed@twy-sendgrid-proof.invalid" in suppressions
    assert (tmp_path / "contact_export.json").exists()
    assert (
        tmp_path / "contacts" / "deliverable" / "contact_export_01.csv"
    ).exists()
    assert (
        tmp_path / "contacts" / "all_contacts" / "contact_export_01.csv"
    ).exists()
    assert runner.api.contact_export_requests == [["list-1"], None]
    assert runner.manifest.object_ids["contact_export"] == "export-1"
    assert runner.manifest.object_ids["contact_export_all"] == "export-2"
    export_evidence = (tmp_path / "contact_export.json").read_text()
    assert "sha256" in export_evidence
    assert "X-Amz-Signature" not in export_evidence
    teardown = (tmp_path / "teardown_inventory.json").read_text()
    assert "list-1" in teardown
    assert "ss-1" in teardown
    assert "api_key" in teardown
    assert "admin@tiffanywoodyoga.com" in teardown
    assert "unsubscribed@twy-sendgrid-proof.invalid" in teardown
    assert "DELETE /v3/marketing/lists/list-1" in teardown
    assert "DELETE /v3/marketing/singlesends/ss-1" in teardown
    markdown_report = (tmp_path / "report.md").read_text()
    assert markdown_report.startswith("# TWY SendGrid Migration Proof")
    assert "| Capability | Status | Evidence | Detail |" in markdown_report
    assert "| cleaned_mapping | unavailable | seed.json |" in markdown_report
    assert "## Created-object inventory" in markdown_report


def test_collect_records_stats_materialization_latency_as_unknown(tmp_path):
    api = FakeAPI()
    runner = make_runner(tmp_path, api)
    runner.preflight()
    runner.seed()
    runner.send("immediate", BODY)
    api.single_send_stats = lambda single_send_id, start_date: None
    manifest = runner.collect()
    assert manifest.capabilities["single_send_stats"].status == "unknown"
    assert '"stats": null' in (tmp_path / "stats.json").read_text()


def test_all_phase_runs_in_safe_order():
    calls = []

    class TrackingRunner:
        def preflight(self):
            calls.append("preflight")

        def seed(self):
            calls.append("seed")

        def send(self, kind, body):
            calls.append((kind, body))

        def collect(self):
            calls.append("collect")

        def export(self):
            calls.append("export")

        def report(self):
            calls.append("report")

    run_phase(TrackingRunner(), "all", BODY)
    assert calls == [
        "preflight",
        "seed",
        ("immediate", BODY),
        ("scheduled", BODY),
        "collect",
        "export",
        "report",
    ]
