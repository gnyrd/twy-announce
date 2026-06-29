"""
Prompt assembly for newsletter generation.
Builds fully populated prompt text for Tweee (members + non-members).
"""
import re
import calendar
from datetime import date
import sys
import requests

CLASSES_API = "http://localhost:5003"

_PERSPECTIVE = """PERSPECTIVE (governs everything below):
Never write from the curriculum. Always write from the student's lived experience. The curriculum informs the email but should rarely be described directly. Introduce the month's philosophy through relatable life experiences before connecting it to the practices students will explore in class.

Answer "why does this matter in my life?", never "what is Tiffany teaching?" The overview, teaching notes, UPAs, apex pose, and teaching lens are TEACHER'S NOTES: they tell you what the month is about so you can write from the student's lived experience. Never quote, transcribe, or describe them as curriculum in the body."""

_HARD_RULES = """RULES (mandatory, no exceptions). Read first, follow without exception:
1. Output ONLY a subject line and a body. NO markdown headers anywhere -- no `#`, no `##`, no decorative title line above or inside the body. The body is flowing prose, not a document with sections. The submission API rejects any body that begins with `#`.
2. The Theme title below is the canonical theme. Use it EXACTLY in the SUBJECT line. Do NOT invent a different theme name, layer a poetic umbrella over it, or substitute a parallel framing. In the body the theme is the silent throughline, not a stated heading and not a described topic.
3. Do NOT introduce outside content -- no CHANI astrology references, no invented season names, no umbrella themes from external sources. The astrology disclaimer (where present below) is the only authorization to mention astrology, and only as a felt quality with no planet names, dates, or events.

4. No em-dashes, no en-dashes, and no semicolons anywhere in your output, in the subject or the body. They are banned from TWY copy, even when the reference exemplars below use them. Replace each with a period and a new sentence, a comma, parentheses, or a reworded phrase. A plain hyphen joining words is fine. The long em-dash and medium en-dash characters and the semicolon are not.

These rules apply to ALL output. The submission API will reject violations of rule 1 and you will be asked to resubmit."""

