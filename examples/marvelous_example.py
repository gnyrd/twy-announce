#!/usr/bin/env python3
"""Example usage of the MarvelousClient library."""

import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from marvelous_client import MarvelousClient, MarvelousAPIError


def example_basic_usage():
    """Basic usage example."""
    print("=== Basic Usage Example ===\n")
    
    token = os.environ.get("MARVELOUS_TOKEN", "your-token-here")
    client = MarvelousClient(auth_token=token)
    
    try:
        print("Listing events...")
        events = client.list_events("tiffany-wood-yoga")
        print(f"Found {len(events)} events")
        
        if events:
            first_event = events[0]
            print(f"\nFirst event: {first_event['event_name']}")
            print(f"  ID: {first_event['id']}")
            print(f"  Start: {first_event['event_start_datetime']}")
        
    except MarvelousAPIError as e:
        print(f"Error: {e}")


if __name__ == "__main__":
    example_basic_usage()
