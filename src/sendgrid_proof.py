"""Resumable, allowlisted SendGrid migration proof for TWY."""

from __future__ import annotations

import argparse
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
from typing import Callable

from newsletter_rendering import render_newsletter
from sendgrid_proof_models import (
    CapabilityResult,
    EvidenceStore,
    ProofConfig,
    ProofManifest,
    assert_allowed_recipients,
)


_RECIPIENT_COPY_TRANSLATIONS = str.maketrans({
    0x00A0: " ",
    0x2014: "-",
    0x2019: "'",
})


def normalize_recipient_copy(text: str) -> str:
    return text.translate(_RECIPIENT_COPY_TRANSLATIONS)


def validate_recipient_copy(text: str) -> None:
    if not text.isascii():
        raise ValueError("SendGrid proof recipient copy must be ASCII")
    if ";" in text:
        raise ValueError("SendGrid proof recipient copy must not contain semicolons")


class ProofRunner:
    def __init__(
        self,
        *,
        api,
        config: ProofConfig,
        store: EvidenceStore,
        run_id: str,
        sender_email: str,
        now_fn: Callable[[], datetime] | None = None,
    ):
        self.api = api
        self.config = config
        self.store = store
        self.run_id = run_id
        self.sender_email = sender_email.strip().lower()
        self.now_fn = now_fn or (lambda: datetime.now(timezone.utc))
        manifest_path = self.store.root / "manifest.json"
        self.manifest = (
            self.store.read_manifest()
            if manifest_path.exists()
            else ProofManifest(run_id=run_id)
        )
        if self.manifest.run_id != run_id:
            raise ValueError("manifest run_id does not match requested run")

    def _save(
        self,
        *,
        phase: str,
        object_ids: dict[str, str] | None = None,
        capabilities: dict[str, CapabilityResult] | None = None,
    ) -> ProofManifest:
        ids = dict(self.manifest.object_ids)
        ids.update(object_ids or {})
        results = dict(self.manifest.capabilities)
        results.update(capabilities or {})
        self.manifest = replace(
            self.manifest,
            phase=phase,
            object_ids=ids,
            capabilities=results,
        )
        self.store.write_manifest(self.manifest)
        return self.manifest

    def preflight(self) -> ProofManifest:
        account = self.api.account()
        senders = self.api.snapshot("/verified_senders")
        groups = self.api.snapshot("/asm/groups")
        sender_results = senders.get("results", senders) if isinstance(senders, dict) else senders
        if not sender_results:
            raise RuntimeError("SendGrid has no verified sender")
        if not groups:
            raise RuntimeError("SendGrid has no unsubscribe group")
        sender = next(
            (
                item for item in sender_results
                if item.get("from_email", "").strip().lower() == self.sender_email
            ),
            None,
        )
        if sender is None:
            raise RuntimeError(
                f"SendGrid sender is not verified: {self.sender_email}"
            )
        group = groups[0]
        evidence = {
            "account": account,
            "sender": sender,
            "suppression_group": group,
            "editor_mode": "design",
            "scheduled_delay_seconds": self.config.scheduled_delay_seconds,
            "canonical_env_path": "/root/twy/secrets/.env",
        }
        self.store.write_json("preflight.json", evidence)
        return self._save(
            phase="preflight",
            object_ids={
                "sender_id": str(sender["id"]),
                "suppression_group_id": str(group["id"]),
            },
            capabilities={
                "account_access": CapabilityResult(
                    status="proven",
                    evidence=("preflight.json",),
                    detail="Account, verified sender, and unsubscribe group are readable.",
                ),
                "editor_mode": CapabilityResult(
                    status="proven",
                    evidence=("preflight.json",),
                    detail="Proof payload pins the SendGrid design editor.",
                ),
            },
        )

    def seed(self) -> ProofManifest:
        list_id = self.manifest.object_ids.get("list_id")
        if not list_id:
            created = self.api.create_list(f"{self.config.list_name_prefix} {self.run_id}")
            list_id = created["id"]

        deliverable_contacts = [
            {"email": email}
            for email in sorted(self.config.deliverable_addresses)
        ]
        synthetic_contacts = [
            {"email": email}
            for email in sorted(self.config.synthetic_addresses)
        ]
        deliverable_job = self.api.upsert_contacts([list_id], deliverable_contacts)
        self.api.wait_contact_job(deliverable_job)
        synthetic_job = self.api.upsert_contacts([], synthetic_contacts)
        self.api.wait_contact_job(synthetic_job)

        unsubscribed = "unsubscribed@twy-sendgrid-proof.invalid"
        cleaned = "cleaned@twy-sendgrid-proof.invalid"
        self.api.add_global_unsubscribes([unsubscribed])
        unsubscribed_state = self.api.get_global_unsubscribe(unsubscribed)
        bounce_state = self.api.get_bounce(cleaned)
        members = self.api.list_contacts(list_id)
        assert_allowed_recipients({
            contact["email"] for contact in members
        })
        self.store.write_json("seed.json", {
            "list_id": list_id,
            "deliverable_contacts": deliverable_contacts,
            "synthetic_contacts": synthetic_contacts,
            "deliverable_job": deliverable_job,
            "synthetic_job": synthetic_job,
            "resolved_members": members,
            "global_unsubscribe": unsubscribed_state,
            "cleaned_bounce": bounce_state,
        })
        cleaned_result = (
            CapabilityResult(
                status="proven",
                evidence=("seed.json",),
                detail="Synthetic cleaned contact resolves from SendGrid bounces.",
            )
            if bounce_state
            else CapabilityResult(
                status="unavailable",
                evidence=("seed.json",),
                detail=(
                    "SendGrid exposes no documented API for injecting a synthetic bounce; "
                    "the Mailchimp cleaned mapping remains unseeded."
                ),
            )
        )
        return self._save(
            phase="seed",
            object_ids={"list_id": list_id},
            capabilities={
                "contact_upsert": CapabilityResult(
                    status="proven",
                    evidence=("seed.json",),
                    detail="Deliverable and synthetic contacts completed asynchronous upserts.",
                ),
                "list_targeting": CapabilityResult(
                    status="proven",
                    evidence=("seed.json",),
                    detail="Proof list resolves to exactly the two approved recipients.",
                ),
                "global_unsubscribe": CapabilityResult(
                    status="proven" if unsubscribed_state else "unknown",
                    evidence=("seed.json",),
                    detail="Synthetic global unsubscribe state was written and read back.",
                ),
                "cleaned_mapping": cleaned_result,
            },
        )

    def _single_send_name(self, kind: str) -> str:
        suffix = {"immediate": "Immediate", "scheduled": "Scheduled"}[kind]
        return f"{self.config.list_name_prefix} {self.run_id} {suffix}"

    def send(self, kind: str, body_md: str) -> dict:
        if kind not in {"immediate", "scheduled"}:
            raise ValueError("kind must be immediate or scheduled")
        body_md = normalize_recipient_copy(body_md)
        validate_recipient_copy(body_md)
        list_id = self.manifest.object_ids["list_id"]
        resolved = self.api.list_contacts(list_id)
        assert_allowed_recipients({contact["email"] for contact in resolved})

        name = self._single_send_name(kind)
        existing = self.api.find_single_send_by_name(name)
        if existing:
            single_send = self.api.get_single_send(existing["id"])
        else:
            rendered = render_newsletter(body_md)
            payload = {
                "name": name,
                "send_to": {"list_ids": [list_id], "all": False},
                "email_config": {
                    "subject": (
                        f"TWY SendGrid Migration Proof - {kind.title()}"
                    ),
                    "html_content": rendered.html,
                    "plain_content": rendered.plain_text,
                    "generate_plain_content": False,
                    "editor": "design",
                    "suppression_group_id": int(
                        self.manifest.object_ids["suppression_group_id"]
                    ),
                    "sender_id": int(self.manifest.object_ids["sender_id"]),
                },
            }
            single_send = self.api.create_single_send(payload)
            self.store.write_json(f"single_send_{kind}_created.json", single_send)

        if single_send.get("status") in {"triggered", "scheduled"}:
            return single_send

        resolved_again = self.api.list_contacts(list_id)
        assert_allowed_recipients({
            contact["email"] for contact in resolved_again
        })
        send_at = "now"
        if kind == "scheduled":
            scheduled = self.now_fn() + timedelta(
                seconds=self.config.scheduled_delay_seconds
            )
            send_at = scheduled.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        result = self.api.schedule_single_send(single_send["id"], send_at)
        self.store.write_json(f"single_send_{kind}_scheduled.json", result)
        self._save(
            phase=f"send_{kind}",
            object_ids={f"single_send_{kind}": single_send["id"]},
            capabilities={
                f"{kind}_single_send": CapabilityResult(
                    status="proven",
                    evidence=(f"single_send_{kind}_scheduled.json",),
                    detail=f"{kind.title()} Single Send was accepted by the API.",
                )
            },
        )
        return result

    def collect(self) -> ProofManifest:
        stats = {}
        for key, single_send_id in self.manifest.object_ids.items():
            if not key.startswith("single_send_"):
                continue
            stats[key] = {
                "single_send": self.api.get_single_send(single_send_id),
                "stats": self.api.single_send_stats(
                    single_send_id,
                    self.now_fn().date().isoformat(),
                ),
            }
        stats_materialized = bool(stats) and all(
            item["stats"] is not None for item in stats.values()
        )
        self.store.write_json("stats.json", stats)
        return self._save(
            phase="collect",
            capabilities={
                "single_send_stats": CapabilityResult(
                    status="proven" if stats_materialized else "unknown",
                    evidence=("stats.json",),
                    detail=(
                        "Per-Single-Send definitions and statistics were retrieved."
                        if stats_materialized
                        else (
                            "Single Send definitions were retrieved, but SendGrid "
                            "has not materialized every statistics object yet."
                        )
                    ),
                )
            },
        )

    def export(self) -> ProofManifest:
        list_id = self.manifest.object_ids["list_id"]
        started = self.api.start_contact_export([list_id])
        ready = self.api.wait_contact_export(started["id"])
        self.store.write_json("contact_export.json", {
            "started": started,
            "ready": ready,
        })
        return self._save(
            phase="export",
            object_ids={"contact_export": started["id"]},
            capabilities={
                "contact_export": CapabilityResult(
                    status="proven" if ready.get("status") == "ready" else "unknown",
                    evidence=("contact_export.json",),
                    detail="Contact export job reached ready state.",
                )
            },
        )

    def report(self) -> dict:
        capabilities = {
            name: {
                "status": result.status,
                "evidence": list(result.evidence),
                "detail": result.detail,
            }
            for name, result in sorted(self.manifest.capabilities.items())
        }
        self.store.write_json("capabilities.json", capabilities)
        objects = [
            {"kind": key, "id": value, "delete": "approval required"}
            for key, value in sorted(self.manifest.object_ids.items())
        ]
        objects.append({
            "kind": "api_key",
            "id": "scoped proof key",
            "delete": "revoke in SendGrid UI after explicit approval",
        })
        self.store.write_json("teardown_inventory.json", {"objects": objects})
        self._save(phase="report")
        return capabilities


