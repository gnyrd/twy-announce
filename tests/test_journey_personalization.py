"""journey_personalization: fill a member's name in, or refuse to send."""
import importlib

import pytest


def personalization():
    return importlib.import_module("journey_personalization")


def test_a_member_is_greeted_by_name():
    module = personalization()

    assert module.personalize(
        "Hello {{first_name}},", first_name="Sarah"
    ) == "Hello Sarah,"


def test_surrounding_whitespace_in_a_name_is_dropped():
    module = personalization()

    assert module.personalize(
        "Hi {{first_name}},", first_name="  Dee  "
    ) == "Hi Dee,"


def test_a_single_letter_is_somebody_s_actual_name():
    """Four Marvelous customers have a one-letter first name. It is theirs."""
    module = personalization()

    assert module.personalize(
        "Hello {{first_name}},", first_name="J"
    ) == "Hello J,"


@pytest.mark.parametrize("missing", [None, "", "   "])
def test_somebody_with_no_name_still_gets_a_sentence(missing):
    module = personalization()

    assert module.personalize(
        "Hello {{first_name}},", first_name=missing
    ) == "Hello there,"


@pytest.mark.parametrize(
    "written",
    ["{{first_name}}", "{{ first_name }}", "{{First_Name}}", "{{ FIRST_NAME }}"],
)
def test_case_and_spacing_are_forgiven_because_tiff_types_these(written):
    module = personalization()

    assert module.personalize(written, first_name="Sarah") == "Sarah"


def test_a_mistyped_token_stops_the_send_rather_than_shipping():
    module = personalization()

    with pytest.raises(module.UnknownToken) as error:
        module.personalize("Hi {{frist_name}},", first_name="Sarah")

    assert "frist_name" in str(error.value)
    assert "first_name" in str(error.value)


def test_a_token_nobody_defined_stops_the_send():
    module = personalization()

    with pytest.raises(module.UnknownToken):
        module.personalize("Your city is {{city}}.", first_name="Sarah")


def test_text_with_no_tokens_is_returned_unchanged():
    module = personalization()
    body = "No tokens here. Just prose, a colon: and an apostrophe's worth."

    assert module.personalize(body, first_name="Sarah") == body


def test_every_occurrence_is_filled_not_only_the_first():
    module = personalization()

    filled = module.personalize(
        "{{first_name}}, welcome. See you soon, {{first_name}}.",
        first_name="Sarah",
    )

    assert filled == "Sarah, welcome. See you soon, Sarah."
    assert "{{" not in filled


def test_subject_preheader_and_body_are_all_personalized():
    module = personalization()
    email = {
        "subject": "{{first_name}}, welcome",
        "preheader": "A note for {{first_name}}",
        "body": "Hello {{first_name}},\n\nGlad you are here.",
        "interval_days": 3,
    }

    filled = module.personalize_email(email, first_name="Sarah")

    assert filled["subject"] == "Sarah, welcome"
    assert filled["preheader"] == "A note for Sarah"
    assert filled["body"].startswith("Hello Sarah,")


def test_personalizing_an_email_leaves_its_other_fields_alone():
    module = personalization()
    email = {"subject": "Hi", "body": "Hello", "interval_days": 3}

    filled = module.personalize_email(email, first_name="Sarah")

    assert filled["interval_days"] == 3


def test_personalizing_does_not_mutate_the_stored_email():
    module = personalization()
    email = {"subject": "Hello {{first_name}}", "body": "b"}

    module.personalize_email(email, first_name="Sarah")

    assert email["subject"] == "Hello {{first_name}}"


def test_an_email_missing_a_field_is_not_invented():
    module = personalization()

    filled = module.personalize_email({"body": "Hello {{first_name}}"})

    assert filled == {"body": "Hello there"}


def test_tokens_in_reports_what_a_piece_of_copy_needs():
    module = personalization()

    assert module.tokens_in("{{first_name}} and {{ City }}") == {
        "first_name",
        "city",
    }


def test_tokens_in_on_empty_copy_is_empty():
    module = personalization()

    assert module.tokens_in(None) == set()


def test_the_live_welcome_sequence_personalizes_end_to_end():
    """The real eight emails, so this cannot pass on invented copy alone."""
    import json

    from twy_paths import journey_path

    module = personalization()
    path = journey_path("yoga_lifestyle_welcome_2024_05")
    if not path.exists():
        pytest.skip("the imported welcome sequence is not on this host")
    journey = json.loads(path.read_text())

    for index, email in enumerate(journey["emails"], start=1):
        filled = module.personalize_email(email, first_name="Sarah")
        assert "{{" not in filled["body"], f"email {index} kept a token"
        assert "{{" not in filled["subject"], f"email {index} subject kept a token"
        if index == 1:
            assert "Hello Sarah," in filled["body"]
