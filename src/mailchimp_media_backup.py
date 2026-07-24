#!/usr/bin/env python3
"""Back up Mailchimp assets, landing pages, and user-template defaults."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

import requests

from mailchimp_backup import (
    BackupGap,
    MailchimpReadOnlyClient,
    SnapshotWriter,
    paginate,
)


def asset_relative_path(item: dict[str, Any]) -> str:
    """Use only the numeric Mailchimp id and a conservative URL extension."""
    asset_id = str(item["id"])
    suffix = Path(urlparse(item["full_size_url"]).path).suffix.lower()
    if not re.fullmatch(r"\.[a-z0-9]{1,8}", suffix):
        suffix = {
            "image": ".bin",
            "file": ".bin",
        }.get(item.get("type"), ".bin")
    return f"assets/{asset_id}{suffix}"


def verify_file_inventory(
    files: list[dict[str, Any]],
    *,
    declared_count: int,
    declared_bytes: int,
) -> None:
    if len(files) != declared_count:
        raise BackupGap(
            f"file inventory has {len(files)} of {declared_count} items"
        )
    actual_bytes = sum(int(item.get("size") or 0) for item in files)
    if actual_bytes != declared_bytes:
        raise BackupGap(
            f"file byte total is {actual_bytes}, expected {declared_bytes}"
        )


def download_asset(
    item: dict[str, Any],
    run_dir: Path,
    *,
    get: Callable[..., Any] = requests.get,
    attempts: int = 3,
) -> dict[str, Any]:
    """Download one immutable asset, verify its API-declared byte size."""
    relative = asset_relative_path(item)
    destination = Path(run_dir) / relative
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(destination.parent, 0o700)
    if destination.exists():
        raise FileExistsError(f"refusing to overwrite {destination}")
    partial = destination.with_suffix(destination.suffix + ".partial")
    expected = int(item.get("size") or 0)
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        digest = hashlib.sha256()
        size = 0
        try:
            with get(
                item["full_size_url"],
                stream=True,
                timeout=120,
            ) as response:
                response.raise_for_status()
                with partial.open("xb") as handle:
                    os.chmod(partial, 0o600)
                    for chunk in response.iter_content(1024 * 1024):
                        if not chunk:
                            continue
                        handle.write(chunk)
                        digest.update(chunk)
                        size += len(chunk)
                    handle.flush()
                    os.fsync(handle.fileno())
            if size != expected:
                raise BackupGap(
                    f"asset {item['id']} downloaded {size} bytes, "
                    f"expected {expected} bytes"
                )
            os.replace(partial, destination)
            os.chmod(destination, 0o600)
            return {
                "id": item["id"],
                "path": relative,
                "bytes": size,
                "sha256": digest.hexdigest(),
                "attempt": attempt,
            }
        except Exception as error:
            last_error = error
            partial.unlink(missing_ok=True)
            if attempt < attempts:
                time.sleep(attempt)
    assert last_error is not None
    raise last_error


def _metadata_ledger(run_dir: Path) -> list[dict[str, Any]]:
    ledger = []
    for path in sorted(Path(run_dir).rglob("*")):
        if (
            not path.is_file()
            or "assets" in path.relative_to(run_dir).parts
            or path.name == "manifest.json"
        ):
            continue
        ledger.append(
            {
                "path": str(path.relative_to(run_dir)),
                "bytes": path.stat().st_size,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
        )
    return ledger


def classify_landing_page_content(
    page_id: str,
    content: dict[str, Any],
) -> tuple[str, str | None]:
    """Distinguish rendered HTML from Mailchimp-confirmed empty drafts."""
    if content.get("_backup_http_status") == 400:
        detail = (content.get("_backup_payload") or {}).get("detail")
        if detail == "There is no html for this template":
            return "verified-no-html", None
        raise BackupGap(
            f"landing page {page_id} content returned 400: {detail!r}"
        )
    html = content.get("html")
    if not isinstance(html, str) or not html.strip():
        raise BackupGap(f"landing page {page_id} missing HTML")
    return "rendered-html", html


def capture_media(
    *,
    client: MailchimpReadOnlyClient,
    writer: SnapshotWriter,
    workers: int,
) -> dict[str, Any]:
    gaps: list[str] = []
    summary = client.get(
        "/file-manager/files",
        params={"count": 1, "offset": 0},
    )
    files = paginate(
        client,
        "/file-manager/files",
        "files",
        page_size=1000,
    )
    verify_file_inventory(
        files,
        declared_count=summary["total_items"],
        declared_bytes=summary["total_file_size"],
    )
    writer.write_json(
        "file-manager/files.json",
        {
            "files": files,
            "total_items": len(files),
            "total_file_size": summary["total_file_size"],
        },
    )

    asset_ledger: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(download_asset, item, writer.run_dir): item
            for item in files
        }
        for future in as_completed(futures):
            item = futures[future]
            try:
                asset_ledger.append(future.result())
            except Exception as error:
                gaps.append(f"asset {item.get('id')}: {error}")
    asset_ledger.sort(key=lambda item: int(item["id"]))
    writer.write_json("file-manager/asset-ledger.json", asset_ledger)
    downloaded_bytes = sum(item["bytes"] for item in asset_ledger)
    if len(asset_ledger) != len(files):
        gaps.append(
            f"downloaded {len(asset_ledger)} of {len(files)} gallery assets"
        )
    if downloaded_bytes != summary["total_file_size"]:
        gaps.append(
            f"downloaded {downloaded_bytes} of "
            f"{summary['total_file_size']} gallery bytes"
        )

    landing_pages = paginate(
        client,
        "/landing-pages",
        "landing_pages",
        page_size=100,
    )
    writer.write_json(
        "landing-pages.json",
        {
            "landing_pages": landing_pages,
            "total_items": len(landing_pages),
        },
    )
    landing_content = 0
    landing_verified_no_html = 0
    for page in landing_pages:
        page_id = str(page["id"])
        try:
            content = client.get(
                f"/landing-pages/{page_id}/content",
                allow_status=(400,),
            )
            state, html = classify_landing_page_content(page_id, content)
            writer.write_json(
                f"landing-pages/{page_id}/content.json",
                content,
            )
            if state == "rendered-html":
                assert html is not None
                writer.write_text(
                    f"landing-pages/{page_id}/rendered.html",
                    html,
                )
                landing_content += 1
            else:
                landing_verified_no_html += 1
        except Exception as error:
            gaps.append(f"landing page {page_id}: {error}")

    templates = paginate(client, "/templates", "templates", page_size=1000)
    user_templates = [
        template for template in templates if template.get("type") == "user"
    ]
    writer.write_json(
        "user-templates.json",
        {
            "templates": user_templates,
            "total_items": len(user_templates),
        },
    )
    template_defaults = 0
    for template in user_templates:
        template_id = str(template["id"])
        try:
            content = client.get(
                f"/templates/{template_id}/default-content"
            )
            writer.write_json(
                f"user-templates/{template_id}/default-content.json",
                content,
            )
            template_defaults += 1
        except Exception as error:
            gaps.append(f"template {template_id}: {error}")

    methods = sorted({entry["method"] for entry in client.audit})
    if methods != ["GET"]:
        gaps.append(f"HTTP method audit is not GET-only: {methods}")
    manifest = {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "complete": not gaps,
        "counts": {
            "gallery_assets_declared": len(files),
            "gallery_assets_downloaded": len(asset_ledger),
            "gallery_bytes_declared": summary["total_file_size"],
            "gallery_bytes_downloaded": downloaded_bytes,
            "landing_pages": len(landing_pages),
            "landing_pages_with_content": landing_content,
            "landing_pages_verified_no_html": landing_verified_no_html,
            "user_templates": len(user_templates),
            "user_template_defaults": template_defaults,
        },
        "gaps": gaps,
        "http_audit": client.audit,
        "asset_ledger": asset_ledger,
        "metadata_files": _metadata_ledger(writer.run_dir),
    }
    writer.write_json("manifest.json", manifest)
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--workers", type=int, default=6)
    args = parser.parse_args(argv)
    if args.workers < 1 or args.workers > 12:
        parser.error("--workers must be between 1 and 12")

    from twy_paths import load_env

    load_env()
    writer = SnapshotWriter(args.output)
    client = MailchimpReadOnlyClient(
        server_prefix=os.environ["MAILCHIMP_SERVER_PREFIX"],
        api_key=os.environ["MAILCHIMP_API_KEY"],
    )
    try:
        manifest = capture_media(
            client=client,
            writer=writer,
            workers=args.workers,
        )
    except Exception as error:
        failure = {
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "complete": False,
            "fatal_error": f"{type(error).__name__}: {error}",
            "http_audit": client.audit,
            "metadata_files": _metadata_ledger(writer.run_dir),
        }
        writer.write_json("manifest.json", failure)
        print(
            json.dumps(
                {
                    "complete": False,
                    "fatal_error": failure["fatal_error"],
                    "output": str(args.output),
                },
                indent=2,
            )
        )
        return 3
    print(
        json.dumps(
            {
                "complete": manifest["complete"],
                "counts": manifest["counts"],
                "gaps": manifest["gaps"],
                "output": str(args.output),
            },
            indent=2,
        )
    )
    return 0 if manifest["complete"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
