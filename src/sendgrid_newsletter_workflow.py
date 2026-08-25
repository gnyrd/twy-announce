"""Provision the seven TWY monthly SendGrid drafts and audiences."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re

from sendgrid_campaigns import SendGridCampaigns
from sendgrid_mailings import (
    EMAIL_SUBSCRIBED,
    MEMBER_YOGA_LIFESTYLE,
    PURPOSE_SECTIONS,
    SECTION_PURPOSES,
    MailingPurpose,
    general_invitation_query,
    habit_activity_name,
    interested_nonmember_query,
    mailing_name,
    non_opener_query,
    opener_not_registered_query,
    mailing_schedule,
)
from newsletter_editorial_review import (
    build_review_record,
    ensure_empty_review_is_done,
    validate_comparison,
)
from twy_paths import (
    habit_recording_state_path,
    newsletter_diffs_dir,
    newsletter_metadata_path,
    newsletter_path,
    newsletters_dir,
)
from twy_platform.text import find_prohibited
from twy_platform import locked_create, locked_write


MATERIALIZATION_WINDOW = timedelta(hours=24)
UNRESOLVED_TOKEN = re.compile(r"\{[A-Z][A-Z0-9_]*\}")


def _split_newsletter_markdown(text: str) -> tuple[str, str]:
    if not text.startswith("#"):
        return "", text.strip()
    first_line, _, rest = text.partition("\n")
    subject = first_line.lstrip("#").strip()
    body = rest[1:] if rest.startswith("\n") else rest
    return subject, body.strip()


def _load_metadata(year: int, month: int) -> dict:
    path = newsletter_metadata_path(year, month)
    if not path.exists():
        return {"version": 1, "drafts": {}}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("version") != 1:
        raise ValueError("unsupported newsletter metadata version")
    payload.setdefault("drafts", {})
    return payload


def _save_metadata(year: int, month: int, payload: dict) -> None:
    locked_write(
        newsletter_metadata_path(year, month),
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
    )


def _period_dir(year: int, month: int) -> Path:
    return newsletters_dir() / f"{year:04d}-{month:02d}"


def _snapshot_content(snapshot: dict) -> dict:
    content = snapshot.get("content") or {}
    section = {
        "subject": str(content.get("subject") or ""),
        "preheader": str(content.get("preheader") or ""),
        "body": str(content.get("body") or ""),
    }
    _validate_sections_before_provider({"snapshot": section})
    return section


def _read_snapshot(
    year: int,
    month: int,
    relative_path: str,
) -> dict:
    path = _period_dir(year, month) / relative_path
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("version") != 1:
        raise ValueError("unsupported newsletter snapshot version")
    return payload


def _write_snapshot(
    *,
    year: int,
    month: int,
    key: str,
    kind: str,
    content: dict,
    captured_at: str,
    provider: dict | None = None,
) -> str:
    digest = hashlib.sha256(
        json.dumps(
            content,
            sort_keys=True,
            ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()
    relative = Path("snapshots") / key / f"{kind}-{digest}.json"
    payload = {
        "version": 1,
        "kind": kind,
        "audience": key,
        "captured_at": captured_at,
        "content_sha256": digest,
        "content": content,
    }
    if provider:
        payload["provider"] = provider
    serialized = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    path = _period_dir(year, month) / relative
    try:
        locked_create(path, serialized)
    except FileExistsError:
        existing = json.loads(path.read_text(encoding="utf-8"))
        existing_stable = dict(existing)
        payload_stable = dict(payload)
        existing_stable.pop("captured_at", None)
        payload_stable.pop("captured_at", None)
        # A retry after a partial capture re-verifies the same send, so
        # provider observation fields move while identity (single_send_id,
        # send_at) must not. Only identity divergence is a collision.
        for stable in (existing_stable, payload_stable):
            provider_stable = stable.get("provider")
            if isinstance(provider_stable, dict):
                provider_stable = dict(provider_stable)
                for field in ("verified_at", "status"):
                    provider_stable.pop(field, None)
                stable["provider"] = provider_stable
        if existing_stable != payload_stable:
            raise ValueError(f"newsletter snapshot collision: {path}")
    return str(relative)


def _create_sent_review(
    *,
    year: int,
    month: int,
    key: str,
    entry: dict,
    sent_content: dict,
    provider: dict,
    captured_at: str,
) -> str | None:
    original_path = str(entry.get("original_snapshot") or "")
    if not original_path:
        return None
    original = _snapshot_content(
        _read_snapshot(year, month, original_path)
    )
    record = build_review_record(
        mailing_name=mailing_name(
            year,
            month,
            SECTION_PURPOSES[key],
        ),
        audience_key=key,
        captured_at=captured_at,
        provider_single_send_id=provider["single_send_id"],
        provider_design_id=None,
        provider_ui_url="https://mc.sendgrid.com/single-sends",
        generated_subject=original["subject"],
        generated_preheader=original["preheader"],
        generated_body=original["body"],
        sent_subject=sent_content["subject"],
        sent_preheader=sent_content["preheader"],
        sent_body=sent_content["body"],
    )
    destination = newsletter_diffs_dir() / captured_at[:7]
    path = destination / f'{record["review_id"]}.review.json'
    serialized = json.dumps(record, indent=2, sort_keys=True) + "\n"
    try:
        locked_create(path, serialized)
    except FileExistsError:
        existing = validate_comparison(
            json.loads(path.read_text(encoding="utf-8"))
        )
        if existing != record:
            raise ValueError(f"newsletter review collision: {path}")
    ensure_empty_review_is_done(record)
    return str(path)


def read_local_sections(year: int, month: int) -> dict[str, dict]:
    """Read locally reviewed newsletter drafts for a period."""
    metadata = _load_metadata(year, month)
    drafts = metadata.get("drafts") or {}
    sections: dict[str, dict] = {}
    for key in SECTION_PURPOSES:
        path = newsletter_path(year, month, key)
        if not path.exists():
            continue
        subject, body = _split_newsletter_markdown(
            path.read_text(encoding="utf-8")
        )
        if not subject or not body:
            continue
        entry = drafts.get(key) or {}
        sections[key] = {
            "subject": subject,
            "body": body,
            "preheader": str(entry.get("preheader") or "").strip(),
        }
    return sections


def section_approvals(year: int, month: int) -> dict[str, bool]:
    """Whether each section's draft for this period is approved, by key.

    Out-of-band from read_local_sections on purpose: the section dicts feed
    snapshots and provider payload builders, so a flag added there would leak
    into stored snapshots and drift comparisons. The campaign path reads this
    beside the sections to hold any section-sourced email whose period draft
    Tiff has not approved, which is what makes approval a per-month fact
    rather than a one-time checkbox (JP 2026-08-24).
    """
    metadata = _load_metadata(year, month)
    drafts = metadata.get("drafts") or {}
    return {
        key: bool((drafts.get(key) or {}).get("approved_at"))
        for key in SECTION_PURPOSES
    }


def sections_due_for_materialization(
    *,
    year: int,
    month: int,
    class_date: date | None,
    sections: dict[str, dict],
    now: datetime,
) -> dict[str, dict]:
    """Return local sections whose Single Send creation window is open."""
    if now.tzinfo is None:
        raise ValueError("materialization time must be timezone aware")
    current = now.astimezone(timezone.utc)
    due: dict[str, dict] = {}
    for key, section in sections.items():
        purpose = SECTION_PURPOSES[key]
        try:
            send_at = mailing_schedule(year, month, purpose, class_date)
        except ValueError:
            continue
        materialize_at = send_at - MATERIALIZATION_WINDOW
        if materialize_at <= current < send_at:
            due[key] = section
    return due


def _validate_sections_before_provider(sections: dict[str, dict]) -> None:
    for key, section in sections.items():
        subject = str(section.get("subject") or "").strip()
        body = str(section.get("body") or "").strip()
        preheader = str(section.get("preheader") or "").strip()
        if not subject:
            raise ValueError(f"{key} subject is required")
        if not preheader:
            raise ValueError(f"{key} preheader is required")
        if preheader.casefold() == subject.casefold():
            raise ValueError(f"{key} preheader must not repeat the subject")
        if not body:
            raise ValueError(f"{key} body is required")
        if body.lstrip().startswith("#"):
            raise ValueError(f"{key} body begins with a markdown heading")
        combined = "\n".join((subject, preheader, body))
        # PROHIBITED is the shared definition in twy_platform.text, the same one
        # the writers normalize against, so this guard and the normalizer cannot
        # drift apart (they were separate literals until 2026-08-02).
        offenders = find_prohibited(combined)
        if offenders:
            raise ValueError(
                f"{key} contains prohibited punctuation: {offenders}"
            )
        if UNRESOLVED_TOKEN.search(combined):
            raise ValueError(f"{key} contains an unresolved token")


def lock_due_sections(
    *,
    year: int,
    month: int,
    class_date: date | None,
    now: datetime,
) -> dict[str, dict]:
    """Lock and return canonical content at the first tick inside T-24."""
    if now.tzinfo is None:
        raise ValueError("materialization time must be timezone aware")
    current = now.astimezone(timezone.utc)
    metadata = _load_metadata(year, month)
    drafts = metadata.get("drafts") or {}
    local_sections = read_local_sections(year, month)
    changed = False
    locked: dict[str, dict] = {}

    for key, section in local_sections.items():
        entry = drafts.setdefault(key, {})
        state = str(entry.get("state") or "draft")
        retrying_pre_provider_error = (
            state == "error"
            and not entry.get("provider")
            and bool(entry.get("locked_snapshot"))
        )
        if state == "locked" or retrying_pre_provider_error:
            purpose = SECTION_PURPOSES[key]
            try:
                send_at = mailing_schedule(
                    year,
                    month,
                    purpose,
                    class_date,
                )
            except ValueError:
                continue
            if current >= send_at:
                continue
            snapshot_path = str(entry.get("locked_snapshot") or "")
            if not snapshot_path:
                raise ValueError(f"{key} is locked without a snapshot")
            if retrying_pre_provider_error:
                entry["state"] = "locked"
                changed = True
            locked[key] = _snapshot_content(
                _read_snapshot(year, month, snapshot_path)
            )
            continue
        if state != "draft":
            continue

        due = sections_due_for_materialization(
            year=year,
            month=month,
            class_date=class_date,
            sections={key: section},
            now=current,
        )
        if key not in due:
            continue
        materialization_section = section
        if entry.get("approved_at"):
            approved_snapshot = str(
                entry.get("approved_snapshot") or ""
            )
            if not approved_snapshot:
                raise ValueError(
                    f"{key} is approved without an approved snapshot"
                )
            materialization_section = _snapshot_content(
                _read_snapshot(year, month, approved_snapshot)
            )
        materialization_section = resolve_section_tokens(
            key,
            materialization_section,
            year=year,
            month=month,
        )
        _validate_sections_before_provider({
            key: materialization_section,
        })
        send_at = mailing_schedule(
            year,
            month,
            SECTION_PURPOSES[key],
            class_date,
        )
        captured_at = current.isoformat()
        if (
            not entry.get("original_snapshot")
            and not entry.get("edited_at")
            and not entry.get("approved_at")
        ):
            generated_snapshot = _write_snapshot(
                year=year,
                month=month,
                key=key,
                kind="generated",
                content=section,
                captured_at=str(
                    entry.get("generated_at") or captured_at
                ),
            )
            entry["original_snapshot"] = generated_snapshot
            entry["latest_generated_snapshot"] = generated_snapshot
            entry.setdefault("generation_history", []).append(
                generated_snapshot
            )
        snapshot_path = _write_snapshot(
            year=year,
            month=month,
            key=key,
            kind="locked",
            content=materialization_section,
            captured_at=captured_at,
        )
        entry.update({
            "state": "locked",
            "locked_at": captured_at,
            "locked_snapshot": snapshot_path,
            "send_at": send_at.isoformat(),
        })
        changed = True
        locked[key] = materialization_section

    if changed:
        metadata["drafts"] = drafts
        _save_metadata(year, month, metadata)
    return locked


def apply_provider_report(
    *,
    year: int,
    month: int,
    report: dict[str, dict],
    now: datetime,
) -> None:
    """Persist scheduled and sent lifecycle state from verified provider data."""
    if now.tzinfo is None:
        raise ValueError("provider report time must be timezone aware")
    metadata = _load_metadata(year, month)
    drafts = metadata.get("drafts") or {}
    captured_at = now.astimezone(timezone.utc).isoformat()
    changed = False

    for purpose_name, result in report.items():
        key = PURPOSE_SECTIONS.get(purpose_name)
        if key is None or key not in drafts:
            continue
        entry = drafts[key]
        state = str(entry.get("state") or "draft")
        status = str(result.get("status") or "")
        provider_status = str(result.get("provider_status") or status)
        identifier = str(result.get("id") or "")
        send_at = str(result.get("send_at") or entry.get("send_at") or "")

        if status == "scheduled" and state in {"locked", "scheduled"}:
            entry["state"] = "scheduled"
            entry.setdefault("scheduled_at", captured_at)
            entry["provider"] = {
                "single_send_id": identifier,
                "status": provider_status,
                "send_at": send_at,
                "verified_at": captured_at,
            }
            for field in ("error", "error_at"):
                entry.pop(field, None)
            changed = True
            continue

        if status == "triggered" and state in {
            "locked",
            "scheduled",
            "sent",
            # error retries the post-send capture: the send already
            # happened, so there is nothing left to approve or arm.
            "error",
        }:
            if state == "sent":
                continue
            snapshot_path = str(entry.get("locked_snapshot") or "")
            if not snapshot_path:
                raise ValueError(f"{key} sent without a locked snapshot")
            content = _snapshot_content(
                _read_snapshot(year, month, snapshot_path)
            )
            provider = {
                "single_send_id": identifier,
                "status": provider_status,
                "send_at": send_at,
                "verified_at": captured_at,
            }
            sent_captured_at = send_at or captured_at
            sent_snapshot = _write_snapshot(
                year=year,
                month=month,
                key=key,
                kind="sent",
                content=content,
                captured_at=sent_captured_at,
                provider=provider,
            )
            review_path = _create_sent_review(
                year=year,
                month=month,
                key=key,
                entry=entry,
                sent_content=content,
                provider=provider,
                captured_at=sent_captured_at,
            )
            entry.update({
                "state": "sent",
                "sent_at": captured_at,
                "sent_snapshot": sent_snapshot,
                "provider": provider,
            })
            if review_path:
                entry["review_path"] = review_path
            for field in ("error", "error_at"):
                entry.pop(field, None)
            changed = True
            continue

        if status in {"unexpected", "overdue"} and state != "sent":
            entry.update({
                "state": "error",
                "error_at": captured_at,
                "provider": {
                    "single_send_id": identifier,
                    "status": provider_status,
                    "send_at": send_at,
                    "verified_at": captured_at,
                },
                "error": status,
            })
            changed = True

    if changed:
        metadata["drafts"] = drafts
        _save_metadata(year, month, metadata)


def mark_provider_error(
    *,
    year: int,
    month: int,
    audiences: set[str],
    error: str,
    now: datetime,
) -> None:
    """Record provider failure without discarding retryable lifecycle state."""
    if now.tzinfo is None:
        raise ValueError("provider error time must be timezone aware")
    metadata = _load_metadata(year, month)
    drafts = metadata.get("drafts") or {}
    failed_at = now.astimezone(timezone.utc).isoformat()
    changed = False
    for key in sorted(audiences):
        entry = drafts.get(key)
        if not entry or entry.get("state") == "sent":
            continue
        entry.update({
            "error_at": failed_at,
            "error": str(error),
        })
        changed = True
    if changed:
        metadata["drafts"] = drafts
        _save_metadata(year, month, metadata)


def _draft(
    campaigns: SendGridCampaigns,
    *,
    key: str,
    section: dict,
    year: int,
    month: int,
    send_to: dict,
) -> dict:
    purpose = SECTION_PURPOSES[key]
    return campaigns.create_draft(
        purpose=purpose,
        year=year,
        month=month,
        subject=section["subject"],
        body_md=section["body"],
        preheader=section.get("preheader", ""),
        send_to=send_to,
    )


def provision_drafts(
    *,
    campaigns: SendGridCampaigns,
    year: int,
    month: int,
    class_date: date | None,
    sections: dict[str, dict],
) -> dict[str, dict]:
    unknown = sorted(set(sections) - set(SECTION_PURPOSES))
    if unknown:
        raise ValueError(f"unsupported newsletter sections: {unknown}")
    _validate_sections_before_provider(sections)
    needs_habit = any(key != "lifestyle" for key in sections)
    if needs_habit and class_date is None:
        raise ValueError("Habit mailings require a class date")
    if class_date is not None and (
        class_date.year,
        class_date.month,
    ) != (year, month):
        raise ValueError("Habit class date must be in the newsletter period")
    campaigns.set_expected_purposes(
        [SECTION_PURPOSES[key] for key in sections]
    )

    subscriber_list_id = campaigns.registry.list_id(EMAIL_SUBSCRIBED)
    member_list_id = campaigns.registry.list_id(MEMBER_YOGA_LIFESTYLE)

    needs_interested = any(key in sections for key in ("ph1", "ph2"))
    needs_registered = any(
        key in sections for key in ("gentle_nudge", "reminder", "recording")
    )
    interested_list_id = (
        campaigns.ensure_list(
            habit_activity_name(year, month, "Interested")
        )
        if needs_interested
        else None
    )
    registered_list_id = (
        campaigns.ensure_list(
            habit_activity_name(year, month, "Registered")
        )
        if needs_registered
        else None
    )

    result: dict[str, dict] = {}

    if "lifestyle" in sections:
        result["lifestyle"] = _draft(
            campaigns,
            key="lifestyle",
            section=sections["lifestyle"],
            year=year,
            month=month,
            send_to={"list_ids": [member_list_id], "all": False},
        )

    if "non_lifestyle" in sections:
        query, parent_ids = general_invitation_query(
            subscribed_list_id=subscriber_list_id,
            member_list_id=member_list_id,
        )
        segment = campaigns.ensure_segment(
            purpose=MailingPurpose.GENERAL_INVITATION,
            year=year,
            month=month,
            query_dsl=query,
            parent_list_ids=parent_ids,
        )
        result["non_lifestyle"] = _draft(
            campaigns,
            key="non_lifestyle",
            section=sections["non_lifestyle"],
            year=year,
            month=month,
            send_to={"segment_ids": [segment["id"]], "all": False},
        )

    if any(key in sections for key in ("non_opener", "gentle_nudge")):
        initial = campaigns.single_send(
            MailingPurpose.GENERAL_INVITATION
        )
        initial_id = initial["id"]
    else:
        initial_id = None

    if "non_opener" in sections:
        segment = campaigns.ensure_segment(
            purpose=MailingPurpose.RESEND,
            year=year,
            month=month,
            query_dsl=non_opener_query(initial_id),
        )
        result["non_opener"] = _draft(
            campaigns,
            key="non_opener",
            section=sections["non_opener"],
            year=year,
            month=month,
            send_to={"segment_ids": [segment["id"]], "all": False},
        )

    if "gentle_nudge" in sections:
        segment = campaigns.ensure_segment(
            purpose=MailingPurpose.GENTLE_REMINDER,
            year=year,
            month=month,
            query_dsl=opener_not_registered_query(
                initial_id,
                registered_list_id,
            ),
        )
        result["gentle_nudge"] = _draft(
            campaigns,
            key="gentle_nudge",
            section=sections["gentle_nudge"],
            year=year,
            month=month,
            send_to={"segment_ids": [segment["id"]], "all": False},
        )

    if "reminder" in sections:
        result["reminder"] = _draft(
            campaigns,
            key="reminder",
            section=sections["reminder"],
            year=year,
            month=month,
            send_to={"list_ids": [registered_list_id], "all": False},
        )

    if "recording" in sections:
        result["recording"] = _draft(
            campaigns,
            key="recording",
            section=sections["recording"],
            year=year,
            month=month,
            send_to={"list_ids": [registered_list_id], "all": False},
        )

    for key, purpose in (
        ("ph1", MailingPurpose.FOLLOW_UP_1),
        ("ph2", MailingPurpose.FOLLOW_UP_2),
    ):
        if key not in sections:
            continue
        query, parent_ids = interested_nonmember_query(
            interested_list_id=interested_list_id,
            member_list_id=member_list_id,
        )
        segment = campaigns.ensure_segment(
            purpose=purpose,
            year=year,
            month=month,
            query_dsl=query,
            parent_list_ids=parent_ids,
        )
        result[key] = _draft(
            campaigns,
            key=key,
            section=sections[key],
            year=year,
            month=month,
            send_to={"segment_ids": [segment["id"]], "all": False},
        )

    return result


RECORDING_TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates"
HABIT_LANDING_URL = "https://habit.tiffanywoodyoga.com"
RECORDING_PRODUCT_URL = (
    "https://studio.tiffanywoodyoga.com/buy/product/{product_id}"
)


def _recording_record(year: int, month: int) -> dict:
    """The month's provisioned recording product record, or {}."""
    path = habit_recording_state_path(year, month)
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return {}
    return payload if isinstance(payload, dict) else {}


