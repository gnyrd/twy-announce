"""Rename a SendGrid contact when HeyMarvelous reports a member under a new address.

HeyMarvelous support changes a member's email on the student record. The next
member sync sees a known customer id under an address SendGrid has never seen,
and the old address still sitting on the member list. Left alone, the exact
sync would drop the old address from the member list and leave it on
`Email: Subscribed`, where it turns into a "TYL Non Member" and gets the
conversion campaign, while the new address never joins the newsletter list.

The planner is pure: given the desired members with ids and the member-list
export with the identity field, it says which contacts are renames and which
are something it refuses to touch. The executor performs one rename in a fixed
order, every step idempotent, delete last, so a failure anywhere leaves the old
contact intact and the next night finishes the job.

The ledger (JSON lines, append only) is the state machine: a `started` line
goes down before the first provider write and records the mode, `created`
(the new address had no contact) or `merged` (it already had one), and a
`completed` line after the old contact is deleted. A started line with no
completed line is a rename to resume, with the same mode as the first attempt,
which is what keeps consent handling stable across a retry.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import time

import journey_enrollment
from sendgrid_contact_identity import IDENTITY_FIELD
from sendgrid_contact_source import DETAIL_FIELD, SOURCE_FIELD

log = logging.getLogger("sendgrid_contact_rename")

STARTED = "started"
COMPLETED = "completed"
MODE_CREATED = "created"
MODE_MERGED = "merged"

# How long to wait for SendGrid's contact search to reflect a write before
# giving up. Reads after an import job can lag the job's own completion.
READBACK_SECONDS = 30
DELETE_SECONDS = 60


@dataclass(frozen=True)
class Rename:
    customer_id: str
    old_email: str
    new_email: str
    old_contact_id: str
    resume: dict | None = None


@dataclass
class RenamePlan:
    renames: list = field(default_factory=list)
    # (customer_id, [emails]) where more than one contact carries the id.
    duplicates: list = field(default_factory=list)
    # (email, listed customer id, desired customer id) where an address that
    # HeyMarvelous now attributes to one customer already carries another id.
    conflicts: list = field(default_factory=list)


def read_ledger(path) -> list[dict]:
    target = Path(path)
    if not target.exists():
        return []
    lines: list[dict] = []
    for raw in target.read_text(encoding="utf-8").splitlines():
        raw = raw.strip()
        if raw:
            lines.append(json.loads(raw))
    return lines


def append_ledger(path, line: dict) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(line, sort_keys=True) + "\n")


def open_started(ledger: list[dict], customer_id, old_email, new_email) -> dict | None:
    """The started line for this rename that no completed line has closed."""
    started = None
    for line in ledger:
        if (
            str(line.get("customer_id")) == str(customer_id)
            and line.get("old_email") == old_email
            and line.get("new_email") == new_email
        ):
            if line.get("event") == STARTED:
                started = line
            elif line.get("event") == COMPLETED:
                started = None
    return started


def plan_renames(
    desired_ids: dict[str, str],
    listed: list[dict],
    ledger: list[dict] | None = None,
) -> RenamePlan:
    """Which listed contacts must be renamed to satisfy the desired members.

    desired_ids maps a desired member email to its HeyMarvelous customer id.
    listed is the member-list export, each row {"email", "id", "fields"} with
    the identity field under fields. Rows without an id are invisible here:
    they are stamped by the ordinary upsert and can be judged the night after.
    """
    ledger = ledger or []
    plan = RenamePlan()
    holders_by_id: dict[str, list[dict]] = {}
    listed_id_by_email: dict[str, str] = {}
    for row in listed:
        email = str(row.get("email") or "").strip().lower()
        customer_id = str((row.get("fields") or {}).get(IDENTITY_FIELD) or "").strip()
        if not email or not customer_id:
            continue
        holders_by_id.setdefault(customer_id, []).append(
            {"email": email, "id": str(row.get("id") or "")}
        )
        listed_id_by_email[email] = customer_id

    for new_email, customer_id in sorted(desired_ids.items()):
        new_email = str(new_email or "").strip().lower()
        customer_id = str(customer_id or "").strip()
        if not new_email or not customer_id:
            continue
        listed_id = listed_id_by_email.get(new_email)
        if listed_id and listed_id != customer_id:
            plan.conflicts.append((new_email, listed_id, customer_id))
            continue
        holders = holders_by_id.get(customer_id) or []
        others = [holder for holder in holders if holder["email"] != new_email]
        if not others:
            continue
        if len(others) == 1:
            old = others[0]
            started = open_started(ledger, customer_id, old["email"], new_email)
            if len(holders) == 1 or started:
                plan.renames.append(
                    Rename(
                        customer_id=customer_id,
                        old_email=old["email"],
                        new_email=new_email,
                        old_contact_id=old["id"],
                        resume=started,
                    )
                )
                continue
        plan.duplicates.append(
            (customer_id, sorted(holder["email"] for holder in holders))
        )
    return plan


def _wait_until(check, *, seconds: float, sleep, clock) -> bool:
    deadline = clock() + seconds
    while True:
        if check():
            return True
        if clock() >= deadline:
            return False
        sleep(1.0)


def _reads_back_with_id(api, email: str, customer_id: str) -> bool:
    contact = api.contacts_by_emails([email]).get(email)
    if not contact:
        return False
    fields = contact.get("custom_fields") or {}
    return str(fields.get(IDENTITY_FIELD) or "").strip() == customer_id


def apply_rename(
    api,
    *,
    rename: Rename,
    suppression_group_id: int,
    enrollments,
    ledger_path,
    identity_field_id: str,
    field_ids: dict[str, str],
    now: datetime | None = None,
    sleep=time.sleep,
    clock=time.monotonic,
) -> dict:
    """Perform one rename, in order. See the module docstring for the order.

    field_ids maps custom field names to SendGrid field ids; the two source
    fields are copied from the old contact when the new one lacks them.
    enrollments is an open journey_enrollments connection, or None to skip.
    """
    stamp = (now or datetime.now(timezone.utc)).isoformat()
    old_email, new_email = rename.old_email, rename.new_email
    found = api.contacts_by_emails([old_email, new_email])
    old = found.get(old_email)
    if old is None:
        log.warning(
            "rename %s: %s is no longer a contact, nothing to do",
            rename.customer_id,
            old_email,
        )
        return {"skipped": "old_missing"}
    existing = found.get(new_email)
    old_contact_id = str(old.get("id") or rename.old_contact_id)

    if rename.resume:
        mode = str(rename.resume.get("mode") or "")
        if mode not in (MODE_CREATED, MODE_MERGED):
            mode = MODE_MERGED if existing else MODE_CREATED
    else:
        mode = MODE_MERGED if existing else MODE_CREATED
        append_ledger(
            ledger_path,
            {
                "event": STARTED,
                "at": stamp,
                "customer_id": rename.customer_id,
                "old_email": old_email,
                "new_email": new_email,
                "old_contact_id": old_contact_id,
                "mode": mode,
                "old_list_ids": sorted(old.get("list_ids") or []),
            },
        )

    # 1. The new address, on every list the old one holds, carrying the
    #    name, the acquisition source and the identity.
    list_ids = sorted(
        set(old.get("list_ids") or []) | set((existing or {}).get("list_ids") or [])
    )
    old_fields = dict(old.get("custom_fields") or {})
    existing_fields = dict((existing or {}).get("custom_fields") or {})
    payload: dict = {"email": new_email}
    for name in ("first_name", "last_name"):
        value = str((existing or {}).get(name) or "").strip()
        if not value:
            value = str(old.get(name) or "").strip()
        if value:
            payload[name] = value
    custom = {identity_field_id: rename.customer_id}
    for name in (SOURCE_FIELD, DETAIL_FIELD):
        field_identifier = field_ids.get(name)
        if field_identifier and old_fields.get(name) and not existing_fields.get(name):
            custom[field_identifier] = old_fields[name]
    payload["custom_fields"] = custom
    job_id = api.upsert_contacts(list_ids, [payload])
    api.wait_contact_job(job_id, timeout_s=300)
    if not _wait_until(
        lambda: _reads_back_with_id(api, new_email, rename.customer_id),
        seconds=READBACK_SECONDS,
        sleep=sleep,
        clock=clock,
    ):
        raise RuntimeError(
            f"rename {rename.customer_id}: {new_email} did not read back "
            "carrying the identity field; old contact left in place"
        )

    # 2. Consent follows the person when this rename created the address. A
    #    pre-existing address keeps its own state: it is the newer evidence.
    consent = {"copied": mode == MODE_CREATED, "group": False, "global": False}
    if mode == MODE_CREATED:
        if old_email in api.search_group_suppressions(suppression_group_id, [old_email]):
            api.add_group_suppressions(suppression_group_id, [new_email])
            consent["group"] = True
        if api.get_global_unsubscribe(old_email):
            api.add_global_unsubscribes([new_email])
            consent["global"] = True

    # 3. A welcome sequence in progress continues at the new address.
    moved = (
        journey_enrollment.rename_email(enrollments, old_email, new_email)
        if enrollments is not None
        else {"enrollments_moved": 0, "sends_moved": 0, "kept_existing": 0}
    )

    # 4. The old contact goes last, after everything it carried is elsewhere.
    api.delete_contacts([old_contact_id])
    gone = _wait_until(
        lambda: old_email not in api.contacts_by_emails([old_email]),
        seconds=DELETE_SECONDS,
        sleep=sleep,
        clock=clock,
    )
    if not gone:
        log.warning(
            "rename %s: deletion of %s requested but not yet visible; "
            "the next run resumes it",
            rename.customer_id,
            old_email,
        )

    new_contact = api.contacts_by_emails([new_email]).get(new_email) or {}
    summary = {
        "event": COMPLETED,
        "at": stamp,
        "customer_id": rename.customer_id,
        "old_email": old_email,
        "new_email": new_email,
        "old_contact_id": old_contact_id,
        "new_contact_id": str(new_contact.get("id") or ""),
        "mode": mode,
        "list_ids": list_ids,
        "consent": consent,
        "enrollments": moved,
        "old_deleted": gone,
    }
    append_ledger(ledger_path, summary)
    log.info(
        "renamed %s -> %s (customer %s, %s, %d lists)",
        old_email,
        new_email,
        rename.customer_id,
        mode,
        len(list_ids),
    )
    return summary
