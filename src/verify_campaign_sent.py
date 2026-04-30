#!/usr/bin/env python3
"""Verify recently-scheduled MC campaigns actually sent.

Queries MC for any 'Yoga Habit' campaign whose send_time was in the past
24 hours but whose status is NOT 'sent' (still 'schedule', 'paused',
'save', or 'sending'). Prints details and exits non-zero so the
notify_on_failure wrapper posts to #system-warnings via the Reports bot.

Daily cron at 17:00 UTC (11am MT). Gives the 9am MT (15:00 UTC) monthly
newsletters ~2h grace and the 16:00 UTC PH1/PH2 follow-ups ~1h grace.
"""
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

sys.path.insert(0, '/root/twy/paths')
from twy_paths import load_env  # noqa: E402

load_env()

MAILCHIMP_API_KEY = os.environ['MAILCHIMP_API_KEY']
MAILCHIMP_DC = MAILCHIMP_API_KEY.split('-')[-1]
WATCH_TITLE_FRAGMENTS = ['Yoga Habit']


def mc_url(path):
    return f'https://{MAILCHIMP_DC}.api.mailchimp.com/3.0{path}'


def mc_auth():
    return ('any', MAILCHIMP_API_KEY)


def find_stuck_campaigns(window_hours: int = 24):
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=window_hours)
    stuck = []
    for status in ('schedule', 'paused', 'save', 'sending'):
        r = requests.get(
            mc_url('/campaigns'),
            auth=mc_auth(),
            params={'status': status, 'count': 100},
            timeout=15,
        )
        if not r.ok:
            continue
        for c in r.json().get('campaigns', []):
            title = (c.get('settings') or {}).get('title', '')
            if not any(f in title for f in WATCH_TITLE_FRAGMENTS):
                continue
            send_time_str = c.get('send_time')
            if not send_time_str:
                continue
            try:
                send_dt = datetime.fromisoformat(send_time_str.replace('Z', '+00:00'))
            except ValueError:
                continue
            if cutoff <= send_dt <= now:
                stuck.append({
                    'id': c['id'],
                    'web_id': c.get('web_id'),
                    'title': title,
                    'status': c['status'],
                    'send_time': send_time_str,
                })
    return stuck


def main() -> int:
    stuck = find_stuck_campaigns()
    if not stuck:
        print('OK: no stuck Yoga Habit campaigns in last 24h')
        return 0

    print('STUCK Yoga Habit campaigns (expected sent within last 24h):')
    for c in stuck:
        print(f"  {c['title']}: status={c['status']}  scheduled_for={c['send_time']}")
        if c.get('web_id'):
            print(f"    https://admin.mailchimp.com/campaigns/show?id={c['web_id']}")
    return 1


if __name__ == '__main__':
    sys.exit(main())