def resolve_section_tokens(
    key: str,
    section: dict,
    *,
    year: int,
    month: int,
) -> dict:
    """Substitute the newsletter token vocabulary with month facts.

    The vocabulary is the one the Class Plans editor documents in
    classes/dashboard/api.py (_substitute_tokens). This is the send-path
    resolver: it fills what the month's recording record can answer and
    leaves anything else in place for the pre-provider validator to
    reject, so a missing recording fails the lock loudly instead of
    mailing a token.
    """
    record = _recording_record(year, month)
    title = str(record.get("class_title") or "")
    product_id = record.get("product_id")
    recording_url = (
        RECORDING_PRODUCT_URL.format(product_id=int(product_id))
        if product_id
        else ""
    )
    landing = (
        f"{HABIT_LANDING_URL}?utm_source=newsletter"
        f"&utm_campaign={year:04d}-{month:02d}"
        f"&utm_content={key.replace('_', '-')}"
    )
    subject = str(section.get("subject") or "")
    preheader = str(section.get("preheader") or "")
    body = str(section.get("body") or "")
    if title:
        subject = subject.replace("{CLASS_TITLE}", title)
        preheader = preheader.replace("{CLASS_TITLE}", title)
        body = body.replace("{CLASS_TITLE}", f"[{title}]({landing})")
    if recording_url:
        body = body.replace(
            "{RECORDING_CTA}",
            f"[Watch {title or 'the class'}]({recording_url})",
        )
        body = body.replace("{RECORDING_URL}", recording_url)
    resolved = dict(section)
    resolved["subject"] = subject
    resolved["preheader"] = preheader
    resolved["body"] = body
    return resolved


