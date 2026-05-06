"""
Prompt assembly for newsletter generation.
Builds fully populated prompt text for Tweee (members + non-members).
"""
import calendar
from datetime import date
import sys
import requests

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
        resp = requests.get(
            f"{CLASSES_API}/api/plans",
            params={"from": from_date, "to": to_date},
            timeout=10,
        )
        if resp.ok:
            for plan in resp.json():
                if plan.get("class_type") == "Habit":
                    return date.fromisoformat(plan["date"])
    except requests.RequestException as exc:
        print(f"[get_habit_class_date] API unreachable, falling back to second Saturday: {exc}", file=sys.stderr)
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
    habit_plan = plans[habit_str]
    if not (habit_plan.get("affirmation") or "").strip():
        raise ValueError(
            f"Yoga Habit class plan {habit_str} is missing the affirmation field "
            f"(this drives the lifestyle newsletter)"
        )


def assemble_lifestyle_prompt(overview: dict, plans: dict, year: int, month: int) -> str:
    habit_date = get_habit_class_date(year, month)
    habit_str = habit_date.strftime("%B %-d")
    habit_plan = plans.get(habit_date.isoformat(), {})

    affirmation   = habit_plan.get('affirmation')      or overview.get('affirmation', '')
    physical_arc  = habit_plan.get('physical_arc')     or overview.get('physical_arc', '')
    apex_pose     = habit_plan.get('apex_pose')        or overview.get('apex_pose', '')
    upas          = habit_plan.get('upas_key_actions') or overview.get('upa', '')
    teaching_lens = habit_plan.get('teaching_lens', '')

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
Physical arc: {physical_arc}
Apex pose: {apex_pose}
UPAs: {upas}
Member affirmation: "{affirmation}"
Teaching lens: {teaching_lens}

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

Write this as Tiff — her voice, her vernacular, her storytelling. Warm, specific, a little irreverent. Weave the practice into one lived moment — how it meets the day, the body, the line at the grocery store, a hard conversation. Show the depth through experience, don't lecture about it. Non-dual undertone is welcomed; philosophy essay is not. Tell them what the month is about, what they'll feel in their bodies, what's coming up. End with the event invitation and the ask to bring someone.

Hard limit: 300 words. Subject line included, not counted.
Shape: natural, not formulaic. Subject line, body that flows, event details where they fit, sign-off. No headers, no bullets except the event details if it helps."""


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
For people with an established practice who want to deepen it. Not a beginner class. Free on Zoom.

Include this exact markdown link verbatim. Do NOT paraphrase to "Register here: URL" or any other prose form. The downstream renderer needs the markdown syntax to produce a clickable link:
[Register Here](https://habit.tiffanywoodyoga.com)

Write this as Tiff — warm, accessible, no yoga jargon. This person is on the fence. They're curious, or tired, or overdue. One thing is happening. One reason to come. One clear ask: register. You can gesture at the deeper why ONCE, briefly — a single sentence that lets the depth show without requiring belief or vocabulary. Discovered, not explained.

Hard limit: 175 words. Subject line included, not counted.
Shape: natural, not formulaic. Subject line, body, event details, register link, sign-off. No headers or bullets."""


# Reference emails (April 2026, Tiff's voice) — quality bar for prompt templates
_PH1_REFERENCE = """Hello!

Thank you for being part of this month's Yoga Habit class.

There was something truly beautiful in the way we practiced together — across locations, across lives — yet meeting in a shared rhythm of attention and care. These moments remind me how real this work is. Even for one class, when we return to our center, something shifts.

I often think of this practice as a living expression of Indra's Net — each of us a point of light, connected. When we remember our center, and then carry that into our lives, it quietly strengthens the whole.

The challenge, of course, is not the one class — it's staying connected to that awareness as life continues.

That's where Abhyāsa comes in.

If you'd like to continue practicing, I'd love to have you inside The Yoga Lifestyle. It's a space to return to — again and again — no matter what's happening in your life.

As a simple invitation, I'm offering your first month for $49 if you join this week.

You can explore and join here:
[link]

Whether or not you continue, I'm so glad you showed up. It matters.

With gratitude,
Tiffany

---

✨ Our next Yoga Habit class is [NEXT MONTH DATE] — I'd love for you to join us.

The community really does feel different when you're part of it. Even if this is the only class you can make, it matters.

Building a habit takes time. Let this be a gentle place to begin."""

