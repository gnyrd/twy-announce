import habit_newsletter_prompt as hnp


def test_class_times_read_as_email_copy_not_24_hour():
    assert hnp._friendly_time("09:00") == "9am"
    assert hnp._friendly_time("17:30") == "5:30pm"
    assert hnp._friendly_time("12:00") == "12pm"
    assert hnp._friendly_time("00:15") == "12:15am"


def test_unparseable_time_passes_through():
    assert hnp._friendly_time("") == ""
    assert hnp._friendly_time("TBD") == "TBD"
