#!/usr/bin/env python3
"""Fetch and save Instagram follower data using instaloader."""

import json
import os
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
import instaloader

# Determine repo root from script location
SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent

# Load environment variables
load_dotenv(REPO_ROOT / ".env")

# Configuration (with env var overrides)
TARGET_PROFILE = os.getenv("INSTAGRAM_PROFILE", "tiffanywoodyoga")
INSTAGRAM_HISTORY_DIR = Path(os.getenv(
    "INSTAGRAM_HISTORY_DIR",
    REPO_ROOT / "data/instagram/history"
))
SESSION_FILE = Path(os.getenv(
    "INSTAGRAM_SESSION_FILE",
    Path.home() / f".config/instaloader/session-{TARGET_PROFILE}"
))


def get_instagram_follower_count() -> int:
    """Fetch follower count from Instagram using instaloader."""
    print("Fetching Instagram follower data...")
    try:
        L = instaloader.Instaloader()
        
        # Load session file
        if SESSION_FILE.exists():
            L.load_session_from_file(TARGET_PROFILE, SESSION_FILE)
            print(f"✓ Loaded session from {SESSION_FILE}")
        else:
            raise Exception(f"Session file not found: {SESSION_FILE}")
        
        profile = instaloader.Profile.from_username(L.context, TARGET_PROFILE)
        follower_count = profile.followers
        
        print(f"✓ Fetched follower count: {follower_count}")
        return follower_count
    except Exception as e:
        raise Exception(f"Failed to fetch Instagram data: {e}")


def save_instagram_snapshot(follower_count: int, date: str):
    """Save daily Instagram follower data to history."""
    INSTAGRAM_HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    
    snapshot = {
        "date": date,
        "timestamp": datetime.now().isoformat(),
        "follower_count": follower_count
    }
    
    filepath = INSTAGRAM_HISTORY_DIR / f"{date}.json"
    with open(filepath, "w") as f:
        json.dump(snapshot, f, indent=2)
    
    print(f"✓ Saved Instagram snapshot to {filepath}")


def main():
    """Main entry point."""
    print("=" * 60)
    print("Instagram Follower Data")
    print("=" * 60)
    
    today = datetime.now().strftime("%Y-%m-%d")
    
    try:
        follower_count = get_instagram_follower_count()
        save_instagram_snapshot(follower_count, today)
        print(f"\n✓ Instagram data saved successfully")
        return 0
    except Exception as e:
        print(f"\n✗ Error: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
