#!/usr/bin/env python3
# Copyright (c) 2025 Ninsim, Inc.
# All rights reserved.
"""
Daily Slack Status Report for TWY Growth Blitz 2025

Posts campaign status to Slack channel with:
- Days to launch counter
- Phase completion percentages
- Overdue tasks
- Tasks due this week
- Priority breakdown

Usage:
    python3 scripts/daily_status_report.py

Setup:
    1. Add SLACK_WEBHOOK_URL to .env file
       OR
    2. Add SLACK_BOT_TOKEN and SLACK_CHANNEL to .env file

Example .env:
    SLACK_WEBHOOK_URL=https://hooks.slack.com/services/YOUR/WEBHOOK/URL
    # OR
    SLACK_BOT_TOKEN=xoxb-your-bot-token
    SLACK_CHANNEL=#twy-campaign
"""

import os
import requests
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
from collections import defaultdict

# Load environment
# Load from parent directory (.env is one level up from src/)
import pathlib
script_dir = pathlib.Path(__file__).parent
load_dotenv(script_dir.parent / '.env')

# Config
TRELLO_KEY = os.getenv('TRELLO_API_KEY')
TRELLO_TOKEN = os.getenv('TRELLO_TOKEN')
TRELLO_BOARD = os.getenv('TRELLO_BOARD_ID')
SLACK_WEBHOOK = os.getenv('SLACK_WEBHOOK_URL')
SLACK_TOKEN = os.getenv('SLACK_BOT_TOKEN')
SLACK_CHANNEL = os.getenv('SLACK_CHANNEL', '#twy-campaign')

LAUNCH_DATE = datetime(2025, 12, 26, tzinfo=timezone.utc)
CAMPAIGN_END = datetime(2026, 1, 31, tzinfo=timezone.utc)

def get_trello_data():
    """Fetch all cards and lists from Trello board."""
    base = 'https://api.trello.com/1'
    params = {'key': TRELLO_KEY, 'token': TRELLO_TOKEN}
    
    # Get lists
    lists = requests.get(f'{base}/boards/{TRELLO_BOARD}/lists', params=params).json()
    list_map = {l['id']: l['name'] for l in lists}
    
    # Get cards
    cards = requests.get(f'{base}/boards/{TRELLO_BOARD}/cards', params=params).json()
    
    return cards, list_map

def analyze_cards(cards, list_map):
    """Analyze cards for phase completion, priorities, due dates."""
    from datetime import timezone
    now = datetime.now(timezone.utc)
    
    # Initialize counters
    phase_stats = defaultdict(lambda: {'total': 0, 'done': 0})
    priority_counts = defaultdict(int)
    overdue = []
    due_this_week = []
    
    for card in cards:
        if card['closed']:
            continue
            
        # Get labels
        labels = {l['name'].lower() for l in card['labels']}
        list_name = list_map.get(card['idList'], 'Unknown')
        
        # Phase analysis
        for phase in ['phase 1', 'phase 2', 'phase 3', 'phase 4']:
            if phase in labels:
                phase_stats[phase]['total'] += 1
                if list_name == 'Done':
                    phase_stats[phase]['done'] += 1
                break
        
        # Priority analysis
        for priority in ['high priority', 'med priority', 'low priority']:
            if priority in labels:
                priority_counts[priority] += 1
                break
        
        # Due date analysis
        if card.get('due') and list_name != 'Done':
            due_date = datetime.fromisoformat(card['due'].replace('Z', '+00:00'))
            if due_date < now:
                overdue.append((card['shortLink'], card['name'], due_date))
            elif due_date < now + timedelta(days=7):
                due_this_week.append((card['shortLink'], card['name'], due_date))
    
    return phase_stats, priority_counts, overdue, due_this_week

