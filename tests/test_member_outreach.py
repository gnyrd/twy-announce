from datetime import date, datetime, timedelta, timezone
import csv
import json
import sqlite3

import pytest

import member_outreach as mo

M = mo.Member
START = date(2026, 9, 15)
TODAY = date(2026, 10, 1)


def quiet(members, attended=None, coverage=date(2026, 9, 30), today=TODAY, alerted=()):
    starts = {m.cid: m.start for m in members}
    return mo.quiet_starts(members, starts, attended or {}, coverage, today, set(alerted))


# ---- the quiet start --------------------------------------------------------

def test_a_new_member_with_no_class_in_two_weeks_is_named():
    member = M(1, "A", START, True)
    assert quiet([member]) == [member]


def test_a_class_in_the_first_two_weeks_means_no_post():
    member = M(1, "A", START, True)
    assert quiet([member], attended={1: [START + timedelta(days=12)]}) == []


def test_a_class_on_day_fourteen_is_outside_the_window():
    member = M(1, "A", START, True)
    assert quiet([member], attended={1: [START + timedelta(days=14)]}) == [member]


def test_waits_until_the_attendance_data_covers_the_whole_window():
    member = M(1, "A", START, True)
    assert quiet([member], coverage=START + timedelta(days=13)) == []
    assert quiet([member], coverage=None) == []


def test_waits_for_the_full_two_weeks():
    member = M(1, "A", START, True)
    assert quiet([member], today=START + timedelta(days=13)) == []


def test_a_member_is_named_once():
    member = M(1, "A", START, True)
    assert quiet([member], alerted={1}) == []


def test_an_old_start_is_not_news():
    member = M(1, "A", TODAY - timedelta(days=60), True)
    assert quiet([member], coverage=TODAY) == []


def test_archive_only_members_are_not_checked_for_live_classes():
    assert quiet([M(1, "A", START, False)]) == []


def test_several_quiet_members_come_in_one_post_oldest_first():
    older, newer = M(2, "B", START - timedelta(days=3), True), M(1, "A", START, True)
    assert quiet([newer, older]) == [older, newer]
    text = mo.quiet_message([older, newer], {1: newer.start, 2: older.start})
    assert text.count("app.heymarvelous.com/customers/") == 2
    assert text.startswith("*New members, no live class in their first two weeks:*")


# ---- the check-ins, every two weeks ------------------------------------------

def first_year(cid, days=100, today=TODAY):
    return M(cid, f"N{cid}", today - timedelta(days=days), True)


def core(cid, days=800, today=TODAY):
    return M(cid, f"C{cid}", today - timedelta(days=days), True)


def test_one_from_the_first_year_and_one_from_the_core():
    picks = mo.pick_checkins([first_year(1), first_year(2), core(10), core(11)], {}, "2026-10-12", TODAY)
    assert len(picks) == 2
    assert sorted(p.cid < 10 for p in picks) == [False, True]


def test_nobody_in_a_group_is_picked_twice_before_everyone_in_it_has_been_picked():
    members = [first_year(1), first_year(2), first_year(3), core(10), core(11)]
    history = {}
    for n in range(6):
        period = (mo.PERIOD_EPOCH + timedelta(days=14 * n)).isoformat()
        picks = mo.pick_checkins(members, history, period, TODAY)
        history[period] = [p.cid for p in picks]
    newer_order = [cid for period in sorted(history) for cid in history[period] if cid < 10]
    core_order = [cid for period in sorted(history) for cid in history[period] if cid >= 10]
    assert sorted(newer_order[:3]) == [1, 2, 3] and sorted(newer_order[3:6]) == [1, 2, 3]
    assert sorted(core_order[:2]) == [10, 11] and sorted(core_order[2:4]) == [10, 11]


def test_members_in_their_first_thirty_days_wait():
    picks = mo.pick_checkins([first_year(1, days=10), core(10)], {}, "2026-10-12", TODAY)
    assert [p.cid for p in picks] == [10]


def test_an_empty_group_means_both_picks_come_from_the_other():
    picks = mo.pick_checkins([core(10), core(11), core(12)], {}, "2026-10-12", TODAY)
    assert len(picks) == 2 and all(p.cid >= 10 for p in picks)


def test_a_member_joins_the_core_on_their_first_anniversary():
    anniversary = first_year(1, days=365)
    picks = mo.pick_checkins([anniversary, first_year(2)], {}, "2026-10-12", TODAY)
    assert {p.cid for p in picks} == {1, 2}  # one from each group


def test_archive_only_members_are_never_picked():
    # JP 2026-09-28: Tiffany wants to retire The Archive, so it is ignored.
    archive_only = M(20, "Archive", TODAY - timedelta(days=800), False)
    picks = mo.pick_checkins([archive_only, core(10), first_year(1)], {}, "2026-10-12", TODAY)
    assert 20 not in {p.cid for p in picks} and len(picks) == 2


def test_the_checkin_post_names_both_for_the_two_weeks_one_a_week():
    text = mo.checkin_message([first_year(1), core(10)], mo.PERIOD_EPOCH)
    assert text.startswith("*Check-ins, Sep 28 to Oct 11:*")
    assert text.count("member since") == 2 and "One a week" in text and "Tiffany" in text


