#!/usr/bin/env python3
"""Run a command. On non-zero exit, post a warning to Slack #system-warnings.

Usage: notify_on_failure.py <job_name> <cmd> [args...]

Exit code mirrors the wrapped command.
"""
import os
import socket
import sys
import subprocess
from datetime import datetime
from zoneinfo import ZoneInfo

sys.path.insert(0, '/root/twy/paths')
from twy_paths import load_env
load_env()

import requests

CHANNEL = 'C0ASG1EU0HL'  # #system-warnings
MOUNTAIN = ZoneInfo('America/Denver')


def post_failure(name: str, cmd: list[str], rc: int, tail: str) -> None:
    # Prefer the Reports bot (twy_reporter) for warnings; fall back to the
    # Friend bot (claude_mcp) if the Reports token isn't configured.
    tok = os.environ.get('TWY_REPORTER_BOT_TOKEN') or os.environ.get('SLACK_BOT_TOKEN', '')
    if not tok:
        print('[notify_on_failure] no bot token available; cannot post warning', file=sys.stderr)
        return
    host = socket.gethostname()
    when = datetime.now(MOUNTAIN).strftime('%Y-%m-%d %H:%M MT')
    cmd_str = ' '.join(cmd)
    text = (
        f":warning: *Cron failure: `{name}`*\n"
        f"*Host:* {host}\n"
        f"*When:* {when}\n"
        f"*Exit code:* {rc}\n"
        f"*Command:* `{cmd_str}`\n"
        f"*Last output:*\n```\n{tail}\n```"
    )
    try:
        requests.post(
            'https://slack.com/api/chat.postMessage',
            headers={'Authorization': 'Bearer ' + tok, 'Content-Type': 'application/json; charset=utf-8'},
            json={'channel': CHANNEL, 'text': text},
            timeout=10,
        )
    except Exception as e:
        print(f'[notify_on_failure] failed to post warning: {e}', file=sys.stderr)


def main():
    if len(sys.argv) < 3:
        print('usage: notify_on_failure.py <job_name> <cmd> [args...]', file=sys.stderr)
        sys.exit(2)
    name = sys.argv[1]
    cmd = sys.argv[2:]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True)
    except FileNotFoundError as e:
        post_failure(name, cmd, 127, f'command not found: {e}')
        sys.exit(127)
    sys.stdout.write(proc.stdout)
    sys.stderr.write(proc.stderr)
    if proc.returncode != 0:
        combined = (proc.stderr or '') + (proc.stdout or '')
        tail = '\n'.join(combined.splitlines()[-30:])[:2500] or '(no output)'
        post_failure(name, cmd, proc.returncode, tail)
    sys.exit(proc.returncode)


if __name__ == '__main__':
    main()