_VOICE_GUARDRAILS = """VOICE NOTES (guidance, not rigid rules):

The reference exemplars at the bottom of this prompt are the strongest signal of Tiff's voice. When this guidance feels in tension with the references, follow the references. The point of these notes is to flag Tweee's specific defaults that Tiff has named; the point of the references is to show the breadth of her actual voice.

Four Tweee defaults Tiff has called out (try to avoid these specific patterns):
- Performance tropes she said don't sound like her: "tired of performing", "tired of tightening", "tired of waiting to feel ready", "taking up space" (used as a contrast payoff). Her words: "I'm not sure I actually speak this way."
- Rhetorical-question openers in the "What if X?" / "Have you ever Y?" hook-style shape. Tiff doesn't open this way. Her words: "structurally clickbait even though the intention was thoughtful."
- Generic somatic-marketing language without anatomical specificity: "the body realizes it doesn't have to force anything", "your body finally trusts itself", "let yourself bloom". When you write a sentence about "the body" abstractly without naming what's actually happening (which body region, which action, which UPA), it tends to land as generic yoga-marketing. Her words: "I'm so tired of this message I could scream."
- Curriculum / teacher-training language lifted from the lesson plan: naming UPAs, "loops", "Muscular and Organic Energy", apex poses, or Sanskrit principles as the email's subject matter (e.g. "Teach the loops as living relationships", "Every relationship is an expression of Spanda"). Wonderful inside a class, wrong in a newsletter. The teaching notes inform the email. Their vocabulary never appears in it. Her words: "It sounds like teacher training... they want to know: why should I be excited to come to class this month?"

Patterns Tiff uses (a sampler, not a checklist -- she draws from these and others, varies them across emails and across months):

- DIRECT DECLARATIVE OPENERS (every single email): "June feels like a long exhale." / "Lately I've been reflecting on..." / "Just sending this back around in case you missed it." / "Looking forward to practicing with you tomorrow for..." / "Thank you for being part of..." / "A week later, I'm still thinking about..." / "Just gently circling back in case..." Tiff never opens with abstraction or question.

- FIRST-PERSON REFLECTION: "I've been thinking a lot about how confidence changes as we change." / "I keep thinking about how rare it is to feel supported enough to let ourselves be seen." / "Lately I've been reflecting on how meaningful joy becomes when it's shared." Personal voice grounds the teaching.

- ANATOMICAL / PRACTICE SPECIFICITY (the substance of yoga marketing): "side-body opening, supported backbending, expansive heart-opening flow", "grounding actions in the legs and pelvis", "rooting before we radiate", "supported heart opening, fluid movement, and spacious backbending". Name the practice, name the body region. Body language without this specificity is forbidden (see above).

- PARALLEL STRUCTURE / ANAPHORA: "A little more space in the body. A little more willingness to be seen as we are. A little more trust in what's unfolding..." / "Even when life gets loud again. Even when we forget for a while." / "We practice by participating. By breathing. By offering our presence sincerely." Repetition of an opening phrase across short clauses. Tight, three-beat is common.

- Negation ladder + arrival (one of her moves, used sparingly): "not performing, not pushing, just allowing the body to participate honestly" / "Not perfected. Not 'fixed.' Just present." Two-or-three short negations of specific misframings, landing on one short positive. Used sparingly. Distinct from a contrast pair -- it strips away wrong framings to arrive at one truth, not "A vs B."

- SANSKRIT / PHILOSOPHICAL REFERENCES with a one-sentence gloss: "Purna, the practice of recognizing inherent wholeness, reminds us that we do not need to perfect ourselves before we belong here." Term, definition, ground.

- ONE-LINE PARAGRAPHS and short sentence rhythm. Frequent throughout her writing. Don't pack everything into long paragraphs.

- WARM ADDRESS FORMS: "Hi sweethearts" (members) / "Hi loves" / "Hi there" (non-members) / "Hi" (post-class follow-ups). Sign-offs: "With love, Tiff" / "Love, Tiff" / a simple "Tiff".

The point: VARIETY. Do not lean on any single technique. Do not repeat the same move within an email (e.g. two negation ladders in one body = too much). If a technique is starting to feel like the structural backbone of the email, vary it.

CONTRAST PAIRS specifically ("Not because X, but because Y" / "X instead of Y") -- MINIMIZE. Tiff uses these rarely and tightly. Her June 2026 entire output contained one full "Not because X but because Y" contrast pair (in PH1: "Not because we need to become better versions of ourselves. But because we forget.") and one four-word qualifier ("the kind that feels nourishing instead of forced" in the reminder). Default: do NOT use a contrast pair. If one is genuinely needed: both sides short, grounded in lived specificity, never as the opener, never more than one per email.

LOGISTICS AND ANNOUNCEMENTS: weave any practical note (a class pause, a schedule change, a new offering) into the conversation in Tiff's voice. Never drop it as a bare customer-service line. "There's no free Yoga Habit class this month" reads like a service desk. "I'm taking a little pause from Yoga Habit in July, and we'll be back together in August" is Tiff. A genuinely exciting new offering earns a warm featured paragraph that makes people want to come, not a tacked-on mention.

PREFERRED voice:
- Direct, declarative openers. Audience-specific positive opener patterns are baked into each assembler's Shape line. The REFERENCE block at the bottom of this prompt is the most recent example of Tiff's voice for this audience -- match the VOICE, not the specific content. Different month, different theme, same shape.
- Conversational sentence length. One-line paragraphs are normal and frequent.
- First-person reflection is welcome ("Lately I've been reflecting on...", "I keep thinking about how...", "I've been thinking a lot about...").

Subject line:
- Subject is a separate top-level field from the body. The API enforces this (no H1 headers in body).
- Tiff's subjects tend to be descriptive or declarative -- short, not hook-style. Examples she has written or kept: "June Yoga Lifestyle -- Creative Confidence" (theme-stamped), "Get Ready to Smile: June's Heart Yoga" (descriptive friendly), "Open, but not unprotected" (felt-quality), "A softer way to open" (low-key), "Open to Camel" (functional/specific), "In case you meant to join us" (gentle), "See you tomorrow" (functional), "Thank you for practicing with me" (direct), "How did it land?" (curious-question, conversational), "Your center is still there" (declarative), "Don't lose this" (urgent-direct).
- One subject style she explicitly rejected: "What if your practice stopped shrinking you?" -- the rhetorical-question hook shape."""








