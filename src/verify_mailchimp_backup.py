#!/usr/bin/env python3
"""Verify TWY Mailchimp backup ledgers without modifying artifacts."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import sys
import zipfile
from pathlib import Path

from twy_paths import load_env


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def check_ledger(root: Path, entries: list[dict]) -> list[str]:
    errors = []
    for entry in entries:
        path = root / entry["path"]
        if not path.is_file():
            errors.append(f"missing: {path}")
            continue
        if path.stat().st_size != entry["bytes"]:
            errors.append(f"size mismatch: {path}")
        if sha256(path) != entry["sha256"]:
            errors.append(f"sha256 mismatch: {path}")
    return errors


def scan_sensitive(root: Path, api_key: bytes) -> list[str]:
    errors = []
    needles = {
        "mailchimp API key": api_key,
        "Authorization header": b"Authorization:",
        "account export download URL field": b'"download_url"',
    }
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        body = path.read_bytes()
        for label, needle in needles.items():
            if needle and needle in body:
                errors.append(f"{label}: {path}")
    return errors


def main() -> int:
    if len(sys.argv) != 4:
        raise SystemExit("usage: verify API_DIR MEDIA_DIR OFFICIAL_ZIP")
    api_dir, media_dir, official_zip = map(Path, sys.argv[1:])
    errors: list[str] = []

    api = json.loads((api_dir / "manifest.json").read_text())
    errors.extend(check_ledger(api_dir, api["artifact_files"]))
    if api["complete"] is not False or len(api["gaps"]) != 5:
        errors.append("API manifest does not retain exactly five UI gaps")
    if {entry["method"] for entry in api["http_audit"]} != {"GET"}:
        errors.append("API snapshot method audit is not GET-only")

    media = json.loads((media_dir / "manifest.json").read_text())
    errors.extend(check_ledger(media_dir, media["metadata_files"]))
    errors.extend(check_ledger(media_dir, media["asset_ledger"]))
    if media["complete"] is not True or media["gaps"]:
        errors.append("media manifest is not complete")
    if {entry["method"] for entry in media["http_audit"]} != {"GET"}:
        errors.append("media snapshot method audit is not GET-only")

    with zipfile.ZipFile(official_zip) as archive:
        corrupt = archive.testzip()
        if corrupt:
            errors.append(f"official ZIP corrupt: {corrupt}")
    expected_zip_sha = api["official_account_export"]["sha256"]
    if sha256(official_zip) != expected_zip_sha:
        errors.append("official ZIP SHA-256 mismatch")

    for root in (api_dir, media_dir):
        for path in root.rglob("*"):
            mode = stat.S_IMODE(path.stat().st_mode)
            allowed = 0o700 if path.is_dir() else 0o600
            if mode & ~allowed:
                errors.append(f"permissions too broad ({mode:o}): {path}")
    if stat.S_IMODE(official_zip.stat().st_mode) & ~0o600:
        errors.append("official ZIP permissions are too broad")

    load_env()
    backup_root = official_zip.parent
    errors.extend(
        scan_sensitive(
            backup_root,
            os.environ["MAILCHIMP_API_KEY"].encode(),
        )
    )

    result = {
        "ok": not errors,
        "api_artifacts_verified": len(api["artifact_files"]),
        "media_metadata_verified": len(media["metadata_files"]),
        "media_assets_verified": len(media["asset_ledger"]),
        "official_zip_verified": not any(
            "official ZIP" in error for error in errors
        ),
        "sensitive_scan_clean": not any(
            label in error
            for error in errors
            for label in (
                "mailchimp API key",
                "Authorization header",
                "account export download URL field",
            )
        ),
        "errors": errors,
    }
    print(json.dumps(result, indent=2))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
