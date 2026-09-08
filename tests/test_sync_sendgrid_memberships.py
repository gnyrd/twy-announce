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


# --- identity: members carry their HeyMarvelous id, renames run first --------

from sendgrid_contact_identity import IDENTITY_FIELD


def test_attach_customer_ids_stamps_the_field_and_counts_the_unknown():
    memberships = {
        "Member: Yoga Lifestyle": [{"email": "a@example.com", "first_name": "A"}, {"email": "b@example.com"}],
        "Member: Archive": [{"email": "a@example.com"}],
    }

    stamped, count, unresolved = membership_sync.attach_customer_ids(
        memberships, {"a@example.com": "441"}, "e5_T"
    )

    assert stamped["Member: Yoga Lifestyle"][0] == {
        "email": "a@example.com", "first_name": "A", "custom_fields": {"e5_T": "441"},
    }
    assert stamped["Member: Archive"][0]["custom_fields"] == {"e5_T": "441"}
    assert "custom_fields" not in stamped["Member: Yoga Lifestyle"][1]
    assert count == 1 and unresolved == ["b@example.com"]
    assert "custom_fields" not in memberships["Member: Yoga Lifestyle"][0]


def test_without_the_field_nothing_is_stamped_and_known_ids_are_not_unresolved():
    memberships = {"Member: Yoga Lifestyle": [{"email": "a@example.com"}, {"email": "b@example.com"}]}

    stamped, count, unresolved = membership_sync.attach_customer_ids(
        memberships, {"a@example.com": "441"}, ""
    )

    assert count == 0 and unresolved == ["b@example.com"]
    assert "custom_fields" not in stamped["Member: Yoga Lifestyle"][0]


def test_desired_customer_ids_span_both_lists():
    memberships = {
        "Member: Yoga Lifestyle": [{"email": "a@example.com"}],
        "Member: Archive": [{"email": "c@example.com"}, {"email": "x@example.com"}],
    }
    assert membership_sync.desired_customer_ids(
        memberships, {"a@example.com": "1", "c@example.com": "3"}
    ) == {"a@example.com": "1", "c@example.com": "3"}


class Registry:
    suppression_group_id = 35187

    def __init__(self, ids):
        self.ids = ids

    def list_id(self, name):
        if name in self.ids:
            return self.ids[name]
        raise KeyError(name)


def _listed(email, customer_id):
    return {"email": email, "id": f"c_{email}", "fields": {IDENTITY_FIELD: customer_id}}


class AccountAPI:
    """all_contacts plus the upsert the identity pass makes."""

    def __init__(self, rows):
        self.rows = rows
        self.upserts = []

    def all_contacts(self, *, fields=()):
        return [dict(r, fields=dict(r["fields"])) for r in self.rows]

    def upsert_contacts(self, list_ids, contacts):
        self.upserts.append((list(list_ids), [dict(c) for c in contacts]))
        return "job-1"

    def wait_contact_job(self, job_id, timeout_s=120):
        return {"status": "completed"}


def test_identity_pass_stamps_known_contacts_without_touching_lists(tmp_path):
    api = AccountAPI([_listed("a@example.com", ""), _listed("m@example.com", "9"), _listed("x@example.com", "")])

    listed, counts = membership_sync.identity_pass(
        api, ids={"a@example.com": "441", "m@example.com": "9", "ghost@example.com": "5"},
        field_id="e5_T", dry_run=False,
    )

    assert api.upserts == [([], [{"email": "a@example.com", "custom_fields": {"e5_T": "441"}}])]
    assert counts == {"matched": 2, "already": 1, "stamped": 1, "restamped": 0, "deferred": 0}
    assert {r["email"]: r["fields"][IDENTITY_FIELD] for r in listed} == {
        "a@example.com": "441", "m@example.com": "9", "x@example.com": "",
    }


def test_identity_pass_dry_run_counts_and_writes_nothing():
    api = AccountAPI([_listed("a@example.com", "")])

    listed, counts = membership_sync.identity_pass(
        api, ids={"a@example.com": "441"}, field_id="", dry_run=True,
    )

    assert api.upserts == []
    assert counts["stamped"] == 1
    assert listed[0]["fields"][IDENTITY_FIELD] == ""


def test_rename_phase_dry_run_plans_and_writes_nothing(tmp_path, monkeypatch):
    registry = Registry({"Member: Yoga Lifestyle": "yl"})

    def must_not_apply(*args, **kwargs):
        raise AssertionError("a dry run must not rename")

    monkeypatch.setattr(membership_sync, "apply_rename", must_not_apply)

    result = membership_sync.rename_phase(
        object(), registry, desired={"b@example.com": "441"},
        listed=[_listed("a@example.com", "441")],
        ledger_path=tmp_path / "l.jsonl", enrollments_path=tmp_path / "j.db",
        identity_field="e5_T", field_ids={}, dry_run=True,
    )

    assert result == {"planned": 1, "renamed": 0, "duplicates": 0, "conflicts": 0}
    assert not (tmp_path / "j.db").exists()


def test_rename_phase_applies_each_planned_rename_with_the_registry_group(tmp_path, monkeypatch):
    registry = Registry({"Member: Yoga Lifestyle": "yl", "Member: Archive": "ar"})
    applied = []

    def fake_apply(api_arg, **kwargs):
        applied.append(kwargs)
        return {"event": "completed"}

    monkeypatch.setattr(membership_sync, "apply_rename", fake_apply)

    result = membership_sync.rename_phase(
        object(), registry, desired={"b@example.com": "441"},
        listed=[_listed("a@example.com", "441")],
        ledger_path=tmp_path / "l.jsonl", enrollments_path=tmp_path / "j.db",
        identity_field="e5_T", field_ids={"twy_source": "e3_T"}, dry_run=False,
    )

    assert result == {"planned": 1, "renamed": 1, "duplicates": 0, "conflicts": 0}
    rename = applied[0]["rename"]
    assert (rename.old_email, rename.new_email, rename.customer_id) == ("a@example.com", "b@example.com", "441")
    assert applied[0]["suppression_group_id"] == 35187
    assert applied[0]["identity_field_id"] == "e5_T"
    assert applied[0]["field_ids"] == {"twy_source": "e3_T"}
    assert (tmp_path / "j.db").exists()


def test_the_parser_knows_dry_run():
    assert membership_sync.build_parser().parse_args(["--dry-run"]).dry_run is True
    assert membership_sync.build_parser().parse_args([]).dry_run is False