# May 2026 exemplars (sent campaigns -- Tiff's final edited content).
# Paired with June refs below so Tweee sees variation across months.

# References are read DYNAMICALLY from disk at prompt-build time -- see
# _format_recent_references() below. The .md files in /root/twy/data/newsletters/YYYY-MM/
# are overwritten with each month's actually-sent content by the diff-loop
# archival step in generate_newsletter_prompts.py. The assemblers embed the
# N most recent months' content as voice references.

from pathlib import Path
from twy_paths import newsletters_dir as _newsletters_dir

_NEWSLETTERS_DIR = _newsletters_dir()
_MONTH_DIR_RE = re.compile(r'^\d{4}-\d{2}$')


def _format_recent_references(audience: str, count: int = 2) -> str:
    """Read the N most recent sent .md files for this audience from disk and
    return a formatted reference block ready to interpolate into a prompt.

    Returns empty string if no .md files exist yet.
    """
    audience_us = audience.replace('-', '_')
    refs = []
    if not _NEWSLETTERS_DIR.exists():
        return ""
    for month_dir in sorted(_NEWSLETTERS_DIR.iterdir(), reverse=True):
        if not month_dir.is_dir() or not _MONTH_DIR_RE.match(month_dir.name):
            continue
        md_file = month_dir / f"{audience_us}.md"
        if not md_file.exists():
            continue
        text = md_file.read_text()
        # Strip the H1 subject line; keep just the body
        lines = text.split("\n", 2)
        body = lines[2].strip() if len(lines) > 2 else ""
        if not body:
            continue
        refs.append((month_dir.name, body))
        if len(refs) >= count:
            break
    if not refs:
        return ""
    label = "REFERENCE" if len(refs) == 1 else f"REFERENCES ({len(refs)} months)"
    intro = (
        f"{label} -- Tiff's actual sent newsletter(s) for this audience from "
        f"the most recent month(s). Match her VOICE and her VARIETY, NOT the "
        f"specific content (theme, dates, class title) of any single example:"
    )
    blocks = "\n\n".join(f"--- {label} ---\n{body}" for label, body in refs)
    return f"{intro}\n\n{blocks}"


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

    recent_refs = _format_recent_references("lifestyle")

    return f"""Write a member newsletter for Tiffany Wood Yoga.

{_PERSPECTIVE}

{_HARD_RULES}

{_VOICE_GUARDRAILS}

Month: {habit_date.strftime('%B %Y')}
Theme title (SUBJECT line only, silent throughline in the body): {overview.get('title', '')}

TEACHER'S NOTES (write FROM these, never quote or describe them in the body):
- What the month is about: {overview.get('teaching_notes', '')}
- Physical arc: {physical_arc}
- Apex pose: {apex_pose}
- UPAs: {upas}
- Member affirmation: "{affirmation}"
- Teaching lens: {teaching_lens}

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

Arc (the shape Tiff's best lifestyle emails follow): open from a moment of ordinary life the reader recognizes, name the recognition, offer a short personal reflection or observation, only THEN connect it to the practice and the month, and close with the invitation. Life, then recognition, then story, then yoga, then invitation. Lead from life. The yoga arrives once they already feel why it matters.

OUTPUT TOKENS — use these LITERAL strings in your output. They are substituted at send time:
- {{CLASS_TITLE}}    — write this token wherever you reference the Yoga Habit class title. Do NOT write the literal title text. Substitutes to a linked title pointing at the class registration page.
- {{REGISTER_CTA}}   — place on its own paragraph (nothing else on that line) after the body, before the sign-off. Substitutes to a styled Register button.

Hard limit: 300 words. Subject line included, not counted.
Shape: natural, not formulaic. Subject line, body that flows, event details (using {{CLASS_TITLE}} where the title goes), {{REGISTER_CTA}} alone on a line, sign-off. Tiff's lifestyle openers tend to ground the theme in something lived or felt rather than abstract -- direct declarative shape, not a rhetorical question and not a long abstract contrast. The reference exemplars below show this. The API will reject any body that begins with a `#` markdown header (no H1, no umbrella title above the body). The theme name belongs in the subject line, not as a line in the body. No bullets except for event details.

{recent_refs}"""


