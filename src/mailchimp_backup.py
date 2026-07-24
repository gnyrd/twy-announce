#!/usr/bin/env python3
"""Create a fail-closed, read-only Mailchimp account snapshot.

This is deliberately independent of the withdrawn journey exporter. A run is
complete only when the official account export is valid, every regular and
automation-email campaign has rendered content, every collection is fully
paginated, and the builder-only journey supplements are present.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

import requests
from markdownify import markdownify


KNOWN_STEPS_404 = {2745, 2925, 3491, 4659, 6036}
CAMPAIGN_TYPES_REQUIRING_CONTENT = {"regular", "automation-email"}
REPORT_PAGE_SIZE = 50


class BackupGap(RuntimeError):
    """A condition that makes a snapshot incomplete."""


class MailchimpReadOnlyClient:
    """Small GET-only Mailchimp client with a redacted request audit."""

    def __init__(
        self,
        *,
        server_prefix: str,
        api_key: str,
        timeout: int = 60,
        max_retries: int = 2,
        retry_delay: float = 1.0,
    ) -> None:
        self.base_url = (
            f"https://{server_prefix}.api.mailchimp.com/3.0"
        )
        self.timeout = timeout
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self.session = requests.Session()
        self.session.auth = ("x", api_key)
        self.audit: list[dict[str, Any]] = []

    def get(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        allow_status: tuple[int, ...] = (),
    ) -> dict[str, Any]:
        normalized = "/" + path.lstrip("/")
        response = None
        for attempt in range(1, self.max_retries + 2):
            try:
                response = self.session.get(
                    self.base_url + normalized,
                    params=params,
                    timeout=self.timeout,
                )
            except (requests.ConnectionError, requests.Timeout) as error:
                self.audit.append(
                    {
                        "method": "GET",
                        "path": normalized,
                        "status": None,
                        "error": type(error).__name__,
                        "attempt": attempt,
                    }
                )
                if attempt > self.max_retries:
                    raise
                time.sleep(self.retry_delay * attempt)
                continue
            self.audit.append(
                {
                    "method": "GET",
                    "path": normalized,
                    "status": response.status_code,
                    "attempt": attempt,
                }
            )
            if (
                response.status_code == 429
                or response.status_code >= 500
            ) and attempt <= self.max_retries:
                time.sleep(self.retry_delay * attempt)
                continue
            break
        if response is None:
            raise RuntimeError(f"{normalized} produced no response")
        if response.status_code in allow_status:
            try:
                error_payload = response.json()
            except (requests.JSONDecodeError, ValueError):
                error_payload = None
            return {
                "_backup_http_status": response.status_code,
                "_backup_error": response.reason,
                "_backup_payload": error_payload,
            }
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise BackupGap(f"{normalized} returned non-object JSON")
        return payload


def paginate(
    client: Any,
    path: str,
    collection_key: str,
    *,
    page_size: int = 1000,
    params: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Fetch and validate every page against a stable ``total_items``."""
    if page_size < 1:
        raise ValueError("page_size must be positive")
    items: list[dict[str, Any]] = []
    expected_total: int | None = None
    offset = 0
    while expected_total is None or offset < expected_total:
        query = dict(params or {})
        query.update({"count": page_size, "offset": offset})
        payload = client.get(path, params=query)
        total = payload.get("total_items")
        page = payload.get(collection_key)
        if not isinstance(total, int) or total < 0:
            raise BackupGap(f"{path} has invalid total_items: {total!r}")
        if not isinstance(page, list):
            raise BackupGap(f"{path} missing list {collection_key!r}")
        if expected_total is None:
            expected_total = total
        elif total != expected_total:
            raise BackupGap(
                f"{path} total_items changed from {expected_total} to {total}"
            )
        if not page and offset < expected_total:
            raise BackupGap(f"{path} ended at {offset} of {expected_total}")
        if len(page) < page_size and offset + len(page) < expected_total:
            raise BackupGap(
                f"{path} returned short page at {offset}: "
                f"{len(page)} items with {expected_total} total"
            )
        items.extend(page)
        offset += len(page)
        if expected_total == 0:
            break
    if len(items) != expected_total:
        raise BackupGap(
            f"{path} collected {len(items)} of {expected_total} items"
        )
    ids = [str(item["id"]) for item in items if "id" in item]
    if len(ids) != len(set(ids)):
        raise BackupGap(f"{path} returned duplicate item ids")
    return items