_PH2_REFERENCE = """Hi,

As the week begins to wind down, I wanted to offer a small reminder…

You already know how to return to your center.

We touched that place together in practice — that steady point underneath the movement, the breath, the noise of the week. It's still there.

Even a few conscious breaths…
Even one moment of remembering…
That is practice.

If you feel the pull to reconnect, I'd love to have you continue inside The Yoga Lifestyle. It's a place to return to — again and again — in a way that supports real life.

Your first month is just $49 — that offer closes soon.

You can explore it here:
[link]

And if not, simply pause for a moment today and remember:
your center hasn't gone anywhere.

With love,
Tiffany

P.S. Our next Yoga Habit class is [NEXT MONTH DATE] — mark your calendar. You are always welcome and wanted here."""


def assemble_ph1_prompt(overview: dict, plans: dict, year: int, month: int) -> str:
    """Prompt for the first post-Habit-class follow-up email (send +24hrs)."""
    habit_date = get_habit_class_date(year, month)
    habit_str = habit_date.strftime("%B %-d")
    habit_plan = plans.get(habit_date.isoformat(), {})

    return f"""Write a follow-up email to people who attended the Yoga Habit free class on {habit_str}.

Class context:
Title: {habit_plan.get('title', 'The Yoga Habit')}
Description: {habit_plan.get('description', '')}
Theme: {overview.get('title', '')} — {overview.get('teaching_notes', '')}

This email sends 24 hours after class ends. The reader just practiced with Tiff for the first time (or returned after a gap). They're in the afterglow.

Goal: contemplative thank-you that weaves the practice into life and naturally opens into an invitation to continue inside The Yoga Lifestyle. Match the reference's non-dual undertone, its weaving of the work into the everyday, its lack of formula. Discovered, not delivered. Offer: first month for $49 via the link below. Do not fabricate details about the class — use only what's provided above.

Leave a literal "[link]" placeholder where the membership offer link goes. Do not invent a URL.

Reference quality (Tiff's voice and tone — match this):
{_PH1_REFERENCE}

Hard limit: 250 words. Subject line included, not counted.
Shape: natural, not formulaic. Subject line, body, [link] on its own line, brief closing, sign-off. No headers or bullets."""


def assemble_ph2_prompt(overview: dict, plans: dict, year: int, month: int) -> str:
    """Prompt for the second post-Habit-class follow-up email (send +7 days)."""
    habit_date = get_habit_class_date(year, month)
    habit_str = habit_date.strftime("%B %-d")
    habit_plan = plans.get(habit_date.isoformat(), {})

    return f"""Write a second follow-up email for people who attended the Yoga Habit free class on {habit_str}.

Class context:
Title: {habit_plan.get('title', 'The Yoga Habit')}
Description: {habit_plan.get('description', '')}
Theme: {overview.get('title', '')} — {overview.get('teaching_notes', '')}

This email sends 7 days after class. The offer is still open but closing soon. The reader has had a week to think about it. Tone is gentle, non-pushy. A quiet reminder that the door is still open.

Goal: re-open the invitation to The Yoga Lifestyle. Match the reference's contemplative weave — the practice still alive in the week that's passed, the door still open, no urgency forced. Discovered, not delivered. Offer: first month for $49, closes soon. Leave a literal "[link]" placeholder where the membership offer link goes.

Reference quality (Tiff's voice and tone — match this):
{_PH2_REFERENCE}

Hard limit: 200 words. Subject line included, not counted.
Shape: natural, not formulaic. Subject line, body, [link] on its own line, brief closing, sign-off + P.S. No headers or bullets."""


