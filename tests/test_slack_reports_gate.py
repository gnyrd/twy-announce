"""The reports to Slack follow slack_reports (JP 2026-09-19): the weekly review
is written but not sent, the member movement post counts as delivered without
posting, the Stats link under the review leaves with stats_site."""
import social_growth_weekly as weekly
import sync_marvelous_member_activity as movement


def switch(tmp_path, monkeypatch, text):
    from twy_platform import contribution
    path = tmp_path / "contribution.toml"
    path.write_text(text)
    monkeypatch.setenv("TWY_CONTRIBUTION_CONFIG", str(path))
    contribution._cache.update(path=None, mtime=None, data=None)


def test_weekly_review_slack_and_stats_link_follow_the_switch(tmp_path, monkeypatch):
    switch(tmp_path, monkeypatch, "continued = true\n")
    assert weekly.slack_reports_on() and weekly.stats_link().strip() == "https://stats.tiffanywoodyoga.com/"
    switch(tmp_path, monkeypatch, "continued = true\n[features]\nslack_reports = false\nstats_site = false\n")
    assert not weekly.slack_reports_on() and weekly.stats_link() == ""


def test_member_movement_post_counts_as_delivered_when_off(tmp_path, monkeypatch):
    calls = []
    import importlib
    slack_module = importlib.import_module("twy_platform.slack")
    monkeypatch.setattr(slack_module, "slack", lambda message, channel=None: calls.append((message, channel)) or True)
    switch(tmp_path, monkeypatch, "continued = true\n[features]\nslack_reports = false\n")
    assert movement.post_activity("Joined: someone", channel="C1") is True
    assert calls == []
    switch(tmp_path, monkeypatch, "continued = true\n")
    assert movement.post_activity("Joined: someone", channel="C1") is True
    assert calls == [("Joined: someone", "C1")]
