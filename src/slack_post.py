"""Post to Slack from the announce side.

Named slack_post, not slack, because classes/dashboard has its own slack
module and inserts THIS directory at sys.path position 0. Under the old name
a dashboard process could resolve `import slack` here, where
post_slack_as_reporter does not exist, and only import order kept that from
breaking the class-plan and journey write firehose. Renamed 2026-08-11 after
the same ambiguity in `newsletter` cost a full build.
"""
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