def assemble_non_lifestyle_prompt(overview: dict, plans: dict, year: int, month: int) -> str:
    habit_date = get_habit_class_date(year, month)
    habit_str = habit_date.strftime("%B %-d")
    habit_plan = plans.get(habit_date.isoformat(), {})

    recent_refs = _format_recent_references("non-lifestyle")

    return f"""Write an open-door newsletter for people who aren't Tiffany Wood Yoga members.

{_PERSPECTIVE}

{_HARD_RULES}

{_VOICE_GUARDRAILS}

Theme title (SUBJECT line only, silent throughline in the body): {overview.get('title', '')}

TEACHER'S NOTES (write FROM these, never quote or describe them in the body): {overview.get('teaching_notes', '')}

Yoga Habit class -- this is what you're inviting them to. Use ONLY these details. Do not invent or embellish:
Date/time: {habit_str} | {habit_plan.get('time', '')} MT | {habit_plan.get('duration', '')} min | Free on Zoom
Title: {habit_plan.get('title', '')}
Description: {habit_plan.get('description', '')}
Apex pose: {habit_plan.get('apex_pose', '')}
Physical arc: {habit_plan.get('physical_arc', '')}
Props: {habit_plan.get('props', '')}
For people with an established practice who want to deepen it. Not a beginner class. Free on Zoom.

Write this as Tiff — warm, accessible, no yoga jargon. This person is on the fence. They're curious, or tired, or overdue. One thing is happening. One reason to come. One clear ask: register. Even in this short form, open from something they recognize in their own life before you name the class. Life, then recognition, then invitation. You can gesture at the deeper why ONCE, briefly — a single sentence that lets the depth show without requiring belief or vocabulary. Discovered, not explained.

OUTPUT TOKENS — use these LITERAL strings in your output. They are substituted at send time:
- {{CLASS_TITLE}}    — write this token wherever you reference the Yoga Habit class title. Do NOT write the literal title text. Substitutes to a linked title pointing at the habit.tiffanywoodyoga.com landing page.
- {{REGISTER_CTA}}   — place on its own paragraph (nothing else on that line) where the Register CTA belongs. Substitutes to a styled Register button.
- {{CALENDAR_CTA}}   — place on its own paragraph (nothing else on that line), after {{REGISTER_CTA}}. Substitutes to a styled "Subscribe to the Habits calendar" button. This invites them to subscribe to the Habits-only calendar feed so they never miss a class.

Do NOT write literal URLs. Do NOT write [Register Here](url). Use the tokens.

Hard limit: 175 words. Subject line included, not counted.
Shape: natural, not formulaic. Subject line, body, event details (using {{CLASS_TITLE}} where the title goes), {{REGISTER_CTA}} alone on a line, {{CALENDAR_CTA}} alone on a line, sign-off. Tiff's non-member openers tend to land directly on what the class is, often something like "This month's Yoga Habit class, {{CLASS_TITLE}}, explores [actual class subject]" or "This month's free Yoga Habit class, {{CLASS_TITLE}}, is a [practice description] centered around [specific details]" -- direct and specific. She also opens with first-person reflection sometimes ("Lately I've been reflecting on..."). The references below show both shapes. Avoid rhetorical-question hooks. The API will reject any body that begins with `#`. The theme name belongs in the subject line, not as a line in the body. No bullets.

{recent_refs}"""


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

    recent_refs = _format_recent_references("ph1")

    return f"""Write a follow-up email to people who attended the Yoga Habit free class on {habit_str}.

{_PERSPECTIVE}

{_HARD_RULES}

{_VOICE_GUARDRAILS}

Class context:
Title: {habit_plan.get('title', 'The Yoga Habit')}
Description: {habit_plan.get('description', '')}
Theme: {overview.get('title', '')} — {overview.get('teaching_notes', '')}

This email sends 24 hours after class ends. The reader just practiced with Tiff for the first time (or returned after a gap). They're in the afterglow.

Goal: contemplative thank-you that weaves the practice into life and naturally opens into an invitation to continue inside The Yoga Lifestyle. Match the reference's non-dual undertone, its weaving of the work into the everyday, its lack of formula. Discovered, not delivered. Offer: first month for $49. Do not fabricate details about the class — use only what's provided above.

Reference quality (Tiff's voice and tone -- match this). The synthetic template baseline plus Tiff's most recent sent examples:

--- TEMPLATE (synthetic baseline) ---
{_PH1_REFERENCE}

{recent_refs}

OUTPUT TOKENS — use these LITERAL strings in your output. They are substituted at send time:
- {{CLASS_TITLE}}    — write this token wherever you reference the Yoga Habit class title (e.g. in the thank-you for the class they just attended). Do NOT write the literal title text.
- [link]             — write the offer link as an INLINE text link using markdown: `[Claim your first month for $49]([link])` or `[start your membership]([link])` (pick a phrase that fits the prose). Place the link INLINE within a sentence — do NOT put it on its own paragraph. Inline placement keeps it a text link; a paragraph alone would render as a button which is not what we want for PH1/PH2.

The literal `[link]` placeholder gets substituted with the coupon checkout URL at send time. Do not invent a URL.

Hard limit: 250 words. Subject line included, not counted.
Shape: natural, not formulaic. Subject line, body (using {{CLASS_TITLE}} where the title appears and an inline `[…]([link])` markdown link in prose), brief closing, sign-off. PH1 openers tend to be a direct thank-you, sometimes paired with a conversational question or invitation to reflect ("I'm curious -- what did you feel?"). The references show variation. The API rejects any body beginning with `#`. Place the offer link inline (no button-style link on its own paragraph; that's the lifestyle/non-lifestyle shape, not the follow-up shape). No bullets."""


