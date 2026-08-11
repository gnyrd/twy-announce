"""save_newsletter keeps the previous words and says what changed."""
import importlib

import pytest


def module():
    return importlib.import_module("newsletter")


@pytest.fixture
def roots(tmp_path, monkeypatch):
    """Point newsletter paths at a scratch tree and silence the real Slack."""
    newsletter = module()
    content = tmp_path / "newsletters"
    versions = tmp_path / "newsletters" / "versions"
    monkeypatch.setattr(
        newsletter,
        "newsletter_path",
        lambda year, month, audience: content
        / f"{year:04d}-{month:02d}"
        / f"{str(audience).replace('-', '_')}.md",
    )
    monkeypatch.setattr(newsletter, "newsletter_versions_dir", lambda: versions)
    posted = []
    monkeypatch.setattr(
        newsletter, "_post_to_writes_channel",
        lambda channel, text: posted.append((channel, text)),
    )
    return content, versions, posted


def test_a_first_save_keeps_no_version_and_posts_a_summary(roots):
    content, versions, posted = roots

    path = module().save_newsletter(2026, 8, "lifestyle", "August", "Body words.")

    assert path.read_text() == "# August\n\nBody words."
    assert not versions.exists() or not any(versions.rglob("*.md"))
    assert len(posted) == 1
    assert posted[0][1].startswith("newsletter save 2026-08 lifestyle [new]")


def test_an_overwrite_keeps_the_previous_words(roots):
    """The whole point. Before this they were simply gone."""
    content, versions, posted = roots
    newsletter = module()
    newsletter.save_newsletter(2026, 8, "lifestyle", "August", "The first words.")

    newsletter.save_newsletter(2026, 8, "lifestyle", "August", "Rewritten words.")

    kept = list(versions.rglob("*.md"))
    assert len(kept) == 1
    assert kept[0].read_text() == "# August\n\nThe first words."
    assert kept[0].parent.parts[-2:] == ("2026-08", "lifestyle")


def test_the_overwrite_posts_a_diff_of_what_changed(roots):
    content, versions, posted = roots
    newsletter = module()
    newsletter.save_newsletter(2026, 8, "lifestyle", "Aug", "x" * 1200)
    posted.clear()

    newsletter.save_newsletter(2026, 8, "lifestyle", "August", "x" * 1310, caller="tiff")

    assert len(posted) == 1
    text = posted[0][1]
    assert text.startswith("newsletter save 2026-08 lifestyle (tiff)")
    assert '  subject: "Aug" -> "August"' in text
    assert "  body: changed (1200 -> 1310 chars)" in text


def test_saving_identical_content_posts_nothing(roots):
    content, versions, posted = roots
    newsletter = module()
    newsletter.save_newsletter(2026, 8, "lifestyle", "August", "Same words.")
    posted.clear()

    newsletter.save_newsletter(2026, 8, "lifestyle", "August", "Same words.")

    assert posted == [], "an unchanged save is not news"


def test_a_hyphenated_audience_is_stored_underscored(roots):
    """Dev rule 5: hyphens never separate identifiers in a TWY filename."""
    content, versions, posted = roots
    newsletter = module()
    newsletter.save_newsletter(2026, 8, "non-lifestyle", "August", "one")
    newsletter.save_newsletter(2026, 8, "non-lifestyle", "August", "two")

    kept = list(versions.rglob("*.md"))
    assert kept[0].parent.name == "non_lifestyle"


def test_a_versioning_failure_never_loses_the_write(roots, monkeypatch):
    content, versions, posted = roots
    newsletter = module()

    def explode(*args, **kwargs):
        raise OSError("disk gone")

    monkeypatch.setattr(newsletter, "backup_before_write", explode)

    path = newsletter.save_newsletter(2026, 8, "lifestyle", "August", "Body.")

    assert path.read_text() == "# August\n\nBody."


def test_a_slack_failure_never_loses_the_write(roots, monkeypatch):
    content, versions, posted = roots
    newsletter = module()

    def explode(channel, text):
        raise RuntimeError("slack is down")

    monkeypatch.setattr(newsletter, "_post_to_writes_channel", explode)

    path = newsletter.save_newsletter(2026, 8, "lifestyle", "August", "Body.")

    assert path.read_text() == "# August\n\nBody."


def test_reading_back_a_stored_newsletter_splits_subject_from_body(roots):
    content, versions, posted = roots
    newsletter = module()
    path = newsletter.save_newsletter(2026, 8, "lifestyle", "August", "Line one.\n\nLine two.")

    stored = newsletter._read_newsletter(path)

    assert stored == {"subject": "August", "body": "Line one.\n\nLine two."}


def test_reading_back_a_file_that_does_not_exist_is_none(tmp_path):
    assert module()._read_newsletter(tmp_path / "absent.md") is None
