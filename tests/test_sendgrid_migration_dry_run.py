import json

from sendgrid_migration_dry_run import (
    journey_shape,
    run_dry_run,
    source_dependency_report,
)


class FakeMailchimp:
    def __init__(self):
        self.audit = ({"method": "GET", "path": "/lists/aud/members", "status": 200},)

    def collect_members(self):
        return {
            "subscribed": [{
                "id": "1",
                "email_address": "a@example.com",
                "status": "subscribed",
                "tags": [{"name": "Membership - Yoga Lifestyle"}],
                "merge_fields": {"FNAME": "A"},
                "last_changed": "2026-07-24T00:00:00Z",
            }],
            "unsubscribed": [{
                "id": "2",
                "email_address": "b@example.com",
                "status": "unsubscribed",
                "tags": [],
                "merge_fields": {},
                "last_changed": "2026-07-24T00:00:00Z",
            }],
            "cleaned": [],
            "pending": [],
            "transactional": [],
            "archived": [],
        }

    def inventory(self, journey_id):
        return {
            "list": {"id": "aud"},
            "merge_fields": [{"tag": "FNAME"}, {"tag": "LNAME"}],
            "segments": [
                {"id": 2964430, "name": "New Subscriber YLS Membership"},
                {"id": 3018884, "name": "Membership - Yoga Lifestyle"},
                {"id": 3019143, "name": "Lifestyle"},
                {"id": 3019144, "name": "Non-Lifestyle"},
                {"id": 10, "name": "Status - Member"},
                {"id": 11, "name": "Status - Lead"},
                {"id": 12, "name": "Status - Yoga Lifestyle - Canceled"},
                {"id": 13, "name": "Status - TWY Archive - Canceled"},
                {"id": 14, "name": "Membership - TWY Archive"},
                {"id": 15, "name": "Role - Owner"},
                {"id": 16, "name": "Role - Admin"},
                {"id": 17, "name": "Yoga Habit - 2026-06"},
                {"id": 18, "name": "Habit Registered - 2026-06"},
            ],
            "journey": {
                "id": journey_id,
                "steps": {
                    "steps": [
                        {
                            "step_type": "trigger-tag_added",
                            "trigger_settings": {"tag_id": 2964430},
                            "trigger_details": {
                                "tag": {"tag_name": "New Subscriber YLS Membership"}
                            },
                        },
                        {
                            "step_type": "action-send_email",
                            "action_settings": {"campaign_id": 12660119},
                        },
                    ],
                },
            },
        }


class FakeSendGrid:
    audit = (
        {"method": "POST", "path": "/marketing/contacts/search/emails", "status": 200},
    )

    def safety_states(self, emails):
        return {}

    def inventory(self):
        return {"lists": [], "custom_fields": [], "groups": []}


def test_run_writes_complete_zero_mutation_evidence(tmp_path):
    backup_steps = {
        "steps": [
            {
                "step_type": "trigger-tag_added",
                "trigger_settings": {"tag_id": 2964430},
                "trigger_details": {
                    "tag": {"tag_name": "New Subscriber YLS Membership"}
                },
            },
            {
                "step_type": "action-send_email",
                "action_settings": {"campaign_id": 12660119},
            },
        ],
    }
    backup_trigger = {
        "state": "resolved",
        "tag_id": 2964430,
        "tag_name": "New Subscriber YLS Membership",
    }
    result = run_dry_run(
        FakeMailchimp(),
        FakeSendGrid(),
        tmp_path / "run",
        "production_test_pass1",
        journey_backup={
            "steps": backup_steps,
            "trigger": backup_trigger,
        },
    )
    assert result["terminal_counts"] == {
        "deliverable": 1,
        "marketing_suppressed": 1,
    }
    manifest = json.loads((tmp_path / "run" / "manifest.json").read_text())
    assert manifest["mutation_endpoint_count"] == 0
    assert manifest["journey_backup_match"] is True
    assert manifest["source_dependencies_complete"] is True
    assert (tmp_path / "run" / "mailchimp_inventory.json").exists()
    assert (tmp_path / "run" / "targeting_dependencies.json").exists()
    assert (tmp_path / "run" / "COMPLETE").exists()


def test_run_has_no_apply_or_write_mode():
    import sendgrid_migration_dry_run

    parser = sendgrid_migration_dry_run.build_parser()
    option_strings = {
        option
        for action in parser._actions
        for option in action.option_strings
    }
    assert "--write" not in option_strings
    assert "--apply" not in option_strings
    assert "--send" not in option_strings


def test_journey_shape_ignores_stats_and_timestamps_but_keeps_targeting():
    payload = {
        "steps": [
            {
                "step_type": "trigger-tag_added",
                "updated_at": "volatile",
                "stats": {"started": 99},
                "trigger_settings": {"tag_id": 2964430},
                "trigger_details": {
                    "tag": {"tag_name": "New Subscriber YLS Membership"}
                },
            },
            {
                "step_type": "delay",
                "delay_time": 259200,
                "updated_at": "volatile",
            },
            {
                "step_type": "action-send_email",
                "action_settings": {"campaign_id": 12660119},
                "stats": {"sent": 10},
            },
        ],
    }
    assert journey_shape(payload) == [
        {
            "step_type": "trigger-tag_added",
            "tag_id": 2964430,
            "tag_name": "New Subscriber YLS Membership",
        },
        {"step_type": "delay", "delay_time": 259200},
        {"step_type": "action-send_email", "campaign_id": 12660119},
    ]


def test_source_dependency_report_fails_closed_on_missing_or_mismatched_targets():
    inventory = FakeMailchimp().inventory(3209)
    inventory["segments"] = [
        segment
        for segment in inventory["segments"]
        if segment["name"] != "Role - Admin"
    ]
    inventory["segments"] = [
        {**segment, "name": "Wrong"}
        if segment["id"] == 3019143
        else segment
        for segment in inventory["segments"]
    ]
    report = source_dependency_report(inventory)
    assert report["complete"] is False
    assert report["missing_names"] == ["Lifestyle", "Role - Admin"]
    assert report["id_name_mismatches"] == [{
        "actual_name": "Wrong",
        "expected_name": "Lifestyle",
        "id": 3019143,
    }]