def assemble_ph2_prompt(overview: dict, plans: dict, year: int, month: int) -> str:
    """Prompt for the second post-Habit-class follow-up email (send +7 days)."""
    habit_date = get_habit_class_date(year, month)
    habit_str = habit_date.strftime("%B %-d")
    habit_plan = plans.get(habit_date.isoformat(), {})

    recent_refs = _format_recent_references("ph2")

    return f"""Write a second follow-up email for people who attended the Yoga Habit free class on {habit_str}.

{_PERSPECTIVE}

{_HARD_RULES}

{_VOICE_GUARDRAILS}

Class context:
Title: {habit_plan.get('title', 'The Yoga Habit')}
Description: {habit_plan.get('description', '')}
Theme: {overview.get('title', '')} — {overview.get('teaching_notes', '')}

This email sends 7 days after class. The offer is still open but closing soon. The reader has had a week to think about it. Tone is gentle, non-pushy. A quiet reminder that the door is still open.

Goal: re-open the invitation to The Yoga Lifestyle. Match the reference's contemplative weave — the practice still alive in the week that's passed, the door still open, no urgency forced. Discovered, not delivered. Offer: first month for $49, closes soon.

Reference quality (Tiff's voice and tone -- match this). The synthetic template baseline plus Tiff's most recent sent examples:

--- TEMPLATE (synthetic baseline) ---
{_PH2_REFERENCE}

{recent_refs}

OUTPUT TOKENS — use these LITERAL strings in your output. They are substituted at send time:
- {{CLASS_TITLE}}    — write this token wherever you reference the Yoga Habit class title (e.g. in the P.S. about the next class, or referencing what they just practiced). Do NOT write the literal title text.
- [link]             — write the offer link as an INLINE text link using markdown: `[start your membership]([link])` or `[claim your first month for $49]([link])` (pick a phrase that fits the prose). Place the link INLINE within a sentence — do NOT put it on its own paragraph. Inline placement keeps it a text link.

The literal `[link]` placeholder gets substituted with the coupon checkout URL at send time. Do not invent a URL.

Hard limit: 200 words. Subject line included, not counted.
Shape: natural, not formulaic. Subject line, body (using {{CLASS_TITLE}} where the title appears and an inline `[…]([link])` markdown link in prose), brief closing, sign-off + P.S. PH2 openers tend to be reflective, opening on the time elapsed and a felt observation ("A week later, I'm still thinking about..." / "A week later... this is where it usually fades."). The references show variation. Inline offer link, no button-style link on its own paragraph. The API rejects any body beginning with `#`. No bullets."""


