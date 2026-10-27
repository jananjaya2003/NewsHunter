"""Share checked editions through Vercel Blob.

The publisher's site sits behind Cloudflare bot protection that refuses cloud
servers, and Windows OCR (needed to read tender adverts) only runs on Windows.
So the office PC checks editions and uploads each result here, and the Vercel
deployment only reads them. Blobs are private: the site's password still
protects them.

Credentials: a classic BLOB_READ_WRITE_TOKEN, or the store's OIDC setup
(BLOB_STORE_ID plus a short-lived Vercel OIDC token). On Vercel the OIDC token
arrives with each request; on the office PC `vercel env pull .env.vercel`
refreshes it before each run (see scripts/windows/run_epaper_monitor.ps1).
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
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Protocol

import pymupdf
import requests
from dotenv import dotenv_values

from app.core.config import IS_VERCEL, settings
from app.monitor.epaper_monitor import (
    COLOMBO,
    PUBLICATIONS,
    Publication,
    _atomic_write_text,
    colombo_today,
    image_cache_path,
    load_manifest,
    manifest_path,
    result_complete,
)

LOGGER = logging.getLogger(__name__)
ROOT = "news-hunter"
INDEX = f"{ROOT}/index.json"
MAX_IMAGE_WIDTH = 2000
KEEP_DAYS = int(os.getenv("BLOB_KEEP_DAYS", "45"))
INDEX_MAX_AGE_SECONDS = 60
# Written by `vercel env pull`; it also sets VERCEL=1, so only Blob keys are read.
PULLED_ENV = Path(__file__).resolve().parents[2] / ".env.vercel"
PULLED_KEYS = ("BLOB_STORE_ID", "VERCEL_OIDC_TOKEN")


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
                # Results and the index are overwritten; the CDN otherwise keeps
                # serving the old copy for up to a month.
                cache_control_max_age=60 if content_type == "application/json" else None,
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


def _read_write_token() -> str:
    return os.getenv("BLOB_READ_WRITE_TOKEN") or os.getenv("VERCEL_BLOB_READ_WRITE_TOKEN") or ""


def _load_pulled_credentials() -> None:
    """On the office PC, use the OIDC Blob credentials from `vercel env pull`."""
    if IS_VERCEL or _read_write_token() or not PULLED_ENV.is_file():
        return
    values = dotenv_values(PULLED_ENV)
    for key in PULLED_KEYS:
        if values.get(key) and not os.getenv(key):
            os.environ[key] = values[key]


def enabled() -> bool:
    _load_pulled_credentials()
    return bool(_read_write_token() or os.getenv("BLOB_STORE_ID"))


def remote_mode() -> bool:
    """True on the Vercel site: read results from Blob, never contact the publisher."""
    return IS_VERCEL and enabled()


def manifest_blob(publication: Publication, edition_date: date) -> str:
    return f"{ROOT}/results/{publication.key}/{edition_date.isoformat()}.json"


def request_blob(publication: Publication, edition_date: date) -> str:
    return f"{ROOT}/requests/{publication.key}/{edition_date.isoformat()}.json"


def oldest_kept_date() -> date:
    return colombo_today() - timedelta(days=KEEP_DAYS)


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


def _summary(manifest: dict, missing_images: int | None) -> dict:
    return {
        "generated_at": manifest.get("generated_at"),
        "complete": result_complete(manifest),
        "count": manifest.get("article_count", 0),
        "tenders": manifest.get("category_counts", {}).get("medical_tender", 0),
        "missing_images": missing_images,
    }


def _reconcile_index(index: dict, backend: Backend, report_root: Path) -> list[str]:
    """Make the index list exactly the uploaded results.

    Blob may serve an overwritten index from its CDN for a while, so the copy
    just read can lack editions uploaded moments earlier (this dropped two
    editions from the site once). The blob listing is always current, so it
    decides which editions exist; summaries come from the local result, the
    old index entry, or the uploaded result itself. Returns readable warnings.
    """
    results = f"{ROOT}/results/"
    old = index["editions"]
    rebuilt: dict[str, dict] = {}
    warnings = []
    for pathname in sorted(backend.list(results)):
        key, _, name = pathname[len(results):].partition("/")
        publication = PUBLICATIONS.get(key)
        try:
            edition_date = date.fromisoformat(name.removesuffix(".json"))
        except ValueError:
            publication = None
        if publication is None:
            warnings.append(f"Unrecognised saved result {pathname} was left out of the index.")
            continue
        value = edition_date.isoformat()
        previous = old.get(key, {}).get(value)
        local = load_manifest(report_root, publication, edition_date)
        if local is not None:
            same = previous and previous.get("generated_at") == local.get("generated_at")
            entry = _summary(local, previous.get("missing_images") if same else None)
        elif previous is not None:
            entry = previous
        else:
            raw = backend.get(pathname)
            try:
                entry = _summary(json.loads(raw), None) if raw else None
            except ValueError:
                entry = None
            if entry is None:
                warnings.append(
                    f"{publication.name} {value} is uploaded but unreadable; upload it again."
                )
                continue
        rebuilt.setdefault(key, {})[value] = entry
    index["editions"] = rebuilt
    return warnings


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
    warnings = _reconcile_index(index, backend, report_root)
    # Set explicitly in case the listing has not caught up with the upload above.
    entry = _summary(manifest, len(failures))
    index["editions"].setdefault(publication.key, {})[edition_date.isoformat()] = entry
    removed = _prune(index, backend)
    backend.put(INDEX, json.dumps(index, indent=1).encode("utf-8"), "application/json")
    return {
        "images_uploaded": uploaded,
        "image_failures": failures,
        "removed_editions": removed,
        "warnings": warnings,
        "index_entry": entry,
    }


# Check requests: the online site cannot reach the publisher, so "Check now"
# leaves a request here and the office PC's request worker runs it.


def request_check(
    publication: Publication, edition_date: date, backend: Backend, *, force: bool, reason: str
) -> dict:
    entry = {
        "publication": publication.key,
        "edition_date": edition_date.isoformat(),
        "status": "queued",
        "force": force,
        "reason": reason,
        "requested_at": datetime.now(COLOMBO).isoformat(timespec="seconds"),
        "error": "",
    }
    backend.put(
        request_blob(publication, edition_date),
        json.dumps(entry).encode("utf-8"),
        "application/json",
    )
    return entry


def pending_requests(backend: Backend, publication: Publication | None = None) -> list[dict]:
    """Every saved request (queued or failed), oldest first."""
    prefix = f"{ROOT}/requests/" + (f"{publication.key}/" if publication else "")
    found = []
    for pathname in backend.list(prefix):
        raw = backend.get(pathname)
        try:
            entry = json.loads(raw) if raw else None
        except ValueError:
            entry = None
        key, _, name = pathname[len(f"{ROOT}/requests/"):].partition("/")
        if not isinstance(entry, dict):
            # Keep it visible: an unreadable request must not vanish silently.
            entry = {
                "publication": key, "edition_date": name.removesuffix(".json"),
                "status": "failed", "force": False, "requested_at": "",
                "error": "The saved request is unreadable; press Check now again.",
            }
        found.append(entry)
    return sorted(found, key=lambda entry: entry.get("requested_at") or "")


def finish_request(publication: Publication, edition_date: date, backend: Backend) -> None:
    backend.delete([request_blob(publication, edition_date)])


def fail_request(entry: dict, error: str, backend: Backend) -> None:
    publication = PUBLICATIONS[entry["publication"]]
    failed = dict(entry, status="failed", error=error,
                  failed_at=datetime.now(COLOMBO).isoformat(timespec="seconds"))
    backend.put(
        request_blob(publication, date.fromisoformat(entry["edition_date"])),
        json.dumps(failed).encode("utf-8"),
        "application/json",
    )


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

    def remember(self, publication: Publication, edition_date: date, summary: dict) -> None:
        """Show a result this server just uploaded before the index cache catches up."""
        with self._lock:
            if self._index is not None:
                self._index["editions"].setdefault(publication.key, {})[
                    edition_date.isoformat()] = summary

    def requests(self, publication: Publication) -> dict[str, dict]:
        return {
            entry["edition_date"]: entry
            for entry in pending_requests(self.backend, publication)
        }

    def request_check(
        self, publication: Publication, edition_date: date, *, force: bool, reason: str
    ) -> dict:
        return request_check(publication, edition_date, self.backend, force=force, reason=reason)

    def ensure_manifest(self, publication: Publication, edition_date: date) -> None:
        entry = self.editions(publication).get(edition_date.isoformat())
        local = manifest_path(self.report_root, publication, edition_date)
        if entry is None:
            # The cached index can lag behind a fresh upload, so ask for the
            # result itself before saying the date is unchecked.
            if local.is_file():
                return
            raw = self.backend.get(manifest_blob(publication, edition_date))
            if raw is not None:
                _atomic_write_text(local, raw.decode("utf-8"))
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
        print(
            "No Blob credentials: run `npx vercel env pull .env.vercel --environment=production`"
            " or set BLOB_READ_WRITE_TOKEN in .env; nothing uploaded."
        )
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
            for warning in summary.get("warnings", []):
                print(f"WARNING: {warning}")
            if failures:
                status = 1
    return status


if __name__ == "__main__":
    sys.exit(main())
