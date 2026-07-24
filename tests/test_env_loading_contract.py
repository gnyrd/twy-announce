"""Configuration must be loaded through twy_paths, never sourced by wrappers."""

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_reminder_script_loads_configuration_through_twy_paths():
    source = (REPO_ROOT / "scripts/send_class_email_reminders.py").read_text()
    assert "from twy_paths import load_env" in source
    assert "load_env()" in source


def test_shell_entrypoints_do_not_source_env_files_directly():
    for relative_path in (
        "scripts/run_class_email_reminders.sh",
        "src/youtube_daily.sh",
    ):
        source = (REPO_ROOT / relative_path).read_text()
        assert "source " not in source
        assert ". ./.env" not in source