def assemble_non_opener_prompt(overview: dict, plans: dict, year: int, month: int) -> str:
    """Prompt for the second-send outreach to people who didn't open the first non-member newsletter."""
    habit_date = get_habit_class_date(year, month)
    habit_str = habit_date.strftime("%B %-d")
    habit_plan = plans.get(habit_date.isoformat(), {})

    recent_refs = _format_recent_references("non-opener")

    return f"""Write a brief outreach email for people who received the first non-member newsletter about the {habit_str} Yoga Habit class but did not open it.

{_PERSPECTIVE}

{_HARD_RULES}

{_VOICE_GUARDRAILS}

These readers have NO prior context about this class. They did not see the first email. DO NOT write as a reminder. DO NOT use phrases like "still time," "last call," "don't forget," "just a reminder," or "Yoga Habit is coming up." Write as if introducing the class to them fresh.

The first send opened with: "If your practice has been feeling stuck... this is usually why. You're trying to open without support." Take a completely different angle. Different hook, different image, different way in. Do not reference the first email or the fact that the reader didn't open it.

Yoga Habit class details — use ONLY these. Do not invent, embellish, or omit:
Date/time: {habit_str} | {habit_plan.get('time', '')} MT | {habit_plan.get('duration', '')} min | Free on Zoom
Title: {habit_plan.get('title', '')}
Description: {habit_plan.get('description', '')}
Apex pose: {habit_plan.get('apex_pose', '')}

Write this as Tiff — short, warm, accessible, no yoga jargon. Use one specific concrete image grounded in the actual class content above (the apex pose, the physical work). Do not invent class details that aren't listed. One sentence of contemplative depth allowed, not required. Discovered, not delivered. Sign Tiff.

OUTPUT TOKENS — use these LITERAL strings in your output. They are substituted at send time:
- {{CLASS_TITLE}}    — write this token wherever you reference the Yoga Habit class title. Do NOT write the literal title text. Substitutes to a linked title pointing at the habit.tiffanywoodyoga.com landing page.
- {{REGISTER_CTA}}   — place on its own paragraph (nothing else on that line) where the Register CTA belongs. Substitutes to a styled Register button.
- {{CALENDAR_CTA}}   — place on its own paragraph (nothing else on that line), after {{REGISTER_CTA}}. Substitutes to a styled "Subscribe to the Habits calendar" button.

Do NOT write literal URLs. Do NOT write [Register Here](url). Use the tokens.

Hard limit: 100 words. Subject line included, not counted.
Shape: natural, not formulaic. Subject line, 1-2 short paragraphs (using {{CLASS_TITLE}} where the title goes), {{REGISTER_CTA}} alone on a line, {{CALENDAR_CTA}} alone on a line, sign-off. Resend openers tend to be functional and low-key ("Just sending this back around in case you missed it." is one shape she uses; she also opens with a single concrete somatic moment, e.g. "There's a moment in Camel where the thighs press forward, the legs root down..."). The references below show both. Avoid a fresh hook on a resend -- the original framing already exists. The API will reject any body that begins with `#`. No bullets.

{recent_refs}"""