def test_periods_start_on_alternate_mondays_from_2026_09_28():
    assert mo.period_start(date(2026, 9, 28)) == date(2026, 9, 28)
    assert mo.period_start(date(2026, 10, 11)) == date(2026, 9, 28)
    assert mo.period_start(date(2026, 10, 12)) == date(2026, 10, 12)
    assert mo.period_start(date(2026, 9, 27)) == date(2026, 9, 14)
    assert all(mo.period_start(date(2026, 11, d)).weekday() == 0 for d in range(1, 29))


# ---- the switch and a whole run -----------------------------------------------

@pytest.fixture
def box(tmp_path, monkeypatch):
    """A tiny marvy.db and a fresh HM report, wired in through twy_paths."""
    import twy_paths
    import twy_platform.contribution as contribution
    import importlib
    # twy_platform re-exports the slack function under its submodule's name,
    # so fetch the module itself: main() reads slack from it at call time.
    slack_module = importlib.import_module("twy_platform.slack")

    now = datetime.now(timezone.utc)
    today = now.date()
    reports = tmp_path / "reports"
    reports.mkdir()
    snapshot = reports / f"active_subscriptions_{now:%Y%m%dT%H%M%SZ}.csv"
    with snapshot.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["Email", "First Name", "Last Name", "Product Name", "Subscription Active Until"])
        writer.writeheader()
        writer.writerow({"Email": "quiet@example.com", "First Name": "Quiet", "Last Name": "Starter",
                         "Product Name": mo.TYL_PRODUCT_NAME, "Subscription Active Until": str(today + timedelta(days=20))})
        writer.writerow({"Email": "keen@example.com", "First Name": "Keen", "Last Name": "Starter",
                         "Product Name": mo.TYL_PRODUCT_NAME, "Subscription Active Until": str(today + timedelta(days=20))})
        writer.writerow({"Email": "steady@example.com", "First Name": "Steady", "Last Name": "Member",
                         "Product Name": mo.TYL_PRODUCT_NAME, "Subscription Active Until": str(today + timedelta(days=20))})
    db = tmp_path / "marvy.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE customers (id INTEGER, email TEXT)")
    con.execute("CREATE TABLE purchases (customer_id INTEGER, product_id INTEGER, created TEXT)")
    con.execute("CREATE TABLE attendance (customer_id INTEGER, student_email TEXT, event_start_datetime TEXT, attended INTEGER, synced_at TEXT)")
    start = today - timedelta(days=20)
    for cid, email, began in ((1, "quiet@example.com", start), (2, "keen@example.com", start),
                              (3, "steady@example.com", today - timedelta(days=500))):
        con.execute("INSERT INTO customers VALUES (?, ?)", (cid, email))
        con.execute("INSERT INTO purchases VALUES (?, ?, ?)", (cid, mo.TYL_PRODUCT_ID, f"{began}T12:00:00Z"))
    con.execute("INSERT INTO attendance VALUES (2, 'keen@example.com', ?, 1, ?)",
                (f"{start + timedelta(days=3)}T15:00:00Z", now.isoformat()))
    con.execute("INSERT INTO attendance VALUES (99, 'other@example.com', ?, 1, ?)",
                (f"{today - timedelta(days=1)}T15:00:00Z", now.isoformat()))
    con.commit()
    con.close()

    posted = []
    monkeypatch.setattr(twy_paths, "load_env", lambda *a, **k: None)
    monkeypatch.setattr(twy_paths, "data_root", lambda: tmp_path)
    monkeypatch.setattr(twy_paths, "hm_subscriptions_dir", lambda: reports)
    monkeypatch.setattr(twy_paths, "marvy_db_path", lambda: db)
    monkeypatch.setattr(contribution, "continued", lambda feature=None: True)
    monkeypatch.setattr(slack_module, "slack", lambda text, channel=None: posted.append((channel, text)) or True)
    return tmp_path, posted, contribution


def test_with_the_labs_switch_off_it_posts_nothing_and_records_nothing(box, monkeypatch):
    tmp_path, posted, contribution = box
    asked = []
    monkeypatch.setattr(contribution, "continued", lambda feature=None: asked.append(feature) or False)
    assert mo.main([]) == 0
    assert posted == []
    assert asked == [None]  # the master switch itself, no feature name of its own
    assert not (tmp_path / "member_outreach").exists()


def test_a_run_names_the_quiet_starter_and_this_periods_checkins_once_each(box):
    tmp_path, posted, _ = box
    assert mo.main([]) == 0
    assert len(posted) == 2
    assert all(channel == mo.DEFAULT_CHANNEL for channel, _ in posted)
    quiet_text, checkin_text = posted[0][1], posted[1][1]
    assert "customers/1|Quiet Starter" in quiet_text and "Keen" not in quiet_text
    assert "customers/3|Steady Member" in checkin_text  # the two new members are under 30 days
    assert "Quiet" not in checkin_text and "Keen" not in checkin_text
    state = json.loads((tmp_path / "member_outreach" / "state.json").read_text())
    assert "1" in state["quiet_alerted"]
    assert list(state["checkins"].values()) == [[3]]
    assert mo.main([]) == 0
    assert len(posted) == 2  # nothing twice


def test_dry_run_prints_and_writes_nothing(box, capsys):
    tmp_path, posted, _ = box
    assert mo.main(["--dry-run"]) == 0
    assert posted == []
    assert "Quiet Starter" in capsys.readouterr().out
    assert not (tmp_path / "member_outreach").exists()