def classify_trigger(
    steps: list[dict[str, Any]] | None,
    tags_by_id: dict[Any, dict[str, Any]],
    *,
    tags_complete: bool,
) -> dict[str, Any]:
    """Represent resolved, deleted, non-tag, and unknown distinctly."""
    if steps is None:
        return {"state": "unknown", "reason": "steps unavailable"}
    if not steps:
        return {"state": "unknown", "reason": "empty steps"}
    first = steps[0]
    settings = first.get("trigger_settings") or {}
    tag_id = settings.get("tag_id")
    if tag_id in (None, ""):
        return {
            "state": "not-a-tag-trigger",
            "step_type": first.get("step_type"),
            "trigger_settings": settings,
        }
    tag = tags_by_id.get(tag_id) or tags_by_id.get(str(tag_id))
    if tag is not None:
        return {
            "state": "resolved",
            "tag_id": tag_id,
            "tag_name": tag.get("name"),
            "member_count": tag.get("member_count"),
        }
    if tags_complete:
        return {"state": "deleted", "tag_id": tag_id}
    return {
        "state": "unknown",
        "tag_id": tag_id,
        "reason": "tag inventory incomplete",
    }


def extract_campaign_renderings(
    campaign_id: str,
    content: dict[str, Any],
) -> tuple[str, str]:
    """Require HTML and derive a readable Markdown rendering."""
    html = content.get("html")
    if not isinstance(html, str) or not html.strip():
        raise BackupGap(f"campaign {campaign_id} missing rendered HTML")
    readable = markdownify(
        html,
        heading_style="ATX",
        bullets="-",
        strip=["style", "script"],
    )
    readable = re.sub(r"\n{3,}", "\n\n", readable).strip() + "\n"
    return html, readable


class SnapshotWriter:
    """Write a new mode-0700 run directory and checksum its artifacts."""

    def __init__(self, run_dir: Path) -> None:
        self.run_dir = Path(run_dir)
        self.run_dir.mkdir(parents=True, mode=0o700)
        os.chmod(self.run_dir, 0o700)

    def _path(self, relative: str) -> Path:
        pure = PurePosixPath(relative)
        if pure.is_absolute() or ".." in pure.parts:
            raise ValueError(f"unsafe artifact path: {relative}")
        path = self.run_dir.joinpath(*pure.parts)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        parent = path.parent
        while parent != self.run_dir:
            os.chmod(parent, 0o700)
            parent = parent.parent
        os.chmod(self.run_dir, 0o700)
        return path

    def write_json(self, relative: str, value: Any) -> Path:
        path = self._path(relative)
        path.write_text(
            json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False)
            + "\n"
        )
        os.chmod(path, 0o600)
        return path

    def write_text(self, relative: str, value: str) -> Path:
        path = self._path(relative)
        path.write_text(value)
        os.chmod(path, 0o600)
        return path

    def file_ledger(self) -> list[dict[str, Any]]:
        ledger = []
        for path in sorted(self.run_dir.rglob("*")):
            if not path.is_file() or path.name == "manifest.json":
                continue
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            ledger.append(
                {
                    "path": str(path.relative_to(self.run_dir)),
                    "bytes": path.stat().st_size,
                    "sha256": digest,
                }
            )
        return ledger


def validate_ui_supplements(
    supplement_dir: Path | None,
    *,
    required_journey_ids: set[int],
    exported_campaign_ids: set[str],
) -> list[str]:
    """Validate builder-order captures for API-inaccessible journeys."""
    gaps: list[str] = []
    if supplement_dir is None:
        return [
            f"journey {journey_id}: builder UI supplement missing"
            for journey_id in sorted(required_journey_ids)
        ]
    supplement_dir = Path(supplement_dir)
    for journey_id in sorted(required_journey_ids):
        path = supplement_dir / f"{journey_id}.json"
        if not path.is_file():
            gaps.append(f"journey {journey_id}: builder UI supplement missing")
            continue
        try:
            payload = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError) as error:
            gaps.append(f"journey {journey_id}: invalid supplement: {error}")
            continue
        if payload.get("journey_id") != journey_id:
            gaps.append(f"journey {journey_id}: supplement id mismatch")
        if payload.get("source") != "mailchimp-builder-ui":
            gaps.append(f"journey {journey_id}: source is not builder UI")
        steps = payload.get("steps")
        if not isinstance(steps, list) or not steps:
            gaps.append(f"journey {journey_id}: ordered steps missing")
        else:
            positions = [step.get("position") for step in steps]
            if positions != list(range(len(steps))):
                gaps.append(
                    f"journey {journey_id}: step positions are not contiguous"
                )
        campaign_ids = payload.get("campaign_ids")
        if not isinstance(campaign_ids, list):
            gaps.append(f"journey {journey_id}: campaign_ids missing")
            continue
        for campaign_id in campaign_ids:
            if str(campaign_id) not in exported_campaign_ids:
                gaps.append(
                    f"journey {journey_id}: campaign {campaign_id} "
                    "has no exported content"
                )
    return gaps