def assemble_non_opener_prompt(overview: dict, plans: dict, year: int, month: int) -> str:
    """Prompt for the second-send outreach to people who didn't open the first non-member newsletter."""
    habit_date = get_habit_class_date(year, month)
    habit_str = habit_date.strftime("%B %-d")
    habit_plan = plans.get(habit_date.isoformat(), {})

    return f"""Write a brief outreach email for people who received the first non-member newsletter about the {habit_str} Yoga Habit class but did not open it.

These readers have NO prior context about this class. They did not see the first email. DO NOT write as a reminder. DO NOT use phrases like "still time," "last call," "don't forget," "just a reminder," or "Yoga Habit is coming up." Write as if introducing the class to them fresh.

The first send opened with: "If your practice has been feeling stuck... this is usually why. You're trying to open without support." Take a completely different angle. Different hook, different image, different way in. Do not reference the first email or the fact that the reader didn't open it.

Yoga Habit class details — use ONLY these. Do not invent, embellish, or omit:
Date/time: {habit_str} | {habit_plan.get('time', '')} MT | {habit_plan.get('duration', '')} min | Free on Zoom
Title: {habit_plan.get('title', '')}
Description: {habit_plan.get('description', '')}
Apex pose: {habit_plan.get('apex_pose', '')}

Include this exact markdown link verbatim — the renderer needs the markdown to produce a button:
[Register Here](https://habit.tiffanywoodyoga.com)

Write this as Tiff — short, warm, accessible, no yoga jargon. Use one specific concrete image grounded in the actual class content above (the apex pose, the physical work). Do not invent class details that aren't listed. One sentence of contemplative depth allowed, not required. Discovered, not delivered. End with the link. Sign Tiff.

Hard limit: 100 words. Subject line included, not counted.
Shape: natural, not formulaic. Subject line, 1-2 short paragraphs, [Register Here] link, sign-off. No headers or bullets."""


def assemble_reminder_prompt(overview: dict, plans: dict, year: int, month: int) -> str:
    """Prompt for the day-before reminder to people who registered for the Habit class."""
    habit_date = get_habit_class_date(year, month)
    habit_str = habit_date.strftime("%B %-d")
    habit_plan = plans.get(habit_date.isoformat(), {})

    return f"""Write a brief day-before reminder email for people who registered for the {habit_str} Yoga Habit class. The class is tomorrow.

This is a service email, not marketing. They've already committed. Job: warm "see you tomorrow" with practical info. Do NOT pitch. Do NOT include a register link — they're already registered. Do NOT invite them to bring a friend.

Yoga Habit class details — use ONLY these. Do not invent or embellish:
Date/time: {habit_str} | {habit_plan.get('time', '')} MT | {habit_plan.get('duration', '')} min | Free on Zoom
Title: {habit_plan.get('title', '')}
Apex pose: {habit_plan.get('apex_pose', '')}
Bring: yoga mat, 2 blocks, strap, blanket if you use one.

The Zoom link comes from their registration confirmation in Marvelous. Mention they can find it there if needed.

Write this as Tiff — short, warm, anticipating. One concrete image grounded in the class content (the apex pose, the physical work). No teaching essay. Just "here's tomorrow." Sign Tiff.

Hard limit: 80 words. Subject line included, not counted.
Shape: subject line, 1-2 short paragraphs, sign-off. No headers or bullets."""


def assemble_gentle_nudge_prompt(overview: dict, plans: dict, year: int, month: int) -> str:
    """Prompt for a soft day-before nudge to openers of the first newsletter who did not register."""
    habit_date = get_habit_class_date(year, month)
    habit_str = habit_date.strftime("%B %-d")
    habit_plan = plans.get(habit_date.isoformat(), {})

    return f"""Write a very brief, soft nudge for people who opened the first non-member newsletter about the {habit_str} Yoga Habit class but did not register. The class is tomorrow.

They have already seen the pitch. They know what it is about. DO NOT repeat the case for the class. DO NOT manufacture urgency. DO NOT be pushy. The point of this email is just to circle back gently — in case they meant to register and forgot. If they decided not to come, that is also fine.

Class details — use sparingly, just for context:
Date/time: {habit_str} | {habit_plan.get('time', '')} MT | {habit_plan.get('duration', '')} min | Free on Zoom

Include this exact markdown link verbatim — the renderer needs the markdown to produce a button:
[Register Here](https://habit.tiffanywoodyoga.com)

Write this as Tiff — short, soft, one breath. No yoga jargon. No new pitch. No "still time" or "last chance" pressure. Acknowledge gently. End with the link. Sign Tiff.

Hard limit: 60 words. Subject line included, not counted.
Shape: subject line, 1 short paragraph, [Register Here] link, sign-off. No headers or bullets."""
