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


def _post(token: str, token_name: str, channel: str, text: str) -> None:
    """Post one message, and raise when Slack refuses it.

    Slack answers a rejected post with HTTP 200 and {"ok": false, "error":
    ...}, so raise_for_status() alone reports success. The daily traffic
    report ran every day from 2026-09-02 into a channel that did not exist,
    printed "posted" and exited 0 for every one of those runs; JP saw
    nothing and no alert fired. Checking `ok` here covers every
    caller on the announce side rather than each report remembering to.
    """
    if not token:
        print(f"[slack] {channel}: {text}")
        return
    response = requests.post(
        "https://slack.com/api/chat.postMessage",
        headers={"Authorization": f"Bearer {token}"},
        json={"channel": channel, "text": text},
        timeout=10,
    )
    response.raise_for_status()
    payload = response.json()
    if not payload.get("ok"):
        raise RuntimeError(
            f"Slack refused the post to {channel} using {token_name}: "
            f"{payload.get('error')}"
        )


def post_slack(channel: str, text: str) -> None:
    """Post as the default bot (claude_mcp, shown in Slack as Friend)."""
    _post(os.getenv("SLACK_BOT_TOKEN", ""), "SLACK_BOT_TOKEN", channel, text)


def post_slack_as_reporter(channel: str, text: str) -> None:
    """Post as TWY Reporter (twy_reporter), the identity for cron reports."""
    _post(
        os.getenv("TWY_REPORTER_BOT_TOKEN", ""),
        "TWY_REPORTER_BOT_TOKEN",
        channel,
        text,
    )
