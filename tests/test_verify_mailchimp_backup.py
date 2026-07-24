from __future__ import annotations

import hashlib

from verify_mailchimp_backup import (
    check_ledger,
    scan_sensitive,
    validate_api_manifest_state,
)


def test_check_ledger_detects_content_change(tmp_path):
    artifact = tmp_path / "artifact.txt"
    artifact.write_text("original")
    entry = {
        "path": "artifact.txt",
        "bytes": len("original"),
        "sha256": hashlib.sha256(b"original").hexdigest(),
    }

    assert check_ledger(tmp_path, [entry]) == []

    artifact.write_text("changed!")
    assert any("sha256 mismatch" in error for error in check_ledger(
        tmp_path, [entry]
    ))


def test_sensitive_scan_finds_secret_without_echoing_it(tmp_path):
    (tmp_path / "manifest.json").write_text("contains-secret-value")

    errors = scan_sensitive(tmp_path, b"secret-value")

    assert len(errors) == 1
    assert "mailchimp API key" in errors[0]
    assert "secret-value" not in errors[0]


def test_final_verifier_requires_complete_ui_integrated_snapshot():
    assert validate_api_manifest_state(
        {
            "complete": True,
            "gaps": [],
            "counts": {"ui_supplement_files": 42},
        }
    ) == []

    errors = validate_api_manifest_state(
        {
            "complete": False,
            "gaps": ["builder supplement missing"],
            "counts": {"ui_supplement_files": 0},
        }
    )
    assert any("not complete" in error for error in errors)
    assert any("UI supplements" in error for error in errors)
