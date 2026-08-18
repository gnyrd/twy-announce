"""Configuration must be loaded through twy_paths, never sourced by wrappers."""

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_shell_entrypoints_do_not_source_env_files_directly():
    for relative_path in (
        "src/youtube_daily.sh",
    ):
        source = (REPO_ROOT / relative_path).read_text()
        assert "source " not in source
        assert ". ./.env" not in source
