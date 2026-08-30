"""What the weekly review says now that the CTA variant experiment is off.

The variant comparison could not resolve: every reviewed Reel scored zero
growth actions, so each wording was measured against a baseline of zero by a
baseline of zero, and the review recommended waiting, every week. Acquisition
source is a fact instead of a comparison. (JP 2026-08-30)
"""

import json
from datetime import date

import social_growth_weekly as weekly


def write_snapshot(directory, day, contacts, lists=None):
    payload = {
        "date": day,
        "contacts": contacts,
        "lists": lists
        if lists is not None
        else {"L1": "Email: Subscribed"},
    }
    (directory / f"{day}.json").write_text(json.dumps(payload))


def test_acquisition_reports_new_contacts_by_source(tmp_path):
    write_snapshot(tmp_path, "2026-09-01", [{"id": "a", "list_ids": ["L1"]}])
    write_snapshot(
        tmp_path,
        "2026-09-07",
        [
            {"id": "a", "list_ids": ["L1"]},
            {"id": "b", "list_ids": ["L1"], "source": "habit_signup"},
            {"id": "c", "list_ids": ["L1"], "source": "habit_sync"},
            {"id": "d", "list_ids": ["L1"], "source": "habit_signup"},
        ],
    )

    result = weekly._acquisition(
        week_start=date(2026, 9, 1),
        week_end=date(2026, 9, 7),
        membership_dir=tmp_path,
    )

    assert result["available"] is True
    assert result["created"] == 3
    assert result["by_source"] == {"habit_signup": 2, "habit_sync": 1}


def test_a_refiled_contact_is_counted_separately_from_a_new_one(tmp_path):
    # The 2026-08-09 case: the subscriber count rose by more than the number of
    # people who actually arrived.
    write_snapshot(tmp_path, "2026-09-01", [{"id": "old", "list_ids": []}])
    write_snapshot(
        tmp_path,
        "2026-09-07",
        [
            {"id": "old", "list_ids": ["L1"]},
            {"id": "new", "list_ids": ["L1"], "source": "habit_signup"},
        ],
    )

    result = weekly._acquisition(
        week_start=date(2026, 9, 1),
        week_end=date(2026, 9, 7),
        membership_dir=tmp_path,
    )

    assert result["created"] == 1
    assert result["refiled_onto_subscribed"] == 1


def test_acquisition_is_unavailable_with_one_snapshot(tmp_path):
    write_snapshot(tmp_path, "2026-09-07", [{"id": "a", "list_ids": ["L1"]}])

    result = weekly._acquisition(
        week_start=date(2026, 9, 1),
        week_end=date(2026, 9, 7),
        membership_dir=tmp_path,
    )

    assert result["available"] is False
    assert "fewer than two" in result["reason"]


def test_acquisition_ignores_snapshots_outside_the_window(tmp_path):
    write_snapshot(tmp_path, "2026-08-01", [])
    write_snapshot(tmp_path, "2026-09-07", [{"id": "a", "list_ids": ["L1"]}])

    result = weekly._acquisition(
        week_start=date(2026, 9, 1),
        week_end=date(2026, 9, 7),
        membership_dir=tmp_path,
    )

    assert result["available"] is False


def _report(**overrides):
    report = {
        "status": "ok",
        "week_end": "2026-09-07",
        "metrics": {
            "instagram_followers": {"start": 2319, "end": 2319, "delta": 0},
            "email_subscribers": {"start": 953, "end": 953, "delta": 0},
            "next_habit_registrations": {"start": 0, "end": 0, "delta": 0},
            "landing_page": {
                "visitors": 0,
                "pageviews": 0,
                "habit_register_clicks": 0,
                "habit_signup_success": 0,
            },
        },
        "campaigns": {"recent_variants": [], "upcoming_variants": []},
        "post_performance": {
            "posts_analyzed": 3,
            "totals": {"growth_actions": 0},
            "averages": {"reach": 120.0},
        },
        "campaign_performance": {"variants": []},
        "website_performance": {
            "main": {"status": "unavailable"},
            "habit": {"status": "unavailable"},
        },
        "acquisition": {"available": False, "reason": "no snapshots"},
    }
    report.update(overrides)
    return report


def test_zero_growth_actions_reads_as_distribution_not_copy():
    recommendations = weekly._recommendations(_report())
    assert any(
        "distribution problem in what the posts are" in line
        for line in recommendations
    )
    assert not any("variant" in line for line in recommendations)


def test_reach_far_below_the_follower_count_is_called_out():
    recommendations = weekly._recommendations(_report())
    assert any(
        "barely leaving the existing audience" in line
        for line in recommendations
    )


def test_unattributed_contacts_are_flagged_as_a_bulk_write():
    recommendations = weekly._recommendations(
        _report(
            acquisition={
                "available": True,
                "created": 5,
                "by_source": {"unattributed": 5},
                "refiled_onto_subscribed": 0,
            }
        )
    )
    assert any("carry no source" in line for line in recommendations)


def test_no_new_contacts_is_the_number_to_watch():
    recommendations = weekly._recommendations(
        _report(
            acquisition={
                "available": True,
                "created": 0,
                "by_source": {},
                "refiled_onto_subscribed": 0,
            }
        )
    )
    assert any("No new email contacts this week" in line for line in recommendations)


def test_markdown_renders_the_acquisition_section():
    report = _report(
        acquisition={
            "available": True,
            "from": "2026-09-01",
            "to": "2026-09-07",
            "created": 2,
            "deleted": 0,
            "by_source": {"habit_signup": 2},
            "refiled_onto_subscribed": 3,
        }
    )
    report["recommendations"] = weekly._recommendations(report)
    report.update(
        {
            "week_start": "2026-09-01",
            "snapshot_count": 7,
            "meta_cross_check": {},
            "post_performance": {
                **report["post_performance"],
                "posts": [],
                "top_by_reach": None,
                "top_by_growth_actions": None,
            },
        }
    )

    markdown = weekly.render_markdown(report)

    assert "## Acquisition" in markdown
    assert "habit_signup: 2" in markdown
    assert "Campaign Variants" not in markdown
    assert "were added to Email: Subscribed by a sync" in markdown
