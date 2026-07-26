"""Provision the seven TWY monthly SendGrid drafts and audiences."""

from __future__ import annotations

from datetime import date

from sendgrid_campaigns import SendGridCampaigns
from sendgrid_mailings import (
    EMAIL_SUBSCRIBED,
    MEMBER_YOGA_LIFESTYLE,
    MailingPurpose,
    general_invitation_query,
    habit_activity_name,
    interested_nonmember_query,
    non_opener_query,
    opener_not_registered_query,
)


SECTION_PURPOSES = {
    "lifestyle": MailingPurpose.MONTHLY,
    "non_lifestyle": MailingPurpose.GENERAL_INVITATION,
    "non_opener": MailingPurpose.RESEND,
    "gentle_nudge": MailingPurpose.GENTLE_REMINDER,
    "reminder": MailingPurpose.REGISTERED_REMINDER,
    "ph1": MailingPurpose.FOLLOW_UP_1,
    "ph2": MailingPurpose.FOLLOW_UP_2,
}


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
        key in sections for key in ("gentle_nudge", "reminder")
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
