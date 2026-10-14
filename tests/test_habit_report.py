"""The Yoga Habit report one week after each class (TRIAL habit_report,
JP 2026-10-14): its numbers, its table, and one post plus one forward."""

import json
import sqlite3
import sys
from datetime import date
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import habit_report as hr

HEADER = "Billing Cycle,Created,Email,First Name,Last Name,Paid,Price,Product Name,Renewal Date,Status,Subscription Active Until,split_part"
MEMBER = "Monthly,2026-07-12T00:00:00Z,member@x.com,M,M,99,99,The Yoga Lifestyle Membership,2026-11-12T00:00:00Z,Active,2026-11-15,1month"


def make_db():
    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE attendance (event_id, event_name, event_start_datetime, customer_id, "
               "student_email, attended, registration_created)")
    db.execute("CREATE TABLE customers (id, email)")
    db.execute("CREATE TABLE purchases (customer_email, customer_name, product_name, amount_paid, created)")
    rows = [
        (1, "Habit: Open to Grace", "2026-08-08T15:00:00Z", None, "member@x.com", 1, "2026-08-03"),
        (1, "Habit: Open to Grace", "2026-08-08T15:00:00Z", None, "new@x.com", 1, "2026-08-04"),
        (2, "Habit: Back of the Heart", "2026-10-10T15:00:00Z", None, "member@x.com", 1, "2026-10-05"),
        (2, "Habit: Back of the Heart", "2026-10-10T15:00:00Z", 7, "", 0, "2026-10-06"),
        (2, "Habit: Back of the Heart", "2026-10-10T15:00:00Z", None, "late@x.com", 0, "2026-10-12"),
    ]
    db.executemany("INSERT INTO attendance VALUES (?,?,?,?,?,?,?)", rows)
    db.execute("INSERT INTO customers VALUES (7, 'acct@x.com')")
    db.executemany("INSERT INTO purchases VALUES (?,?,?,?,?)", [
        ("late@x.com", "Late", "FREE CLASS: Yoga Habit October 2026", 0, "2026-10-12"),
        ("new@x.com", "New Person", "The Yoga Lifestyle Membership", 99, "2026-08-09"),
    ])
    return db


def reports(tmp_path):
    (tmp_path / "active_subscriptions_20261009T072002Z.csv").write_text(HEADER + "\n" + MEMBER + "\n")
    return tmp_path


def test_events_and_the_one_due_a_week_later():
    events = hr.habit_events(make_db())
    assert [(e[0], e[2]) for e in events] == [(1, date(2026, 8, 8)), (2, date(2026, 10, 10))]


def test_members_come_from_the_report_on_or_before_the_class(tmp_path):
    assert hr.members_on(date(2026, 10, 10), reports(tmp_path)) == {"member@x.com"}
    assert hr.members_on(date(2026, 9, 1), reports(tmp_path)) is None


def test_class_numbers_split_members_and_non_members(tmp_path):
    db = make_db()
    s = hr.class_stats(db, 2, "Habit: Back of the Heart", date(2026, 10, 10),
                       hr.members_on(date(2026, 10, 10), reports(tmp_path)), date(2026, 10, 17))
    assert (s["registered"], s["nonmember_registered"], s["attended"], s["nonmember_attended"]) == (3, 2, 1, 0)
    assert (s["claimed"], s["nonmember_claimed"], s["registered_after_class"]) == (1, 1, 1)
    assert s["joined"] == [] and not s["joined_window_complete"]
    assert (s["registered_4plus_days_before"], s["registered_last_3_days"]) == (2, 0)
    aug = hr.class_stats(db, 1, "Habit: Open to Grace", date(2026, 8, 8), {"member@x.com"}, date(2026, 10, 17))
    assert aug["joined"] == ["New Person"] and aug["joined_window_complete"]


def test_table_and_message_carry_no_dashes_or_semicolons():
    c = {"name": "Back of the Heart", "date": "2026-10-10", "registered": 20, "nonmember_registered": 14,
         "attended": 7, "nonmember_attended": 3, "claimed": 2, "nonmember_claimed": 2,
         "joined": [], "joined_window_complete": False, "page_visits": 87, "register_clicks": 18}
    text = hr.message(c, [c], [{"email": "Email 1", "delivered": 901, "opens": 98, "clicks": 13}],
                      "\u2022 My read is the long weekend \u2014 people travel; that matters")
    assert "0 so far" in text and "Oct 10" in text
    assert "Page visits" in text and "87" in text
    assert "\u2014" not in text and "\u2013" not in text and ";" not in text


def test_why_reads_the_text_block_and_survives_no_claude():
    msg = SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="thinking"),
                                                           SimpleNamespace(type="text", text="\u2022 A read.")])
    client = SimpleNamespace(messages=SimpleNamespace(create=lambda **k: msg))
    assert hr.why({"x": 1}, client) == "\u2022 A read."
    broken = SimpleNamespace(messages=SimpleNamespace(create=lambda **k: (_ for _ in ()).throw(RuntimeError("down"))))
    assert hr.why({"x": 1}, broken) == ""


def test_posts_once_and_forwards_once(tmp_path, monkeypatch):
    import member_outreach as mo
    posted = []
    monkeypatch.setattr(mo, "post_message", lambda tok, ch, text: posted.append((tok, ch, text)) or f"17{len(posted)}.1")
    monkeypatch.setattr(hr, "load_env", lambda *a, **k: None)
    monkeypatch.setattr(hr, "state_path", lambda: tmp_path / "state.json")
    monkeypatch.setattr(hr, "build", lambda db, event, today, **k: "the report")
    monkeypatch.setattr(hr, "habit_events", lambda db: [(2, "Habit: X", date(2026, 10, 7))])
    monkeypatch.setattr(hr, "marvy_db_path", lambda: tmp_path / "marvy.db")
    sqlite3.connect(tmp_path / "marvy.db").close()
    monkeypatch.setenv("SLACK_BOT_TOKEN", "friend")
    monkeypatch.setenv("SLACK_JP_POST_TOKEN", "jp")
    assert hr.main(["--event", "2"]) == 0
    assert [(t, c) for t, c, _ in posted] == [("friend", mo.CHECKIN_CHANNEL), ("jp", mo.TIFF_DM_CHANNEL)]
    assert posted[1][2].startswith("https://tiffanywoodyogagroup.slack.com/archives/C0BDRRX79E3/p")
    assert hr.main(["--event", "2"]) == 0
    assert len(posted) == 2
    assert json.loads((tmp_path / "state.json").read_text())["posts"]["2"]["forwarded_ts"]


def test_dry_run_posts_nothing(tmp_path, monkeypatch, capsys):
    import member_outreach as mo
    monkeypatch.setattr(mo, "post_message", lambda *a: (_ for _ in ()).throw(AssertionError("posted")))
    monkeypatch.setattr(hr, "load_env", lambda *a, **k: None)
    monkeypatch.setattr(hr, "state_path", lambda: tmp_path / "state.json")
    monkeypatch.setattr(hr, "build", lambda db, event, today, **k: "the report")
    monkeypatch.setattr(hr, "habit_events", lambda db: [(2, "Habit: X", date(2026, 10, 7))])
    monkeypatch.setattr(hr, "marvy_db_path", lambda: tmp_path / "marvy.db")
    sqlite3.connect(tmp_path / "marvy.db").close()
    assert hr.main(["--event", "2", "--dry-run"]) == 0
    assert "the report" in capsys.readouterr().out
    assert not (tmp_path / "state.json").exists()
