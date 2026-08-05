"""Current daily status membership snapshot freshness coverage."""

import daily_status_report


ACTIVE_HDR = (
    "Billing Cycle,Created,Email,First Name,Last Name,Paid,Price,"
    "Product Name,Renewal Date,Status,Subscription Active Until,split_part"
)


def test_current_membership_report_rejects_stale_snapshot(tmp_path, monkeypatch):
    path = tmp_path / "active_subscriptions_20200101T010000Z.csv"
    path.write_text(ACTIVE_HDR + "\n", encoding="utf-8")
    monkeypatch.setattr(daily_status_report, "REPORTS_DIR", tmp_path)

    try:
        daily_status_report.get_marvelous_data()
    except RuntimeError as exc:
        assert "stale" in str(exc)
    else:
        raise AssertionError("stale membership snapshot was accepted")
