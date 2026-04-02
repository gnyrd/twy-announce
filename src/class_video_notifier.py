#!/usr/bin/env python3
"""Post Slack notifications to #video-status as class recordings hit milestones."""

import json
import os
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

import requests
from dotenv import load_dotenv
from twy_paths import load_env
from twy_classplan import load_plan as _load_plan

load_env()

CLASSES_DIR = Path("/root/twy/data/classes")
STATE_FILE      = Path(__file__).parent.parent / "state/class_video_notifications.json"
WEBHOOK_URL     = os.getenv("SLACK_VIDEO_WEBHOOK_URL")
TRIM_BASE_URL   = "https://classes.tiffanywood.yoga/trim"
SCAN_DAYS       = 30
DATE_RE         = re.compile(r"^(\d{4}-\d{2}-\d{2})")


def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {}


def save_state(state: dict):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2))


def load_class_plan(date_iso: str) -> dict | None:
    return _load_plan(date_iso)


def class_display_name(date_iso: str, plan: dict | None) -> str:
    if plan and plan.get("title"):
        return plan["title"]
    return date_iso


def format_date(date_iso: str) -> str:
    dt = datetime.strptime(date_iso, "%Y-%m-%d")
    return dt.strftime("%b %-d")


def check_milestones(class_dir: Path, date_iso: str) -> dict:
    zoom = class_dir / "zoom_files"
    video_ready = (zoom / "shared_screen_with_speaker_view.mp4").exists()
    transcript_ready = (zoom / "audio_transcript.vtt").exists()
    ready_to_edit = video_ready and transcript_ready

    ranking_file = class_dir / "class_thumbnails" / "thumbnail_ranking.json"
    thumbnails_ready = ranking_file.exists()

    thumbnail_best = None
    if thumbnails_ready:
        try:
            ranking = json.loads(ranking_file.read_text())
            best_file = ranking.get("best", "")
            top = next((r for r in ranking.get("ranked", []) if r.get("thumbnail") == best_file), None)
            if top:
                # Extract time from filename: thumbnail_YYYY-MM-DD_MM_SS.png
                m = re.search(r"_(\d+)_(\d+)\.png$", best_file)
                if m:
                    mins, secs = int(m.group(1)), int(m.group(2))
                    thumbnail_best = f"{mins}:{secs:02d}"
        except Exception:
            pass

    plan = load_class_plan(date_iso)
    posted_to_marvelous = bool(plan and plan.get("marvelous_media_id"))

    return {
        "ready_to_edit": ready_to_edit,
        "thumbnails_ready": thumbnails_ready,
        "thumbnail_best": thumbnail_best,
        "posted_to_marvelous": posted_to_marvelous,
        "plan": plan,
    }


def post_to_slack(text: str):
    if not WEBHOOK_URL:
        raise ValueError("SLACK_VIDEO_WEBHOOK_URL not set")
    resp = requests.post(WEBHOOK_URL, json={"text": text},
                         headers={"Content-Type": "application/json"})
    resp.raise_for_status()


def thumb_line(milestones: dict) -> str:
    if milestones["thumbnails_ready"] and milestones["thumbnail_best"]:
        return f"Thumbnails: ✓ best: {milestones['thumbnail_best']}"
    elif milestones["thumbnails_ready"]:
        return "Thumbnails: ✓ ready"
    else:
        return "Thumbnails: not yet ready"


def build_notifications(class_slug: str, date_iso: str, milestones: dict, sent: dict) -> list[str]:
    name = class_display_name(date_iso, milestones["plan"])
    date_fmt = format_date(date_iso)
    messages = []

    if milestones["thumbnails_ready"] and not sent.get("thumbnails_ready"):
        msg = f"🖼 *{name}* ({date_fmt}) — thumbnails ready"
        messages.append(("thumbnails_ready", msg))

    if milestones["posted_to_marvelous"] and not sent.get("posted_to_marvelous"):
        msg = f"✅ *{name}* ({date_fmt}) — posted to Marvelous"
        messages.append(("posted_to_marvelous", msg))

    return messages
def prune_state(state: dict) -> dict:
    cutoff = (datetime.now() - timedelta(days=SCAN_DAYS)).strftime("%Y-%m-%d")
    return {k: v for k, v in state.items() if k[:10] >= cutoff}


def main():
    if not WEBHOOK_URL:
        print("ERROR: SLACK_VIDEO_WEBHOOK_URL not set in .env")
        return 1

    state = load_state()
    cutoff = datetime.now() - timedelta(days=SCAN_DAYS)
    errors = 0

    for class_dir in sorted(CLASSES_DIR.iterdir()):
        if not class_dir.is_dir():
            continue
        m = DATE_RE.match(class_dir.name)
        if not m:
            continue
        date_iso = m.group(1)
        if datetime.strptime(date_iso, "%Y-%m-%d") < cutoff:
            continue

        slug = class_dir.name
        sent = state.get(slug, {})
        milestones = check_milestones(class_dir, date_iso)
        notifications = build_notifications(slug, date_iso, milestones, sent)

        for milestone_key, message in notifications:
            if message is None:
                # Mark as noted but don't post (will appear in ready_to_edit message)
                sent[milestone_key] = True
                continue
            try:
                post_to_slack(message)
                print(f"  ✓ {slug} — {milestone_key}")
                sent[milestone_key] = True
            except Exception as e:
                print(f"  ✗ {slug} — {milestone_key}: {e}")
                errors += 1

        if sent:
            state[slug] = sent

    state = prune_state(state)
    save_state(state)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
