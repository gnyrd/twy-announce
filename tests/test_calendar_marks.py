"""No registered or trademark mark reaches a calendar subscriber.

JP 2026-08-12, verbatim: "I do not want to use (R) - not anywhere." The mark
was still live on calendar.tiffanywoodyoga.com/classes.ics on 2026-09-04,
carried straight through from the plan titles onto three September events.
"""

import calendar_server


def test_the_registered_mark_never_reaches_a_subscriber():
    assert (
        calendar_server._esc("Principles of Anusara®: The Art of the Hand")
        == "Principles of Anusara: The Art of the Hand"
    )


def test_the_trademark_mark_is_stripped_too():
    assert calendar_server._esc("Yoga Habit™") == "Yoga Habit"


def test_the_spelled_out_forms_are_stripped():
    assert calendar_server._esc("Principles of Anusara(R)") == (
        "Principles of Anusara"
    )
    assert calendar_server._esc("Something(TM)") == "Something"


def test_ics_escaping_still_happens():
    # The strip runs before escaping and must not displace it.
    assert calendar_server._esc("a,b;c\\d\ne") == "a\\,b\\;c\\\\d\\ne"


def test_a_mark_next_to_a_comma_is_stripped_and_the_comma_escaped():
    assert calendar_server._esc("Anusara®, live") == "Anusara\\, live"


def test_none_is_still_empty():
    assert calendar_server._esc(None) == ""


def test_text_without_a_mark_is_untouched():
    assert calendar_server._esc("Breath: Conscious Pause") == (
        "Breath: Conscious Pause"
    )