def assemble_reminder_prompt(overview: dict, plans: dict, year: int, month: int) -> str:
    """Prompt for the day-before reminder to people who registered for the Habit class."""
    habit_date = get_habit_class_date(year, month)
    habit_str = habit_date.strftime("%B %-d")
    habit_plan = plans.get(habit_date.isoformat(), {})

    recent_refs = _format_recent_references("reminder")

    return f"""Write a brief day-before reminder email for people who registered for the {habit_str} Yoga Habit class. The class is tomorrow.

{_PERSPECTIVE}

{_HARD_RULES}

{_VOICE_GUARDRAILS}

This is a service email, not marketing. They've already committed. Job: warm "see you tomorrow" with practical info. Do NOT pitch. Do NOT invite them to bring a friend. Do NOT include a Register CTA button — they are already registered.

Yoga Habit class details — use ONLY these. Do not invent or embellish:
Date/time: {habit_str} | {habit_plan.get('time', '')} MT | {habit_plan.get('duration', '')} min | Free on Zoom
Title: {habit_plan.get('title', '')}
Apex pose: {habit_plan.get('apex_pose', '')}
Bring: yoga mat, 2 blocks, strap, blanket if you use one.

The Zoom link comes from their registration confirmation in Marvelous. Mention they can find it there if needed.

Write this as Tiff — short, warm, anticipating. One concrete image grounded in the class content (the apex pose, the physical work). No teaching essay. Just "here's tomorrow." Sign Tiff.

OUTPUT TOKENS — use these LITERAL strings in your output. They are substituted at send time:
- {{CLASS_TITLE}}    — write this token wherever you reference the Yoga Habit class title. Do NOT write the literal title text. Substitutes to a linked title pointing at the class page (they are registered, so this goes direct to the class).
- {{CLASS_URL}}      — write inline within a sentence as part of a markdown link, e.g. `[See you tomorrow]({{CLASS_URL}})`. Substitutes to the class URL. INLINE placement is required — do NOT put the resulting markdown link on its own paragraph (that would make it a button; we want a text link).

Hard limit: 80 words. Subject line included, not counted.
Shape: subject line, 1-2 short paragraphs (using {{CLASS_TITLE}} where the title goes), inline `[See you tomorrow]({{CLASS_URL}})` at the end of a sentence, sign-off. Reminder openers tend to be warm and functional -- something like "Looking forward to practicing with you tomorrow for {{CLASS_TITLE}}." The June reference below shows this shape. The API rejects any body beginning with `#`. No bullets. No Register button (this audience has already registered).

{recent_refs}"""


def assemble_gentle_nudge_prompt(overview: dict, plans: dict, year: int, month: int) -> str:
    """Prompt for a soft day-before nudge to openers of the first newsletter who did not register."""
    habit_date = get_habit_class_date(year, month)
    habit_str = habit_date.strftime("%B %-d")
    habit_plan = plans.get(habit_date.isoformat(), {})

    recent_refs = _format_recent_references("gentle-nudge")

    return f"""Write a very brief, soft nudge for people who opened the first non-member newsletter about the {habit_str} Yoga Habit class but did not register. The class is tomorrow.

{_PERSPECTIVE}

{_HARD_RULES}

{_VOICE_GUARDRAILS}

They have already seen the pitch. They know what it is about. DO NOT repeat the case for the class. DO NOT manufacture urgency. DO NOT be pushy. The point of this email is just to circle back gently — in case they meant to register and forgot. If they decided not to come, that is also fine.

Class details — use sparingly, just for context:
Date/time: {habit_str} | {habit_plan.get('time', '')} MT | {habit_plan.get('duration', '')} min | Free on Zoom
Title: {habit_plan.get('title', '')}

Write this as Tiff — short, soft, one breath. No yoga jargon. No new pitch. No "still time" or "last chance" pressure. Acknowledge gently. Sign Tiff.

OUTPUT TOKENS — use these LITERAL strings in your output. They are substituted at send time:
- {{CLASS_TITLE}}    — write this token wherever you reference the Yoga Habit class title. Do NOT write the literal title text. Substitutes to a linked title pointing at the habit.tiffanywoodyoga.com landing page.
- {{REGISTER_CTA}}   — place on its own paragraph (nothing else on that line) at the end. Substitutes to a styled Register button.
- {{CALENDAR_CTA}}   — place on its own paragraph after {{REGISTER_CTA}}. Substitutes to a styled "Subscribe to the Habits calendar" button (gentle alternative if they cannot make it this time).

Do NOT write literal URLs. Do NOT write [Register Here](url). Use the tokens.

Hard limit: 60 words. Subject line included, not counted.
Shape: subject line, 1 short paragraph (using {{CLASS_TITLE}} if you reference the class title), {{REGISTER_CTA}} alone on a line, {{CALENDAR_CTA}} alone on a line, sign-off. Gentle Nudge openers tend to be very low-key and one-paragraph -- "Just gently circling back in case [CLASS_TITLE] was something you meant to register for." and similar. The references below show this. Short. The API rejects any body beginning with `#`. No bullets.

{recent_refs}"""