def format_report(phase_stats, priority_counts, overdue, due_this_week):
    """Format status report as Slack message."""
    from datetime import timezone
    now = datetime.now(timezone.utc)
    days_to_launch = (LAUNCH_DATE - now).days
    days_to_end = (CAMPAIGN_END - now).days
    
    # Header
    lines = [
        f"📊 *TWY Growth Blitz - Daily Status* ({now.strftime('%b %d, %Y')})",
        ""
    ]
    
    # Launch countdown
    if days_to_launch >= 0:
        lines.append(f"🚀 *Launch:* {days_to_launch} days (Dec 26)")
    else:
        lines.append(f"🎯 *Campaign Day {abs(days_to_launch) + 1}/60* ({days_to_end} days remaining)")
    lines.append("")
    
    # Phase status
    lines.append("*Phase Status:*")
    for phase in sorted(phase_stats.keys()):
        stats = phase_stats[phase]
        if stats['total'] > 0:
            pct = int(100 * stats['done'] / stats['total'])
            emoji = '✅' if pct == 100 else '🔄' if pct > 50 else '⏳'
            phase_name = phase.replace('phase ', 'Phase ')
            lines.append(f"{emoji} {phase_name}: {stats['done']}/{stats['total']} complete ({pct}%)")
    lines.append("")
    
    # Overdue tasks
    if overdue:
        lines.append(f"⚠️ *Overdue ({len(overdue)}):*")
        for card_id, name, due in sorted(overdue, key=lambda x: x[2])[:5]:  # Top 5
            days_overdue = (now - due).days
            card_url = f"https://trello.com/c/{card_id}"
            lines.append(f"• <{card_url}|{name}> ({days_overdue}d overdue)")
        if len(overdue) > 5:
            lines.append(f"• _{len(overdue) - 5} more..._")
        lines.append("")
    
    # Due this week
    if due_this_week:
        lines.append(f"🎯 *Due This Week ({len(due_this_week)}):*")
        for card_id, name, due in sorted(due_this_week, key=lambda x: x[2])[:8]:  # Top 8
            date_str = due.strftime('%b %d')
            card_url = f"https://trello.com/c/{card_id}"
            lines.append(f"• <{card_url}|{name}> ({date_str})")
        if len(due_this_week) > 8:
            lines.append(f"• _{len(due_this_week) - 8} more..._")
        lines.append("")
    
    # Priority breakdown
    lines.append("*Priority Breakdown:*")
    if priority_counts['high priority']:
        lines.append(f"🔴 High: {priority_counts['high priority']} cards")
    if priority_counts['med priority']:
        lines.append(f"🟡 Med: {priority_counts['med priority']} cards")
    if priority_counts['low priority']:
        lines.append(f"🟢 Low: {priority_counts['low priority']} cards")
    
    return '\n'.join(lines)

def post_to_slack(message):
    """Post message to Slack via webhook or bot token."""
    if SLACK_WEBHOOK:
        # Webhook method (simpler)
        payload = {'text': message}
        response = requests.post(SLACK_WEBHOOK, json=payload)
        response.raise_for_status()
        print(f"✓ Posted to Slack via webhook")
    elif SLACK_TOKEN:
        # Bot token method
        url = 'https://slack.com/api/chat.postMessage'
        headers = {'Authorization': f'Bearer {SLACK_TOKEN}'}
        payload = {
            'channel': SLACK_CHANNEL,
            'text': message,
            'unfurl_links': False,
            'unfurl_media': False
        }
        response = requests.post(url, headers=headers, json=payload)
        response.raise_for_status()
        data = response.json()
        if not data.get('ok'):
            raise Exception(f"Slack API error: {data.get('error')}")
        print(f"✓ Posted to Slack channel {SLACK_CHANNEL}")
    else:
        raise ValueError("No Slack credentials found. Set SLACK_WEBHOOK_URL or SLACK_BOT_TOKEN in .env")

def main():
    """Generate and post daily status report."""
    print("Fetching Trello data...")
    cards, list_map = get_trello_data()
    
    print("Analyzing cards...")
    phase_stats, priority_counts, overdue, due_this_week = analyze_cards(cards, list_map)
    
    print("Formatting report...")
    message = format_report(phase_stats, priority_counts, overdue, due_this_week)
    
    print("\n" + "="*50)
    print(message)
    print("="*50 + "\n")
    
    print("Posting to Slack...")
    post_to_slack(message)
    
    print("✓ Done!")

if __name__ == '__main__':
    main()