def verify_official_export(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise BackupGap(f"official export missing: {path}")
    with zipfile.ZipFile(path) as archive:
        corrupt = archive.testzip()
        if corrupt is not None:
            raise BackupGap(f"official export corrupt at {corrupt}")
        member_count = len(archive.infolist())
        uncompressed_bytes = sum(info.file_size for info in archive.infolist())
    return {
        "path": str(path),
        "bytes": path.stat().st_size,
        "uncompressed_bytes": uncompressed_bytes,
        "members": member_count,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


def _campaign_content(
    client: MailchimpReadOnlyClient,
    writer: SnapshotWriter,
    campaign: dict[str, Any],
) -> None:
    campaign_id = str(campaign["id"])
    content = client.get(f"/campaigns/{campaign_id}/content")
    html, readable = extract_campaign_renderings(campaign_id, content)
    writer.write_json(f"campaigns/{campaign_id}/content.json", content)
    writer.write_text(f"campaigns/{campaign_id}/rendered.html", html)
    writer.write_text(f"campaigns/{campaign_id}/readable.md", readable)


def capture_snapshot(
    *,
    client: MailchimpReadOnlyClient,
    writer: SnapshotWriter,
    official_export: Path,
    ui_supplements: Path | None,
) -> dict[str, Any]:
    """Capture all API-readable state and return a fail-closed manifest."""
    captured_at = datetime.now(timezone.utc).isoformat()
    gaps: list[str] = []
    official = verify_official_export(official_export)

    lists = paginate(client, "/lists", "lists")
    writer.write_json("lists.json", {"lists": lists, "total_items": len(lists)})

    segments: list[dict[str, Any]] = []
    for audience in lists:
        audience_id = str(audience["id"])
        audience_segments = paginate(
            client,
            f"/lists/{audience_id}/segments",
            "segments",
        )
        writer.write_json(
            f"segments/{audience_id}.json",
            {
                "segments": audience_segments,
                "total_items": len(audience_segments),
            },
        )
        segments.extend(audience_segments)
    tags_by_id: dict[Any, dict[str, Any]] = {}
    for segment in segments:
        segment_id = segment.get("id")
        if segment_id is not None:
            tags_by_id[segment_id] = segment
            tags_by_id[str(segment_id)] = segment

    campaigns = paginate(client, "/campaigns", "campaigns")
    writer.write_json(
        "campaigns.json",
        {"campaigns": campaigns, "total_items": len(campaigns)},
    )
    exported_campaign_ids: set[str] = set()
    content_required = [
        campaign
        for campaign in campaigns
        if campaign.get("type") in CAMPAIGN_TYPES_REQUIRING_CONTENT
    ]
    for campaign in content_required:
        campaign_id = str(campaign.get("id"))
        try:
            _campaign_content(client, writer, campaign)
            exported_campaign_ids.add(campaign_id)
        except Exception as error:
            gaps.append(f"campaign {campaign_id}: content unavailable: {error}")

    templates = paginate(client, "/templates", "templates")
    writer.write_json(
        "templates.json",
        {"templates": templates, "total_items": len(templates)},
    )
    reports = paginate(
        client,
        "/reports",
        "reports",
        page_size=REPORT_PAGE_SIZE,
    )
    writer.write_json(
        "reports.json",
        {"reports": reports, "total_items": len(reports)},
    )
    automations = paginate(client, "/automations", "automations")
    writer.write_json(
        "classic-automations.json",
        {"automations": automations, "total_items": len(automations)},
    )
    journeys = paginate(
        client,
        "/customer-journeys/journeys",
        "journeys",
        page_size=50,
    )
    writer.write_json(
        "journeys.json",
        {"journeys": journeys, "total_items": len(journeys)},
    )

    steps_404: set[int] = set()
    for journey in journeys:
        journey_id = int(journey["id"])
        steps_payload = client.get(
            f"/customer-journeys/journeys/{journey_id}/steps",
            params={"count": 1000},
            allow_status=(404,),
        )
        if steps_payload.get("_backup_http_status") == 404:
            steps_404.add(journey_id)
            writer.write_json(
                f"journeys/{journey_id}/steps-unavailable.json",
                steps_payload,
            )
            trigger = classify_trigger(None, tags_by_id, tags_complete=True)
            writer.write_json(
                f"journeys/{journey_id}/trigger.json",
                trigger,
            )
            continue
        steps = steps_payload.get("steps")
        if not isinstance(steps, list):
            gaps.append(f"journey {journey_id}: steps list missing")
            steps = None
        if steps is not None and isinstance(
            steps_payload.get("total_items"), int
        ):
            if len(steps) != steps_payload["total_items"]:
                gaps.append(
                    f"journey {journey_id}: fetched {len(steps)} of "
                    f"{steps_payload['total_items']} steps"
                )
        writer.write_json(
            f"journeys/{journey_id}/steps.json",
            steps_payload,
        )
        writer.write_json(
            f"journeys/{journey_id}/trigger.json",
            classify_trigger(steps, tags_by_id, tags_complete=True),
        )
        for step in steps or []:
            if step.get("step_type") != "action-send_email":
                continue
            email = (step.get("action_details") or {}).get("email") or {}
            campaign_id = email.get("id")
            if campaign_id and str(campaign_id) not in exported_campaign_ids:
                gaps.append(
                    f"journey {journey_id}: send-step campaign "
                    f"{campaign_id} has no exported content"
                )

    unexpected_404 = steps_404 - KNOWN_STEPS_404
    missing_known_404 = KNOWN_STEPS_404 - steps_404
    for journey_id in sorted(unexpected_404):
        gaps.append(f"journey {journey_id}: unexpected steps 404")
    for journey_id in sorted(missing_known_404):
        gaps.append(
            f"journey {journey_id}: expected steps-404 state changed; "
            "manual review required"
        )

    gaps.extend(
        validate_ui_supplements(
            ui_supplements,
            required_journey_ids=steps_404,
            exported_campaign_ids=exported_campaign_ids,
        )
    )

    regular = sum(c.get("type") == "regular" for c in campaigns)
    automation_email = sum(
        c.get("type") == "automation-email" for c in campaigns
    )
    if len(exported_campaign_ids) != regular + automation_email:
        gaps.append(
            "campaign content ledger mismatch: "
            f"{len(exported_campaign_ids)} exported, "
            f"{regular + automation_email} required"
        )
    methods = sorted({entry["method"] for entry in client.audit})
    if methods != ["GET"]:
        gaps.append(f"HTTP method audit is not GET-only: {methods}")

    manifest = {
        "captured_at": captured_at,
        "complete": not gaps,
        "official_account_export": official,
        "counts": {
            "audiences": len(lists),
            "segments": len(segments),
            "campaigns": len(campaigns),
            "regular_campaigns": regular,
            "automation_email_campaigns": automation_email,
            "campaigns_with_exported_content": len(exported_campaign_ids),
            "templates": len(templates),
            "reports": len(reports),
            "classic_automations": len(automations),
            "customer_journeys": len(journeys),
            "journeys_steps_404": len(steps_404),
        },
        "steps_404_journey_ids": sorted(steps_404),
        "gaps": gaps,
        "http_audit": client.audit,
        "artifact_files": writer.file_ledger(),
    }
    writer.write_json("manifest.json", manifest)
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--official-export", type=Path, required=True)
    parser.add_argument("--ui-supplements", type=Path)
    args = parser.parse_args(argv)

    from twy_paths import load_env

    load_env()
    writer = SnapshotWriter(args.output)
    client = MailchimpReadOnlyClient(
        server_prefix=os.environ["MAILCHIMP_SERVER_PREFIX"],
        api_key=os.environ["MAILCHIMP_API_KEY"],
    )
    try:
        manifest = capture_snapshot(
            client=client,
            writer=writer,
            official_export=args.official_export,
            ui_supplements=args.ui_supplements,
        )
    except Exception as error:
        failure = {
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "complete": False,
            "fatal_error": f"{type(error).__name__}: {error}",
            "http_audit": client.audit,
            "artifact_files": writer.file_ledger(),
        }
        writer.write_json("manifest.json", failure)
        print(
            json.dumps(
                {
                    "complete": False,
                    "fatal_error": failure["fatal_error"],
                    "request_count": len(client.audit),
                    "output": str(args.output),
                },
                indent=2,
            )
        )
        return 3
    print(
        json.dumps(
            {
                "complete": manifest["complete"],
                "counts": manifest["counts"],
                "gaps": manifest["gaps"],
                "output": str(args.output),
            },
            indent=2,
        )
    )
    return 0 if manifest["complete"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
