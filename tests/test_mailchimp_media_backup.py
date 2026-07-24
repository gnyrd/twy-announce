from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from mailchimp_backup import BackupGap
from mailchimp_media_backup import (
    asset_relative_path,
    classify_landing_page_content,
    download_asset,
    verify_file_inventory,
)


class FakeDownloadResponse:
    def __init__(self, body: bytes, status_code: int = 200):
        self.body = body
        self.status_code = status_code

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def iter_content(self, chunk_size):
        yield self.body[:2]
        yield self.body[2:]


def test_asset_path_uses_id_and_url_extension_only():
    item = {
        "id": 42,
        "name": "../../not-safe.jpg",
        "full_size_url": "https://cdn.example/path/photo.PNG?x=1",
    }

    assert asset_relative_path(item) == "assets/42.png"


def test_verify_file_inventory_checks_count_and_total_bytes():
    files = [{"id": 1, "size": 4}, {"id": 2, "size": 6}]

    verify_file_inventory(files, declared_count=2, declared_bytes=10)

    with pytest.raises(BackupGap, match="file byte total"):
        verify_file_inventory(files, declared_count=2, declared_bytes=11)


def test_download_asset_verifies_size_and_hash(tmp_path):
    body = b"asset-data"
    item = {
        "id": 7,
        "name": "image.jpg",
        "full_size_url": "https://cdn.example/image.jpg",
        "size": len(body),
    }

    result = download_asset(
        item,
        tmp_path,
        get=lambda *args, **kwargs: FakeDownloadResponse(body),
    )

    assert (tmp_path / "assets/7.jpg").read_bytes() == body
    assert result["sha256"] == hashlib.sha256(body).hexdigest()
    assert result["bytes"] == len(body)


def test_download_asset_rejects_size_mismatch(tmp_path):
    item = {
        "id": 7,
        "name": "image.jpg",
        "full_size_url": "https://cdn.example/image.jpg",
        "size": 99,
    }

    with pytest.raises(BackupGap, match="expected 99 bytes"):
        download_asset(
            item,
            tmp_path,
            get=lambda *args, **kwargs: FakeDownloadResponse(b"short"),
        )

    assert not list(tmp_path.rglob("*.partial"))


def test_landing_page_accepts_only_explicit_mailchimp_no_html_response():
    state, html = classify_landing_page_content(
        "draft-id",
        {
            "_backup_http_status": 400,
            "_backup_payload": {
                "detail": "There is no html for this template",
            },
        },
    )

    assert state == "verified-no-html"
    assert html is None


def test_landing_page_rejects_other_400_response():
    with pytest.raises(BackupGap, match="content returned 400"):
        classify_landing_page_content(
            "draft-id",
            {
                "_backup_http_status": 400,
                "_backup_payload": {"detail": "Something else failed"},
            },
        )
