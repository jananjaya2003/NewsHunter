"""Share checked editions through Vercel Blob.

The publisher's site sits behind Cloudflare bot protection that refuses cloud
servers, and Windows OCR (needed to read tender adverts) only runs on Windows.
So the office PC checks editions and uploads each result here, and the Vercel
deployment only reads them. Blobs are private: the site's password still
protects them. The token comes from BLOB_READ_WRITE_TOKEN.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from pathlib import Path
from typing import Protocol

import pymupdf
import requests

from app.core.config import IS_VERCEL, settings
from app.monitor.epaper_monitor import (
    PUBLICATIONS,
    Publication,
    _atomic_write_text,
    colombo_today,
    image_cache_path,
    load_manifest,
    manifest_path,
)

LOGGER = logging.getLogger(__name__)
ROOT = "news-hunter"
INDEX = f"{ROOT}/index.json"
MAX_IMAGE_WIDTH = 2000
KEEP_DAYS = int(os.getenv("BLOB_KEEP_DAYS", "45"))
INDEX_MAX_AGE_SECONDS = 60


class StoreError(RuntimeError):
    pass


class Backend(Protocol):
    def get(self, pathname: str) -> bytes | None: ...
    def put(self, pathname: str, body: bytes, content_type: str) -> None: ...
    def list(self, prefix: str) -> list[str]: ...
    def delete(self, pathnames: list[str]) -> None: ...


class VercelBlobBackend:
    def __init__(self) -> None:
        # Imported here so the app still starts where the package is missing.
        from vercel.blob import sync as blob

        self.blob = blob

    def get(self, pathname: str) -> bytes | None:
        try:
            return self.blob.get_blob(pathname, access="private").body
        except self.blob.BlobNotFoundError:
            return None
        except self.blob.BlobError as exc:
            raise StoreError(f"Saved-results storage failed: {exc}") from exc

    def put(self, pathname: str, body: bytes, content_type: str) -> None:
        try:
            self.blob.put_blob(
                pathname, body, access="private", content_type=content_type,
                allow_overwrite=True,
            )
        except self.blob.BlobError as exc:
            raise StoreError(f"Upload of {pathname} failed: {exc}") from exc

    def list(self, prefix: str) -> list[str]:
        try:
            return [item.pathname for item in self.blob.query_blobs(prefix=prefix)]
        except self.blob.BlobError as exc:
            raise StoreError(f"Listing saved results failed: {exc}") from exc

    def delete(self, pathnames: list[str]) -> None:
        for start in range(0, len(pathnames), 1000):
            try:
                self.blob.delete_blob(pathnames[start : start + 1000])
            except self.blob.BlobError as exc:
                raise StoreError(f"Deleting old results failed: {exc}") from exc


def enabled() -> bool:
    return bool(os.getenv("BLOB_READ_WRITE_TOKEN") or os.getenv("VERCEL_BLOB_READ_WRITE_TOKEN"))


def remote_mode() -> bool:
    """True on the Vercel site: read results from Blob, never contact the publisher."""
    return IS_VERCEL and enabled()


def manifest_blob(publication: Publication, edition_date: date) -> str:
    return f"{ROOT}/results/{publication.key}/{edition_date.isoformat()}.json"


def image_blob(publication: Publication, edition_date: date, url: str) -> str:
    # Grouped by edition so old editions can be deleted by prefix.
    name = image_cache_path(Path(), url).name
    return f"{ROOT}/images/{publication.key}/{edition_date.isoformat()}/{name}"


def _image_urls(manifest: dict) -> list[str]:
    urls = [item.get("image_url") for item in manifest.get("articles", [])]
    urls += [item.get("image_url") for item in manifest.get("unscreened", [])]
    return list(dict.fromkeys(url for url in urls if url))


def _fetch_image(url: str) -> bytes:
    response = requests.get(
        url, timeout=(5, 30),
        headers={"Referer": "https://epaperst.lakehouse.lk/", "User-Agent": "Mozilla/5.0"},
    )
    response.raise_for_status()
    if not response.headers.get("Content-Type", "").startswith("image/") or not response.content:
        raise StoreError("publisher did not return an image")
    return response.content


def _web_jpeg(content: bytes) -> bytes:
    """Re-encode as JPEG no wider than MAX_IMAGE_WIDTH to keep storage small."""
    pixmap = pymupdf.Pixmap(content)
    if pixmap.alpha:
        pixmap = pymupdf.Pixmap(pixmap, 0)
    factor = 0
    while pixmap.width >> factor > MAX_IMAGE_WIDTH:
        factor += 1
    if factor:
        pixmap.shrink(factor)
    return pixmap.tobytes("jpg", jpg_quality=85)


def _load_index(backend: Backend) -> dict:
    raw = backend.get(INDEX)
    if not raw:
        return {"editions": {}}
    try:
        index = json.loads(raw)
    except ValueError as exc:
        raise StoreError("The saved-results index is unreadable.") from exc
    index.setdefault("editions", {})
    return index


def publish_edition(
    report_root: Path,
    publication: Publication,
    edition_date: date,
    backend: Backend | None = None,
    fetch_image=_fetch_image,
) -> dict:
    """Upload one local result and its images; returns a summary.

    Images that cannot be uploaded are listed in the summary so they are never
    lost silently; the page then offers the e-paper link for those items.
    """
    if edition_date < colombo_today() - timedelta(days=KEEP_DAYS):
        raise StoreError(f"Older than {KEEP_DAYS} days, so the online site does not keep it.")
    backend = backend or VercelBlobBackend()
    manifest = load_manifest(report_root, publication, edition_date)
    if manifest is None:
        raise StoreError(f"No saved result for {publication.name} {edition_date}.")
    cache_root = report_root / "_cache"
    prefix = f"{ROOT}/images/{publication.key}/{edition_date.isoformat()}/"
    existing = set(backend.list(prefix))
    failures: list[str] = []
    uploaded = 0

    def upload(url: str) -> bool:
        pathname = image_blob(publication, edition_date, url)
        if pathname in existing:
            return False
        local = image_cache_path(cache_root, url)
        content = local.read_bytes() if local.is_file() else fetch_image(url)
        backend.put(pathname, _web_jpeg(content), "image/jpeg")
        return True

    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = {pool.submit(upload, url): url for url in _image_urls(manifest)}
        for future, url in futures.items():
            try:
                uploaded += int(future.result())
            # One bad image must not stop the edition (pymupdf raises RuntimeError/ValueError).
            except (StoreError, OSError, requests.RequestException, RuntimeError, ValueError) as exc:
                LOGGER.warning("Image upload failed for %s: %s", url, exc)
                failures.append(url)

    backend.put(
        manifest_blob(publication, edition_date),
        json.dumps(manifest, ensure_ascii=False).encode("utf-8"),
        "application/json",
    )
    index = _load_index(backend)
    index["editions"].setdefault(publication.key, {})[edition_date.isoformat()] = {
        "generated_at": manifest.get("generated_at"),
        "complete": bool(manifest.get("complete")),
        "count": manifest.get("article_count", 0),
        "tenders": manifest.get("category_counts", {}).get("medical_tender", 0),
        "missing_images": len(failures),
    }
    removed = _prune(index, backend)
    backend.put(INDEX, json.dumps(index, indent=1).encode("utf-8"), "application/json")
    return {"images_uploaded": uploaded, "image_failures": failures, "removed_editions": removed}


def _prune(index: dict, backend: Backend, keep_days: int = KEEP_DAYS) -> list[str]:
    cutoff = (colombo_today() - timedelta(days=keep_days)).isoformat()
    removed = []
    for key, editions in index["editions"].items():
        publication = PUBLICATIONS.get(key)
        for value in [value for value in editions if value < cutoff]:
            del editions[value]
            removed.append(f"{key} {value}")
            if publication is None:
                continue
            old = backend.list(f"{ROOT}/images/{key}/{value}/")
            old.append(manifest_blob(publication, date.fromisoformat(value)))
            backend.delete(old)
    return removed


class RemoteResults:
    """Read side used by the Vercel site. Copies blobs into the local report dir."""

    def __init__(self, report_root: Path, backend: Backend | None = None) -> None:
        self.report_root = report_root
        self._backend = backend
        self._index: dict | None = None
        self._index_at = 0.0
        self._lock = threading.Lock()

    @property
    def backend(self) -> Backend:
        if self._backend is None:
            self._backend = VercelBlobBackend()
        return self._backend

    def index(self) -> dict:
        with self._lock:
            if self._index is None or time.monotonic() - self._index_at > INDEX_MAX_AGE_SECONDS:
                self._index = _load_index(self.backend)
                self._index_at = time.monotonic()
            return self._index

    def editions(self, publication: Publication) -> dict[str, dict]:
        return self.index()["editions"].get(publication.key, {})

    def ensure_manifest(self, publication: Publication, edition_date: date) -> None:
        entry = self.editions(publication).get(edition_date.isoformat())
        local = manifest_path(self.report_root, publication, edition_date)
        if entry is None:
            local.unlink(missing_ok=True)
            return
        cached = load_manifest(self.report_root, publication, edition_date)
        if cached and cached.get("generated_at") == entry.get("generated_at"):
            return
        raw = self.backend.get(manifest_blob(publication, edition_date))
        if raw is None:
            raise StoreError("The saved result is listed but its file is missing.")
        _atomic_write_text(local, raw.decode("utf-8"))

    def ensure_image(self, publication: Publication, edition_date: date, url: str) -> Path | None:
        local = image_cache_path(self.report_root / "_cache", url)
        if local.is_file():
            return local
        content = self.backend.get(image_blob(publication, edition_date, url))
        if content is None:
            return None
        local.parent.mkdir(parents=True, exist_ok=True)
        temporary = local.with_name(f".{local.name}.{os.getpid()}.part")
        temporary.write_bytes(content)
        temporary.replace(local)
        return local


def _local_editions(report_root: Path, publication: Publication, keep_days: int) -> list[date]:
    cutoff = colombo_today() - timedelta(days=keep_days)
    found = []
    for folder in (report_root / publication.key).glob("????-??-??"):
        try:
            value = date.fromisoformat(folder.name)
        except ValueError:
            continue
        if value >= cutoff and (folder / "manifest.json").is_file():
            found.append(value)
    return sorted(found)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Upload checked editions for the Vercel site.")
    parser.add_argument("--publication", choices=sorted(PUBLICATIONS), action="append")
    parser.add_argument("--date", action="append", help="Edition date YYYY-MM-DD (repeatable).")
    parser.add_argument("--output", default=str(settings.report_dir), help="Result directory.")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if not enabled():
        print("BLOB_READ_WRITE_TOKEN is not set (add it to .env); nothing uploaded.")
        return 2
    report_root = Path(args.output)
    status = 0
    for key in args.publication or sorted(PUBLICATIONS):
        publication = PUBLICATIONS[key]
        dates = (
            [date.fromisoformat(value) for value in args.date]
            if args.date else _local_editions(report_root, publication, KEEP_DAYS)
        )
        for value in dates:
            try:
                summary = publish_edition(report_root, publication, value)
            except (StoreError, OSError, requests.RequestException) as exc:
                print(f"FAILED {publication.name} {value}: {exc}")
                status = 1
                continue
            failures = summary["image_failures"]
            print(
                f"Uploaded {publication.name} {value}: {summary['images_uploaded']} new image(s)"
                + (f"; {len(failures)} image(s) FAILED, review those items in the e-paper"
                   if failures else "")
            )
            if failures:
                status = 1
    return status


if __name__ == "__main__":
    sys.exit(main())
