import sync_sendgrid_memberships as membership_sync


def test_normalize_active_memberships_keeps_products_separate():
    rows = [
        {
            "Status": "Active",
            "Email": " BOTH@example.com ",
            "First Name": "Both",
            "Product Name": "The Yoga Lifestyle Membership",
        },
        {
            "Status": "Active",
            "Email": "both@example.com",
            "Last Name": "Member",
            "Product Name": "The Archive",
        },
        {
            "Status": "Canceled",
            "Email": "canceled@example.com",
            "Product Name": "Yoga Lifestyle",
        },
        {
            "Status": "Active",
            "Email": "unknown@example.com",
            "Product Name": "Unrelated Product",
        },
    ]

    memberships, unknown = membership_sync.normalize_active_memberships(rows)

    assert memberships == {
        "Member: Yoga Lifestyle": [
            {
                "email": "both@example.com",
                "first_name": "Both",
                "last_name": "Member",
            }
        ],
        "Member: Archive": [
            {
                "email": "both@example.com",
                "first_name": "Both",
                "last_name": "Member",
            }
        ],
    }
    assert unknown == {"Unrelated Product"}


def test_sync_memberships_reconciles_each_locked_list(monkeypatch):
    ensured = []
    calls = []

    def fake_ensure(api, registry, name):
        ensured.append(name)
        return f"id{len(ensured)}"

    def fake_sync(**kwargs):
        calls.append(kwargs)
        return {"desired": len(kwargs["desired_contacts"]), "removed": 0}

    monkeypatch.setattr(membership_sync, "ensure_list", fake_ensure)
    monkeypatch.setattr(membership_sync, "sync_exact_list", fake_sync)

    result = membership_sync.sync_membership_lists(
        api=object(),
        registry=object(),
        memberships={
            "Member: Yoga Lifestyle": [{"email": "yl@example.com"}],
            "Member: Archive": [{"email": "archive@example.com"}],
        },
    )

    assert ensured == ["Member: Yoga Lifestyle", "Member: Archive"]
    assert [call["destination_list_id"] for call in calls] == ["id1", "id2"]
    assert all(call["additive_list_ids"] is None for call in calls)
    assert result["Member: Yoga Lifestyle"]["desired"] == 1


def test_membership_sync_does_not_touch_email_subscription_list(monkeypatch):
    names = []
    monkeypatch.setattr(
        membership_sync,
        "ensure_list",
        lambda api, registry, name: names.append(name) or name,
    )
    monkeypatch.setattr(
        membership_sync,
        "sync_exact_list",
        lambda **kwargs: {"desired": 0, "removed": 0},
    )

    membership_sync.sync_membership_lists(
        api=object(),
        registry=object(),
        memberships={
            "Member: Yoga Lifestyle": [],
            "Member: Archive": [],
        },
    )

    assert "Email: Subscribed" not in names
