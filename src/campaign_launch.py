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

from datetime import date, datetime, time, timedelta, timezone
import json
from pathlib import Path
import time as _time
from zoneinfo import ZoneInfo

from newsletter_rendering import render_newsletter
from sendgrid_mailings import (
    INTERNAL_SEND_COPY,
    campaign_single_send_name,
)
from twy_platform import locked_write
from twy_platform.journeys import TYPE_CAMPAIGN, journey_type
from twy_platform.text import find_prohibited


MOUNTAIN = ZoneInfo("America/Denver")
# Every campaign email goes out at the house newsletter hour, Mountain time.
SEND_HOUR = 9
SEND_MINUTE = 49


class CampaignLaunchError(ValueError):
    """A campaign cannot be launched as asked, and nothing was sent."""


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
    ):
        if journey_type(journey) != TYPE_CAMPAIGN:
            raise CampaignLaunchError("not a campaign journey")
        self.api = api
        self.registry = registry
        self.journey = journey
        self.state_path = Path(state_path)
        self.now_fn = now_fn or (lambda: datetime.now(timezone.utc))
        self._sleep = sleep_fn
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
    def _send_at(self, day) -> datetime:
        local = datetime.combine(
            day, time(SEND_HOUR, SEND_MINUTE), tzinfo=MOUNTAIN
        )
        return local.astimezone(timezone.utc)

    def _email_dates(self, start_date) -> dict:
        """The calendar date each email lands on.

        Email one is the run date. A later email is either pinned to its own
        fixed date, or falls the wait it carries after the previous email's
        date. A fixed date earlier than the email before it is refused, because
        a sequence that goes backwards would send out of order.
        """
        dates = {}
        current = start_date
        for index, email in enumerate(self.journey.get("emails") or []):
            if index == 0:
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
        return {
            index: self._send_at(day)
            for index, day in self._email_dates(start_date).items()
        }

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

    def _distinct_segment_ids(self) -> list:
        """Every segment this campaign will send to, plus the default fallback.

        Deduped and order-stable so the confirmation, and the state it records,
        are deterministic.
        """
        ids = []
        for email in self.journey.get("emails") or []:
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

    def _email_payload(self, index: int, email: dict, segment_id: str) -> dict:
        subject = str(email.get("subject") or "")
        preheader = str(email.get("preheader") or "")
        body = str(email.get("body") or "")
        offenders = find_prohibited("\n".join([subject, preheader, body]))
        if offenders:
            raise CampaignLaunchError(
                f"email {index + 1} contains prohibited punctuation: {offenders}"
            )
        rendered = render_newsletter(body, use_template=True, preheader=preheader)
        return {
            "name": campaign_single_send_name(
                self._year, self._month, self._name, index
            ),
            "send_to": self._send_to(segment_id),
            "email_config": {
                "subject": subject,
                "html_content": rendered.html,
                "plain_content": rendered.plain_text,
                "generate_plain_content": False,
                "editor": "design",
                "suppression_group_id": self.registry.suppression_group_id,
                "sender_id": self.registry.sender_id,
            },
        }

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
        for index, email in enumerate(self.journey.get("emails") or []):
            audience = email.get("audience") or {}
            rows.append({
                "index": index,
                "position": index + 1,
                "subject": str(email.get("subject") or ""),
                "name": campaign_single_send_name(
                    self._year, self._month, self._name, index
                ),
                "send_at": self._format(schedule[index]),
                "segment_id": self._email_segment_id(email),
                "segment_name": audience.get("segment_name") or self.journey.get("segment_name"),
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
            entry = (state.get("sends") or {}).get(str(index))
            if entry and self._already_scheduled(entry, send_at):
                results.append({**entry, "skipped": True})
                continue

            payload = self._email_payload(index, email, self._email_segment_id(email))
            created = self.api.create_single_send(payload)
            single_send_id = str(created.get("id") or "")
            if not single_send_id:
                raise CampaignLaunchError(
                    f"email {index + 1} Single Send returned no id"
                )
            confirmed = self.api.get_single_send(single_send_id)
            if confirmed.get("name") != payload["name"]:
                raise CampaignLaunchError(
                    f"email {index + 1} created Single Send name mismatch"
                )

            self.api.schedule_single_send(single_send_id, send_at)
            scheduled = self.api.get_single_send(single_send_id)
            if (
                scheduled.get("status") != "scheduled"
                or scheduled.get("send_at") != send_at
            ):
                raise CampaignLaunchError(
                    f"email {index + 1} schedule verification failed"
                )

            record = {
                "id": single_send_id,
                "segment_id": self._email_segment_id(email),
                "name": payload["name"],
                "send_at": send_at,
                "status": "scheduled",
                "verified_at": self.now_fn().astimezone(timezone.utc).isoformat(),
            }
            state.setdefault("sends", {})[str(index)] = record
            self._save_state(state)
            results.append({**record, "skipped": False})

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
