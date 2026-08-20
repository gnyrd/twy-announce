"""Launch a campaign journey as a set of scheduled SendGrid Single Sends.

A campaign journey is authored in the same editor as a product journey, but it
is not per-person: it sends one Single Send per email to a whole non-member
segment, spaced by the per-email waits, starting on a date chosen at launch.
The per-person drip runner and the monthly newsletter registry are both left
untouched, because a campaign keeps its scheduled sends in its own state file.

Launching is idempotent. A repeat with the same start date re-checks the
provider and reschedules nothing already scheduled, so a partial launch resumes
rather than double-sending. Changing the start date requires an explicit
unschedule first, so a relaunch can never leave two schedules live.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
import json
from pathlib import Path
import re
import time as _time
from zoneinfo import ZoneInfo

# A newsletter draft template carries tokens like {CLASS_TITLE} that the old
# workflow resolves at lock time. The campaign content path reads the draft
# directly, so it must never send one still carrying a token. Matches the
# workflow's own UNRESOLVED_TOKEN.
_UNRESOLVED_TOKEN = re.compile(r"\{[A-Z][A-Z0-9_]*\}")

from newsletter_rendering import render_newsletter
from sendgrid_mailings import (
    INTERNAL_SEND_COPY,
    MEMBER_YOGA_LIFESTYLE,
    SECTION_PURPOSES,
    campaign_non_opener_segment_name,
    campaign_resend_send_name,
    campaign_single_send_name,
    habit_activity_name,
    interested_nonmember_query,
    non_opener_query,
    opener_not_registered_query,
    registered_query,
)
from twy_platform import locked_write
from twy_platform.journeys import (
    ANCHOR_CLASS_DATE,
    ANCHOR_CLASS_WEEKDAY_BEFORE,
    ANCHOR_FIRST_WEEKDAY,
    GATE_CLASS_EXISTS,
    GATE_CLASS_HAPPENED,
    GATE_RECORDING_READY,
    TYPE_CAMPAIGN,
    journey_type,
)
from twy_platform.text import find_prohibited


MOUNTAIN = ZoneInfo("America/Denver")
# Every campaign email goes out at the house newsletter hour, Mountain time.
SEND_HOUR = 9
SEND_MINUTE = 49


def _first_weekday(year: int, month: int) -> date:
    """The first Monday-through-Friday day of the month."""
    day = date(year, month, 1)
    while day.weekday() >= 5:  # Saturday is 5, Sunday is 6
        day += timedelta(days=1)
    return day


def _previous_weekday_strictly_before(day: date, weekday: int) -> date:
    """The given weekday (0=Monday..6=Sunday) strictly before this date.

    Mirrors the live workflow's sendgrid_mailings helper of the same name so a
    campaign anchored on a weekday reproduces the invitation, resend and gentle
    reminder dates exactly. The two implementations are held in agreement by the
    cutover parity tests, which assert the campaign schedule equals
    mailing_schedule for these mailings across class weekdays.
    """
    delta = (day.weekday() - weekday) % 7
    if delta == 0:
        delta = 7
    return day - timedelta(days=delta)


class CampaignLaunchError(ValueError):
    """A campaign cannot be launched as asked, and nothing was sent."""


@dataclass(frozen=True)
class GateContext:
    """The live facts a send gate is checked against when a message is provisioned.

    A recurring campaign resolves this per period from the class plans and
    HeyMarvelous. The launcher never reaches for those itself, so a test supplies
    a context directly and production builds one and passes it in.
    """
    class_exists: bool = False
    recording_ready: bool = False
    class_date: date | None = None
    now: date | None = None


def gate_passes(gate, ctx: GateContext) -> bool:
    """Whether a message carrying this gate should go out against this context.

    An empty gate always passes. Every real gate needs a context, so a set gate
    with no context is a programming error the caller must catch before here.
    """
    if not gate:
        return True
    if ctx is None:
        raise CampaignLaunchError(f"gate {gate} has no gate context to evaluate it")
    if gate == GATE_CLASS_EXISTS:
        return bool(ctx.class_exists)
    if gate == GATE_RECORDING_READY:
        return bool(ctx.recording_ready)
    if gate == GATE_CLASS_HAPPENED:
        return ctx.class_date is not None and ctx.now is not None and ctx.now >= ctx.class_date
    raise CampaignLaunchError(f"unknown campaign gate: {gate}")


class CampaignLauncher:
    def __init__(
        self,
        *,
        api,
        registry,
        journey: dict,
        state_path,
        now_fn=None,
        sleep_fn=_time.sleep,
        gate_context: GateContext = None,
        sections: dict = None,
        campaigns=None,
    ):
        if journey_type(journey) != TYPE_CAMPAIGN:
            raise CampaignLaunchError("not a campaign journey")
        self.api = api
        self.registry = registry
        # A SendGridCampaigns handle, used only to build a dynamic audience at
        # launch (the follow-ups' interested-non-members, the gentle reminder's
        # openers-not-registered). It ensures the same locked-name segment the
        # newsletter workflow does, so the two never mint a duplicate. A campaign
        # with only static audiences never touches it.
        self.campaigns = campaigns
        self.journey = journey
        self.state_path = Path(state_path)
        self.now_fn = now_fn or (lambda: datetime.now(timezone.utc))
        self._sleep = sleep_fn
        self.gate_context = gate_context
        # This period's newsletter drafts, keyed by section, as
        # read_local_sections returns them. A campaign email carrying a `section`
        # draws its copy from here instead of its static text; production passes
        # the resolved drafts in, a test supplies them directly.
        self.sections = sections or {}
        month = str(journey.get("campaign_month") or "")
        if len(month) != 7 or month[4] != "_":
            raise CampaignLaunchError("campaign month must be YYYY_MM")
        self._year = int(month[:4])
        self._month = int(month[5:7])
        self._name = journey["name"]
        self._segment_id = str(journey.get("segment_id") or "").strip()
        if not self._segment_id:
            raise CampaignLaunchError("campaign has no SendGrid segment to send to")

    # ---- state ----------------------------------------------------------
    def _new_state(self) -> dict:
        return {
            "version": 1,
            "journey_id": self.journey["journey_id"],
            "campaign_month": self.journey["campaign_month"],
            "segment": None,
            "sends": {},
        }

    def _load_state(self) -> dict:
        if not self.state_path.exists():
            return self._new_state()
        payload = json.loads(self.state_path.read_text(encoding="utf-8"))
        if payload.get("version") != 1:
            raise CampaignLaunchError("unsupported campaign launch state")
        return payload

    def _save_state(self, payload: dict) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        locked_write(
            self.state_path,
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
        )

    # ---- schedule math --------------------------------------------------
    def _send_at(self, day, hour: int = SEND_HOUR, minute: int = SEND_MINUTE) -> datetime:
        local = datetime.combine(day, time(hour, minute), tzinfo=MOUNTAIN)
        return local.astimezone(timezone.utc)

    def _class_date(self) -> date:
        """The Habit class date for this period, from the campaign context.

        A class-anchored email cannot resolve its date without it, so a missing
        one is refused rather than guessed.
        """
        ctx = self.gate_context
        if ctx is None or ctx.class_date is None:
            raise CampaignLaunchError(
                "a class-anchored email needs a class date in the campaign context"
            )
        return ctx.class_date

    def _anchor_date(self, anchor: str, offset_days: int, weekday=None) -> date:
        if anchor == ANCHOR_FIRST_WEEKDAY:
            base = _first_weekday(self._year, self._month)
        elif anchor == ANCHOR_CLASS_DATE:
            base = self._class_date()
        elif anchor == ANCHOR_CLASS_WEEKDAY_BEFORE:
            if weekday is None:
                raise CampaignLaunchError(
                    "a class_weekday_before email needs a weekday (0=Monday..6=Sunday)"
                )
            base = _previous_weekday_strictly_before(self._class_date(), int(weekday))
        else:
            raise CampaignLaunchError(f"unknown campaign anchor: {anchor}")
        return base + timedelta(days=offset_days)

    def _email_dates(self, start_date) -> dict:
        """The calendar date each email lands on.

        An anchored email derives its date from the period (the first weekday of
        the month, or the class date) moved by its offset. Otherwise email one is
        the run date, and a later email is pinned to its own fixed date or falls
        the wait it carries after the previous email. A fixed date earlier than
        the email before it is refused, because a sequence that goes backwards
        would send out of order.
        """
        dates = {}
        current = start_date
        for index, email in enumerate(self.journey.get("emails") or []):
            anchor = email.get("anchor")
            if anchor:
                resolved = self._anchor_date(
                    anchor,
                    int(email.get("offset_days") or 0),
                    email.get("weekday"),
                )
            elif index == 0:
                resolved = start_date
            elif email.get("send_date"):
                resolved = date.fromisoformat(email["send_date"])
                if resolved < current:
                    raise CampaignLaunchError(
                        f"email {index + 1} is dated {resolved.isoformat()}, "
                        f"before email {index} on {current.isoformat()}"
                    )
            else:
                resolved = current + timedelta(
                    days=int(email.get("interval_days") or 0)
                )
            dates[index] = resolved
            current = resolved
        return dates

    def _schedule(self, start_date) -> dict:
        emails = self.journey.get("emails") or []
        schedule = {}
        for index, day in self._email_dates(start_date).items():
            email = emails[index]
            hour = email.get("send_hour")
            minute = email.get("send_minute")
            schedule[index] = self._send_at(
                day,
                SEND_HOUR if hour is None else int(hour),
                SEND_MINUTE if minute is None else int(minute),
            )
        return schedule

    @staticmethod
    def _format(moment: datetime) -> str:
        return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    def _validate_start(self, start_date) -> None:
        if (start_date.year, start_date.month) != (self._year, self._month):
            raise CampaignLaunchError(
                f"start date {start_date.isoformat()} is not in the campaign "
                f"month {self.journey['campaign_month']}"
            )
        now = self.now_fn().astimezone(timezone.utc)
        for index, when in self._schedule(start_date).items():
            if when <= now:
                raise CampaignLaunchError(
                    "refusing to schedule a campaign email in the past: "
                    f"email {index + 1} at {self._format(when)}"
                )

    # ---- audience -------------------------------------------------------
    def _email_segment_id(self, email: dict) -> str:
        """The segment one email sends to: its own audience, or the default.

        A campaign can target a different segment per email, which is what lets
        one campaign invite non-members and remind registrants. An email with no
        audience of its own falls back to the campaign-level segment.
        """
        audience = email.get("audience") or {}
        return str(audience.get("segment_id") or "").strip() or self._segment_id

    @staticmethod
    def _dynamic_kind(email: dict):
        """The dynamic audience kind for this email, or None for a static one."""
        audience = email.get("audience") or {}
        return str(audience.get("dynamic") or "").strip() or None

    def _purpose_for(self, index: int, email: dict):
        """The mailing purpose that names a dynamic audience's segment.

        A dynamic audience is built for a specific mailing (the gentle reminder,
        a follow-up), so it borrows that mailing's section to name its segment
        with the same locked name the workflow uses. No section, no name.
        """
        section = str(email.get("section") or "").strip()
        if not section or section not in SECTION_PURPOSES:
            raise CampaignLaunchError(
                f"email {index + 1} has a dynamic audience but no section to "
                f"name its segment"
            )
        return SECTION_PURPOSES[section]

    def _resolve_audience(self, index: int, email: dict, state: dict):
        """The segment id one email sends to this period.

        A static audience returns its own segment or the campaign default. A
        dynamic audience is built now against the live lists and, for the opener
        case, the parent's own send. Returns None when a dynamic audience cannot
        be built yet this period (the opener parent has not sent), so the caller
        holds that one email the way a gate does.
        """
        kind = self._dynamic_kind(email)
        if not kind:
            return self._email_segment_id(email)
        if self.campaigns is None:
            raise CampaignLaunchError(
                f"email {index + 1} needs a dynamic audience but the launcher "
                f"was given no campaigns handle to build it"
            )
        purpose = self._purpose_for(index, email)
        if kind == "interested_nonmember":
            interested = self.campaigns.ensure_list(
                habit_activity_name(self._year, self._month, "Interested")
            )
            member = self.campaigns.registry.list_id(MEMBER_YOGA_LIFESTYLE)
            query, parent_ids = interested_nonmember_query(
                interested_list_id=interested, member_list_id=member
            )
            segment = self.campaigns.ensure_segment(
                purpose=purpose, year=self._year, month=self._month,
                query_dsl=query, parent_list_ids=parent_ids,
            )
        elif kind == "opener_not_registered":
            of = (email.get("audience") or {}).get("of")
            parent = (state.get("sends") or {}).get(str(of)) or {}
            parent_send_id = str(parent.get("id") or "")
            if not parent_send_id:
                # The parent email did not send this period, so there are no
                # openers to build from. Hold this one, like a gate.
                return None
            registered = self.campaigns.ensure_list(
                habit_activity_name(self._year, self._month, "Registered")
            )
            query = opener_not_registered_query(parent_send_id, registered)
            segment = self.campaigns.ensure_segment(
                purpose=purpose, year=self._year, month=self._month,
                query_dsl=query,
            )
        elif kind == "registered":
            registered = self.campaigns.ensure_list(
                habit_activity_name(self._year, self._month, "Registered")
            )
            query, parent_ids = registered_query(registered)
            segment = self.campaigns.ensure_segment(
                purpose=purpose, year=self._year, month=self._month,
                query_dsl=query, parent_list_ids=parent_ids,
            )
        else:
            raise CampaignLaunchError(
                f"email {index + 1} unknown dynamic audience: {kind}"
            )
        segment_id = str((segment or {}).get("id") or "")
        if not segment_id:
            raise CampaignLaunchError(
                f"email {index + 1} dynamic audience returned no segment id"
            )
        state.setdefault("dynamic_segments", {})[str(index)] = {
            "id": segment_id, "kind": kind,
        }
        self._save_state(state)
        return segment_id

    def _gated_out(self, index: int, email: dict) -> bool:
        """Whether this message's send gate holds it back this period.

        A message with no gate is never held. A gated message needs a context to
        judge against, so a gate with none is refused loudly rather than sent to
        the wrong audience or held forever by accident.
        """
        gate = email.get("gate")
        if not gate:
            return False
        if self.gate_context is None:
            raise CampaignLaunchError(
                f"email {index + 1} has gate {gate} but the campaign has no gate "
                f"context to evaluate it"
            )
        return not gate_passes(gate, self.gate_context)

    def _check_gate_context(self) -> None:
        """Refuse a gated campaign with no context before any state is written."""
        for index, email in enumerate(self.journey.get("emails") or []):
            self._gated_out(index, email)

    def _distinct_segment_ids(self) -> list:
        """Every segment this campaign will send to, plus the default fallback.

        Deduped and order-stable so the confirmation, and the state it records,
        are deterministic.
        """
        ids = []
        for email in self.journey.get("emails") or []:
            # A dynamic audience is built at launch, not confirmed up front, so
            # it contributes no id here.
            if self._dynamic_kind(email):
                continue
            segment_id = self._email_segment_id(email)
            if segment_id not in ids:
                ids.append(segment_id)
        if self._segment_id not in ids:
            ids.append(self._segment_id)
        return ids

    def _confirm_segment(self, segment_id: str) -> dict:
        try:
            segment = self.api.segment(segment_id)
        except Exception as exc:
            raise CampaignLaunchError(
                f"SendGrid segment {segment_id} could not be read: {exc}"
            )
        confirmed_id = str((segment or {}).get("id") or "")
        if not confirmed_id:
            raise CampaignLaunchError(
                f"SendGrid segment {segment_id} was not found"
            )
        return {"id": confirmed_id, "name": segment.get("name")}

    def _resolve_segments(self, state: dict) -> dict:
        """Confirm every segment the campaign will send to, before any send.

        We do not build or own these segments, so launching only confirms each
        one is still there. All are confirmed up front, so a single missing
        segment aborts the whole launch rather than sending some emails and
        leaving the rest pointed at a segment that is gone.
        """
        resolved = {sid: self._confirm_segment(sid) for sid in self._distinct_segment_ids()}
        state["segments"] = resolved
        # The campaign default is still recorded on its own key, so a reader
        # written before per-email audiences keeps working.
        state["segment"] = resolved.get(self._segment_id)
        self._save_state(state)
        return resolved

    # ---- single sends ---------------------------------------------------
    def _send_to(self, segment_id: str) -> dict:
        # JP directive 2026-08-09: every Single Send delivers an internal copy.
        copy_id = self.registry.list_id(INTERNAL_SEND_COPY)
        return {
            "segment_ids": [segment_id],
            "list_ids": [copy_id],
            "all": False,
        }

    def _email_config(self, subject: str, preheader: str, body: str) -> dict:
        rendered = render_newsletter(body, use_template=True, preheader=preheader)
        return {
            "subject": subject,
            "html_content": rendered.html,
            "plain_content": rendered.plain_text,
            "generate_plain_content": False,
            "editor": "design",
            "suppression_group_id": self.registry.suppression_group_id,
            "sender_id": self.registry.sender_id,
        }

    def _guard_prohibited(self, label: str, subject: str, preheader: str, body: str) -> None:
        offenders = find_prohibited("\n".join([subject, preheader, body]))
        if offenders:
            raise CampaignLaunchError(
                f"{label} contains prohibited punctuation: {offenders}"
            )

    def _email_content(self, email: dict):
        """This email's subject, preheader and body for the period, or None.

        An email carrying a `section` draws its copy from that section's draft in
        this period's newsletter (self.sections); None means the draft is not
        ready yet, so the email holds this period the way a false gate does. An
        email with no section keeps its own typed copy, which is how a one-off
        campaign works.
        """
        section = str(email.get("section") or "").strip()
        if section:
            resolved = self.sections.get(section)
            if not resolved:
                return None
            content = {
                "subject": str(resolved.get("subject") or ""),
                "preheader": str(resolved.get("preheader") or ""),
                "body": str(resolved.get("body") or ""),
            }
            # The draft still carries an unresolved template token (the campaign
            # content path does not resolve tokens the way the old workflow's
            # locking does), so hold rather than send a literal {CLASS_TITLE}.
            combined = "\n".join(content.values())
            if _UNRESOLVED_TOKEN.search(combined):
                return None
            return content
        return {
            "subject": str(email.get("subject") or ""),
            "preheader": str(email.get("preheader") or ""),
            "body": str(email.get("body") or ""),
        }

    def _email_payload(self, index: int, content: dict, segment_id: str) -> dict:
        subject = content["subject"]
        preheader = content["preheader"]
        body = content["body"]
        self._guard_prohibited(f"email {index + 1}", subject, preheader, body)
        return {
            "name": campaign_single_send_name(
                self._year, self._month, self._name, index
            ),
            "send_to": self._send_to(segment_id),
            "email_config": self._email_config(subject, preheader, body),
        }

    def _resend_payload(self, index: int, parent_content: dict, resend: dict, segment_id: str) -> dict:
        """The resend child's Single Send: the parent copy unless overridden here."""
        subject = str(resend.get("subject") or parent_content.get("subject") or "")
        preheader = str(resend.get("preheader") or parent_content.get("preheader") or "")
        body = str(resend.get("body") or parent_content.get("body") or "")
        self._guard_prohibited(f"email {index + 1} resend", subject, preheader, body)
        return {
            "name": campaign_resend_send_name(
                self._year, self._month, self._name, index
            ),
            "send_to": self._send_to(segment_id),
            "email_config": self._email_config(subject, preheader, body),
        }

    def _provision(self, payload: dict, send_at: str, label: str):
        """Create one Single Send, verify its name, schedule it, verify that.

        Returns (single_send_id, record). Every Single Send a launch creates,
        parent or resend child, goes through here so both are checked the same.
        """
        created = self.api.create_single_send(payload)
        single_send_id = str(created.get("id") or "")
        if not single_send_id:
            raise CampaignLaunchError(f"{label} Single Send returned no id")
        confirmed = self.api.get_single_send(single_send_id)
        if confirmed.get("name") != payload["name"]:
            raise CampaignLaunchError(f"{label} created Single Send name mismatch")
        self.api.schedule_single_send(single_send_id, send_at)
        scheduled = self.api.get_single_send(single_send_id)
        if (
            scheduled.get("status") != "scheduled"
            or scheduled.get("send_at") != send_at
        ):
            raise CampaignLaunchError(f"{label} schedule verification failed")
        return single_send_id, {
            "id": single_send_id,
            "name": payload["name"],
            "send_at": send_at,
            "status": "scheduled",
            "verified_at": self.now_fn().astimezone(timezone.utc).isoformat(),
        }

    def _ensure_non_opener_segment(self, index: int, parent_send_id: str, state: dict) -> str:
        """The segment of contacts sent the parent email who did not open it.

        Built from non_opener_query of the parent's Single Send id and named after
        that email. Recorded in state so a resumed launch reuses it rather than
        minting a second segment for the same email.
        """
        name = campaign_non_opener_segment_name(
            self._year, self._month, self._name, index
        )
        query_dsl = non_opener_query(parent_send_id)
        existing = (state.get("resend_segments") or {}).get(str(index))
        if existing and existing.get("id"):
            try:
                segment = self.api.segment(existing["id"])
            except Exception:
                segment = None
            if segment and str(segment.get("id")) == existing["id"]:
                return existing["id"]
        segment = self.api.create_segment(name=name, query_dsl=query_dsl)
        segment_id = str((segment or {}).get("id") or "")
        if not segment_id:
            raise CampaignLaunchError(
                f"email {index + 1} non-opener segment returned no id"
            )
        state.setdefault("resend_segments", {})[str(index)] = {
            "id": segment_id, "name": name,
        }
        self._save_state(state)
        return segment_id

    def _provision_resend(self, index, parent_content, resend, parent_send_id, start_date, state) -> dict:
        """Create the resend child once its parent has been scheduled.

        The child sends the parent's copy, or the override, to the parent's
        non-openers, a set number of days after the parent's own send date. The
        parent's copy is its resolved content for the period, so a resend of a
        draft-sourced parent carries this month's draft copy.
        """
        parent_day = self._email_dates(start_date)[index]
        child_send_at = self._format(
            self._send_at(parent_day + timedelta(days=int(resend["wait_days"])))
        )
        entry = (state.get("resends") or {}).get(str(index))
        if entry and self._already_scheduled(entry, child_send_at):
            return {**entry, "resend_of": index, "skipped": True}
        segment_id = self._ensure_non_opener_segment(index, parent_send_id, state)
        payload = self._resend_payload(index, parent_content, resend, segment_id)
        _child_id, record = self._provision(
            payload, child_send_at, f"email {index + 1} resend"
        )
        record["resend_of"] = index
        record["segment_id"] = segment_id
        state.setdefault("resends", {})[str(index)] = record
        self._save_state(state)
        return {**record, "skipped": False}

    def _already_scheduled(self, entry: dict, send_at: str) -> bool:
        if not entry.get("id"):
            return False
        try:
            single_send = self.api.get_single_send(entry["id"])
        except Exception:
            return False
        return (
            single_send.get("status") == "scheduled"
            and single_send.get("send_at") == send_at
        )

    # ---- public API -----------------------------------------------------
    def plan(self, start_date) -> dict:
        """What a launch would schedule, touching no provider or state.

        This is what the double-confirm shows: how many emails, to which
        audience, and on what dates, before anything is created.
        """
        schedule = self._schedule(start_date)
        rows = []
        dynamic_names = {
            "interested_nonmember": "Interested non-members (built at send)",
            "opener_not_registered": "Openers not registered (built at send)",
            "registered": "Registered for the class (built at send)",
        }
        for index, email in enumerate(self.journey.get("emails") or []):
            audience = email.get("audience") or {}
            content = self._email_content(email)
            kind = self._dynamic_kind(email)
            rows.append({
                "index": index,
                "position": index + 1,
                "subject": content["subject"] if content else str(email.get("subject") or ""),
                "section": email.get("section"),
                "content_pending": content is None,
                "name": campaign_single_send_name(
                    self._year, self._month, self._name, index
                ),
                "send_at": self._format(schedule[index]),
                # A dynamic audience has no id until launch, so the preview names
                # it rather than guessing a segment.
                "segment_id": None if kind else self._email_segment_id(email),
                "segment_name": (
                    dynamic_names.get(kind, kind) if kind
                    else audience.get("segment_name") or self.journey.get("segment_name")
                ),
                "dynamic_audience": kind,
                "gate": email.get("gate"),
                "gated_out": self._gated_out(index, email),
            })
        return {
            "journey_id": self.journey["journey_id"],
            "campaign_month": self.journey["campaign_month"],
            "audience": self.journey.get("audience"),
            "start_date": start_date.isoformat(),
            "emails": rows,
            "count": len(rows),
        }

    def launch(self, start_date) -> dict:
        """Create and schedule one Single Send per email. Idempotent."""
        self._validate_start(start_date)
        self._check_gate_context()
        state = self._load_state()
        recorded_start = state.get("start_date")
        if recorded_start and recorded_start != start_date.isoformat():
            raise CampaignLaunchError(
                f"campaign already launched for {recorded_start}; unschedule "
                f"before relaunching on {start_date.isoformat()}"
            )
        state["start_date"] = start_date.isoformat()
        self._save_state(state)

        self._resolve_segments(state)
        schedule = self._schedule(start_date)
        results = []
        for index, email in enumerate(self.journey.get("emails") or []):
            send_at = self._format(schedule[index])
            if self._gated_out(index, email):
                # Held back this period by its send gate. Nothing is created, so a
                # later run (or the recurring tick) provisions it once the gate
                # holds, since no send state exists for it yet.
                results.append({
                    "index": index,
                    "name": campaign_single_send_name(
                        self._year, self._month, self._name, index
                    ),
                    "gate": email.get("gate"),
                    "gated_out": True,
                    "skipped": True,
                })
                continue
            content = self._email_content(email)
            if content is None:
                # A draft-sourced email whose section has no draft this period.
                # It holds like a gated one, nothing is created, and a later run
                # provisions it once the draft is ready.
                results.append({
                    "index": index,
                    "name": campaign_single_send_name(
                        self._year, self._month, self._name, index
                    ),
                    "section": email.get("section"),
                    "content_pending": True,
                    "skipped": True,
                })
                continue
            entry = (state.get("sends") or {}).get(str(index))
            if entry and self._already_scheduled(entry, send_at):
                results.append({**entry, "skipped": True})
                parent_send_id = str(entry.get("id") or "")
            else:
                segment_id = self._resolve_audience(index, email, state)
                if segment_id is None:
                    # A dynamic audience that cannot be built this period (its
                    # opener parent did not send). Held, and a later run builds
                    # it once the parent is out.
                    results.append({
                        "index": index,
                        "name": campaign_single_send_name(
                            self._year, self._month, self._name, index
                        ),
                        "audience_pending": True,
                        "skipped": True,
                    })
                    continue
                payload = self._email_payload(index, content, segment_id)
                parent_send_id, record = self._provision(
                    payload, send_at, f"email {index + 1}"
                )
                record["segment_id"] = segment_id
                state.setdefault("sends", {})[str(index)] = record
                self._save_state(state)
                results.append({**record, "skipped": False})

            # A resend child is provisioned only after its parent has been
            # scheduled, because its audience is that parent's non-openers and its
            # timing is measured from the parent's send. It stays checked even when
            # the parent was already scheduled, so a resumed launch still completes
            # a child that a prior run had not created yet.
            resend = email.get("resend")
            if resend:
                results.append(
                    self._provision_resend(
                        index, content, resend, parent_send_id, start_date, state
                    )
                )

        return {
            "journey_id": self.journey["journey_id"],
            "segment_id": self._segment_id,
            "start_date": start_date.isoformat(),
            "sends": results,
        }

    def unschedule(self) -> list[str]:
        """Pull back every not-yet-sent Single Send and clear the start date.

        Reversible by design: a scheduled Single Send can be unscheduled at the
        provider up until it sends, which frees the campaign to be relaunched on
        a new date. A send already triggered is left alone.
        """
        state = self._load_state()
        pulled = []
        for entry in (state.get("sends") or {}).values():
            single_send_id = entry.get("id")
            if not single_send_id:
                continue
            single_send = self.api.get_single_send(single_send_id)
            if single_send.get("status") == "scheduled":
                self.api.unschedule_single_send(single_send_id)
                entry["status"] = "draft"
                pulled.append(single_send_id)
        state.pop("start_date", None)
        self._save_state(state)
        return pulled

    def launch_state(self):
        """The recorded launch state, or None if never launched."""
        if not self.state_path.exists():
            return None
        return self._load_state()
