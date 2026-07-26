import json

import pytest

from sendgrid_provider_cutover import (
    approval_phrase,
    apply_plan,
    build_plan,
    plan_digest,
)


SOURCE_LISTS = [
    {"id": "marketing1", "name": "TWY Marketing", "contact_count": 921},
    {
        "id": "member1",
        "name": "TWY Yoga Lifestyle",
        "contact_count": 32,
    },
    {"id": "archive1", "name": "TWY Archive", "contact_count": 7},
    {"id": "interest4", "name": "TWY Yoga Habit 2026-04"},
    {"id": "interest5", "name": "TWY Yoga Habit 2026-05"},
    {"id": "interest6", "name": "TWY Yoga Habit 2026-06"},
    {"id": "registered5", "name": "TWY Habit Registered 2026-05"},
    {"id": "registered6", "name": "TWY Habit Registered 2026-06"},
    {"id": "welcome1", "name": "TWY Welcome 3209"},
]


class FakeAPI:
    def __init__(self):
        self.lists = [dict(item) for item in SOURCE_LISTS]
        self.group = {
            "id": 35187,
            "name": "TWY Newsletters",
            "description": "old",
            "is_default": True,
        }
        self.renamed = []
        self.created = []

    def user_email(self):
        return "admin@tiffanywoodyoga.com"

    def marketing_lists(self):
        return [dict(item) for item in self.lists]

    def suppression_groups(self):
        return [dict(self.group)]

    def suppression_group(self, group_id):
        assert group_id == 35187
        return dict(self.group)

    def update_list(self, list_id, name):
        item = next(item for item in self.lists if item["id"] == list_id)
        item["name"] = name
        self.renamed.append((list_id, name))
        return dict(item)

    def create_list(self, name):
        item = {"id": f"created{len(self.created) + 1}", "name": name}
        self.lists.append(item)
        self.created.append(name)
        return dict(item)

    def update_suppression_group(
        self,
        group_id,
        *,
        name,
        description,
        is_default,
    ):
        self.group.update({
            "name": name,
            "description": description,
            "is_default": is_default,
        })
        return dict(self.group)


def test_plan_has_only_locked_renames_and_current_period_creates():
    plan = build_plan(
        account_email="admin@tiffanywoodyoga.com",
        marketing_lists=SOURCE_LISTS,
        suppression_groups=[{
            "id": 35187,
            "name": "TWY Newsletters",
            "is_default": True,
        }],
    )

    assert {
        operation["target_name"]
        for operation in plan["list_renames"]
    } == {
        "Email: Subscribed",
        "Member: Yoga Lifestyle",
        "Member: Archive",
        "Yoga Habit: Interested: 2026_04",
        "Yoga Habit: Interested: 2026_05",
        "Yoga Habit: Interested: 2026_06",
        "Yoga Habit: Registered: 2026_05",
        "Yoga Habit: Registered: 2026_06",
    }
    assert plan["list_ensures"] == [
        "Yoga Habit: Interested: 2026_08",
        "Yoga Habit: Registered: 2026_08",
    ]
    assert plan["suppression_group"]["target_name"] == "Email: Unsubscribed"
    assert all("Welcome" not in item["target_name"] for item in plan["list_renames"])


def test_apply_requires_exact_digest_approval_and_writes_registry(tmp_path):
    api = FakeAPI()
    plan = build_plan(
        account_email=api.user_email(),
        marketing_lists=api.marketing_lists(),
        suppression_groups=api.suppression_groups(),
    )
    registry_path = tmp_path / "production_objects.json"

    with pytest.raises(ValueError, match="approval"):
        apply_plan(
            api=api,
            plan=plan,
            approval="approve",
            registry_path=registry_path,
        )

    result = apply_plan(
        api=api,
        plan=plan,
        approval=approval_phrase(plan),
        registry_path=registry_path,
    )

    registry = json.loads(registry_path.read_text())
    assert registry["lists"]["Email: Subscribed"]["id"] == "marketing1"
    assert registry["lists"]["Yoga Habit: Interested: 2026_08"]["id"] == "created1"
    assert registry["suppression_group"] == {
        "id": 35187,
        "name": "Email: Unsubscribed",
    }
    assert result["digest"] == plan_digest(plan)
    assert "TWY Welcome 3209" not in registry["lists"]


def test_plan_rejects_unexpected_account():
    with pytest.raises(ValueError, match="account"):
        build_plan(
            account_email="someone@example.com",
            marketing_lists=SOURCE_LISTS,
            suppression_groups=[],
        )
