"""
Prompt assembly for newsletter generation.
Builds fully populated prompt text for Tweee (members + non-members).
"""
import calendar
from datetime import date
import requests as _requests

CLASSES_API = "http://localhost:5003"


def get_habit_class_date(year: int, month: int) -> date:
    """Return the Habit class date for the given month by querying the classes API.

    Looks for a plan with class_type == 'Habit' in the given month.
    Falls back to second Saturday if no plan found (pre-entry safety net).
    """
    last_day = calendar.monthrange(year, month)[1]
    from_date = f"{year:04d}-{month:02d}-01"
    to_date = f"{year:04d}-{month:02d}-{last_day:02d}"
    try:
        resp = _requests.get(
            f"{CLASSES_API}/api/plans",
            params={"from": from_date, "to": to_date},
            timeout=10,
        )
        if resp.ok:
            for plan in resp.json():
                if plan.get("class_type") == "Habit":
                    return date.fromisoformat(plan["date"])
    except Exception:
        pass
    # Fallback: second Saturday
    count = 0
    for day in range(1, last_day + 1):
        if date(year, month, day).weekday() == 5:
            count += 1
            if count == 2:
                return date(year, month, day)
    raise ValueError(f"No Habit class date found for {year}-{month:02d}")


def check_coverage(plans: dict, year: int, month: int) -> None:
    """Raise ValueError if plans are insufficient for the month.

    Tiff teaches ~3 days/week (Mon/Tue/Thu + Yoga Habit free class).
    Minimum: 7 plans, and the Habit class date must have a plan.
    """
    if len(plans) < 7:
        raise ValueError(
            f"insufficient class plans: {len(plans)} plans for {year}-{month:02d} "
            f"(need at least 7)"
        )
    habit_date = get_habit_class_date(year, month)
    habit_str = habit_date.isoformat()
    if habit_str not in plans:
        raise ValueError(
            f"no class plan for Yoga Habit date {habit_str}"
        )


def assemble_lifestyle_prompt(overview: dict, plans: dict, year: int, month: int) -> str:
    habit_date = get_habit_class_date(year, month)
    habit_str = habit_date.strftime("%B %-d")
    habit_plan = plans.get(habit_date.isoformat(), {})

    plan_lines = []
    for date_str in sorted(plans.keys()):
        p = plans[date_str]
        title = p.get("title", "")
        if title:
            try:
                d = date.fromisoformat(date_str)
            except ValueError:
                continue
            plan_lines.append(f"- {d.strftime('%B %-d')}: {title}")

    plans_block = "\n".join(plan_lines) if plan_lines else "(no plans yet)"

    return f"""Write a member newsletter for Tiffany Wood Yoga.

Month: {habit_date.strftime('%B %Y')}
Theme: {overview.get('title', '')} -- {overview.get('teaching_notes', '')}
Physical arc: {overview.get('physical_arc', '')}
Apex pose: {overview.get('apex_pose', '')}
UPAs: {overview.get('upa', '')}
Member affirmation: "{overview.get('affirmation', '')}"

Tiff is attuned to astrology but does not present herself as a celestially-guided teacher. If astrological energy reinforces the month's theme, reference it as a felt quality only -- no planet names, no specific dates, no events. Example: "there's a natural moment of clarity mid-month." One brief mention max, or none if it doesn't serve the theme.

Class schedule this month:
{plans_block}

Yoga Habit class (free, open to anyone -- invite members to bring someone):
Date/time: {habit_str} | {habit_plan.get('time', '')} MT | {habit_plan.get('duration', '')} min | Free on Zoom
Title: {habit_plan.get('title', '')}
Description: {habit_plan.get('description', '')}
Apex pose: {habit_plan.get('apex_pose', '')}
Physical arc: {habit_plan.get('physical_arc', '')}
Props: {habit_plan.get('props', '')}

Write this as Tiff -- her voice, her vernacular, her storytelling. "Here's what's happening this month." Warm, specific, a little irreverent. Not a philosophy essay. Tell them what the month is about, what they'll feel in their bodies, what's coming up. End with the event invitation and the ask to bring someone.

Hard limit: 300 words. Subject line included, not counted.
Structure: subject line, 3-4 short paragraphs, event block with details, sign-off.
No bullet lists except the event block. No headers."""


def assemble_non_lifestyle_prompt(overview: dict, plans: dict, year: int, month: int) -> str:
    habit_date = get_habit_class_date(year, month)
    habit_str = habit_date.strftime("%B %-d")
    habit_plan = plans.get(habit_date.isoformat(), {})

    return f"""Write an open-door newsletter for people who aren't Tiffany Wood Yoga members.

Month theme: {overview.get('title', '')} -- {overview.get('teaching_notes', '')}

Yoga Habit class -- this is what you're inviting them to. Use ONLY these details. Do not invent or embellish:
Date/time: {habit_str} | {habit_plan.get('time', '')} MT | {habit_plan.get('duration', '')} min | Free on Zoom
Title: {habit_plan.get('title', '')}
Description: {habit_plan.get('description', '')}
Apex pose: {habit_plan.get('apex_pose', '')}
Physical arc: {habit_plan.get('physical_arc', '')}
Props: {habit_plan.get('props', '')}
No experience needed. No membership. No commitment.
Register: https://habit.tiffanywoodyoga.com

Write this as Tiff -- direct, warm, no yoga-speak. This person is on the fence. They're curious, or tired, or overdue. One thing is happening. One reason to come. One clear ask: register.

Hard limit: 175 words. Subject line included, not counted.
Structure: subject line, 2-3 short paragraphs, event details, register link, sign-off.
No headers. No bullets."""
