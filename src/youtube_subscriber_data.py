#!/usr/bin/env python3
"""Fetch and save YouTube subscriber data using the YouTube Data API."""

import json
import os
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
import requests

# Determine repo root from script location
SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent

# Load environment variables
load_dotenv(REPO_ROOT / ".env")

# Configuration (with env var overrides)
YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY")
YOUTUBE_CHANNEL_ID = os.getenv("YOUTUBE_CHANNEL_ID")
YOUTUBE_HISTORY_DIR = Path(os.getenv(
    "YOUTUBE_HISTORY_DIR",
    REPO_ROOT / "data/youtube/history"
))


def get_youtube_subscriber_count() -> dict:
    """Fetch subscriber count from YouTube using the Data API."""
    print("Fetching YouTube subscriber data...")
    
    if not YOUTUBE_API_KEY:
        raise Exception("YOUTUBE_API_KEY environment variable not set")
    if not YOUTUBE_CHANNEL_ID:
        raise Exception("YOUTUBE_CHANNEL_ID environment variable not set")
    
    url = "https://www.googleapis.com/youtube/v3/channels"
    params = {
        "part": "statistics",
        "id": YOUTUBE_CHANNEL_ID,
        "key": YOUTUBE_API_KEY
    }
    
    try:
        response = requests.get(url, params=params)
        response.raise_for_status()
        data = response.json()
        
        if not data.get("items"):
            raise Exception(f"Channel not found: {YOUTUBE_CHANNEL_ID}")
        
        stats = data["items"][0]["statistics"]
        subscriber_count = int(stats.get("subscriberCount", 0))
        view_count = int(stats.get("viewCount", 0))
        video_count = int(stats.get("videoCount", 0))
        
        print(f"✓ Fetched subscriber count: {subscriber_count}")
        return {
            "subscriber_count": subscriber_count,
            "view_count": view_count,
            "video_count": video_count
        }
    except requests.RequestException as e:
        raise Exception(f"Failed to fetch YouTube data: {e}")


def save_youtube_snapshot(stats: dict, date: str):
    """Save daily YouTube subscriber data to history."""
    YOUTUBE_HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    
    snapshot = {
        "date": date,
        "timestamp": datetime.now().isoformat(),
        **stats
    }
    
    filepath = YOUTUBE_HISTORY_DIR / f"{date}.json"
    with open(filepath, "w") as f:
        json.dump(snapshot, f, indent=2)
    
    print(f"✓ Saved YouTube snapshot to {filepath}")


def main():
    """Main entry point."""
    print("=" * 60)
    print("YouTube Subscriber Data")
    print("=" * 60)
    
    today = datetime.now().strftime("%Y-%m-%d")
    
    try:
        stats = get_youtube_subscriber_count()
        save_youtube_snapshot(stats, today)
        print(f"\n✓ YouTube data saved successfully")
        return 0
    except Exception as e:
        print(f"\n✗ Error: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