def ensure_recording_draft(year: int, month: int) -> bool:
    """Seed the month's recording section from the canonical template.

    The Class Recording mailing is generic: one token template works for
    every Habit class because lock-time resolution fills the class title
    and the recording product link from the month's provisioning record.
    Seeding makes each month editable in the Class Plans editor with no
    hand authoring.
    """
    changed = False
    target = newsletter_path(year, month, "recording")
    if not target.exists():
        template = RECORDING_TEMPLATE_DIR / "recording.md"
        try:
            locked_create(target, template.read_text(encoding="utf-8"))
            changed = True
        except FileExistsError:
            # A concurrent seed beat us to it; either way the file is there
            # now, so this caller has nothing new to report.
            pass
    metadata = _load_metadata(year, month)
    drafts = metadata.setdefault("drafts", {})
    entry = drafts.setdefault("recording", {})
    if not str(entry.get("preheader") or "").strip():
        preheader = RECORDING_TEMPLATE_DIR / "recording_preheader.txt"
        entry["preheader"] = preheader.read_text(encoding="utf-8").strip()
        _save_metadata(year, month, metadata)
        changed = True
    return changed


def hold_mailing(
    *,
    year: int,
    month: int,
    key: str,
    campaigns: SendGridCampaigns,
    now: datetime,
) -> dict:
    """Take one mailing out of the automatic workflow until released.

    Removes the purpose from scheduling expectations, disarms any
    scheduled single send at the provider, and marks the draft held so
    the lock loop leaves it alone. The section, its snapshots, and the
    provider draft all survive intact.
    """
    if key not in SECTION_PURPOSES:
        raise ValueError(f"unknown section: {key}")
    if now.tzinfo is None:
        raise ValueError("hold time must be timezone aware")
    metadata = _load_metadata(year, month)
    drafts = metadata.setdefault("drafts", {})
    entry = drafts.setdefault(key, {})
    state = str(entry.get("state") or "draft")
    if state == "sent":
        raise ValueError(f"{key} is already sent")
    if state != "held":
        entry["state"] = "held"
        entry["held_at"] = now.astimezone(timezone.utc).isoformat()
        _save_metadata(year, month, metadata)
    campaigns.hold_purpose(SECTION_PURPOSES[key])
    return entry


def release_mailing(
    *,
    year: int,
    month: int,
    key: str,
    campaigns: SendGridCampaigns,
) -> dict:
    """Return a held or errored mailing to the automatic workflow."""
    if key not in SECTION_PURPOSES:
        raise ValueError(f"unknown section: {key}")
    metadata = _load_metadata(year, month)
    drafts = metadata.setdefault("drafts", {})
    entry = drafts.setdefault(key, {})
    state = str(entry.get("state") or "draft")
    if state == "sent":
        raise ValueError(f"{key} is already sent")
    if state in {"held", "error"}:
        entry["state"] = "draft"
        for field in ("held_at", "held_reason", "error", "error_at"):
            entry.pop(field, None)
        _save_metadata(year, month, metadata)
    campaigns.release_purpose(SECTION_PURPOSES[key])
    return entry
