"""Slack posting via bot token."""
import os
import requests


def post_slack(channel: str, text: str) -> None:
    token = os.getenv("SLACK_BOT_TOKEN", "")
    if not token:
        print(f"[slack] {channel}: {text}")
        return
    requests.post(
        "https://slack.com/api/chat.postMessage",
        headers={"Authorization": f"Bearer {token}"},
        json={"channel": channel, "text": text},
        timeout=10,
    ).raise_for_status()