def run_phase(runner: ProofRunner, phase: str, body_md: str) -> None:
    if phase == "all":
        for current in (
            "preflight",
            "seed",
            "send-immediate",
            "schedule",
            "collect",
            "export",
            "report",
        ):
            run_phase(runner, current, body_md)
        return
    if phase == "preflight":
        runner.preflight()
    elif phase == "seed":
        runner.seed()
    elif phase == "send-immediate":
        runner.send("immediate", body_md)
    elif phase == "schedule":
        runner.send("scheduled", body_md)
    elif phase == "collect":
        runner.collect()
    elif phase == "export":
        runner.export()
    elif phase == "report":
        runner.report()
    else:
        raise ValueError(f"unsupported phase: {phase}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--phase",
        required=True,
        choices=(
            "preflight",
            "seed",
            "send-immediate",
            "schedule",
            "collect",
            "export",
            "report",
            "all",
        ),
    )
    parser.add_argument("--run-id", required=True)
    parser.add_argument(
        "--newsletter",
        default="/root/twy/data/newsletters/2026-07/lifestyle.md",
    )
    args = parser.parse_args()

    from sendgrid_api import SendGridAPI
    from twy_paths import load_env, sendgrid_proof_dir

    load_env()
    api_key = os.getenv("SENDGRID_API_KEY", "")
    sender_email = os.getenv("SENDGRID_FROM_EMAIL", "")
    if not api_key or not sender_email:
        raise RuntimeError(
            "SENDGRID_API_KEY and SENDGRID_FROM_EMAIL must be set in canonical secrets"
        )
    body_md = Path(args.newsletter).read_text()
    runner = ProofRunner(
        api=SendGridAPI(api_key),
        config=ProofConfig(),
        store=EvidenceStore(sendgrid_proof_dir(args.run_id)),
        run_id=args.run_id,
        sender_email=sender_email,
    )
    run_phase(runner, args.phase, body_md)
    print(json.dumps({
        "run_id": args.run_id,
        "phase": runner.manifest.phase,
        "evidence_dir": str(runner.store.root),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
