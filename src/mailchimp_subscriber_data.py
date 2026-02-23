#!/usr/bin/env python3
"""Fetch and save Mailchimp subscriber data."""

import json
import os
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from mailchimp3 import MailChimp

# Load environment variables
load_dotenv()

# Configuration
PROJECT_ROOT = Path(__file__).parent.parent
MAILCHIMP_HISTORY_DIR = PROJECT_ROOT / "data/mailchimp/history"


def get_mailchimp_subscriber_count() -> int:
    """Fetch subscriber count from Mailchimp."""
    api_key = os.getenv("MAILCHIMP_API_KEY")
    audience_id = os.getenv("MAILCHIMP_AUDIENCE_ID")
    
    if not api_key or not audience_id:
        raise Exception("MAILCHIMP_API_KEY and MAILCHIMP_AUDIENCE_ID must be set in .env")
    
    print("Fetching Mailchimp subscriber data...")
    try:
        client = MailChimp(mc_api=api_key)
        list_info = client.lists.get(list_id=audience_id)
        member_count = list_info["stats"]["member_count"]
        print(f"✓ Fetched subscriber count: {member_count}")
        return member_count
    except Exception as e:
        raise Exception(f"Failed to fetch Mailchimp data: {e}")


def save_mailchimp_snapshot(subscriber_count: int, date: str):
    """Save daily Mailchimp subscriber data to history."""
    MAILCHIMP_HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    
    snapshot = {
        "date": date,
        "timestamp": datetime.now().isoformat(),
        "subscriber_count": subscriber_count
    }
    
    filepath = MAILCHIMP_HISTORY_DIR / f"{date}.json"
    with open(filepath, "w") as f:
        json.dump(snapshot, f, indent=2)
    
    print(f"✓ Saved Mailchimp snapshot to {filepath}")


def main():
    """Main entry point."""
    print("=" * 60)
    print("Mailchimp Subscriber Data")
    print("=" * 60)
    
    today = datetime.now().strftime("%Y-%m-%d")
    
    try:
        subscriber_count = get_mailchimp_subscriber_count()
        save_mailchimp_snapshot(subscriber_count, today)
        print(f"\n✓ Mailchimp data saved successfully")
        return 0
    except Exception as e:
        print(f"\n✗ Error: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
