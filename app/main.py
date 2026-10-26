from __future__ import annotations

import hashlib
import logging
import secrets
import threading
from base64 import b64decode
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import quote, urlsplit

import pymupdf
import requests
from fastapi import BackgroundTasks, FastAPI, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app import ad_ocr
from app.agent import NewspaperAgent
from app.config import IS_VERCEL, settings
from app.database import Database
from app.epaper_monitor import (
    COLOMBO,
    DEFAULT_PUBLICATION,
    PUBLICATIONS,
    AvailabilityCache,
    colombo_today,
    generate_latest_report,
    generate_report_for_date,
    image_cache_path,
    load_manifest,
)
from app.jobs import JobManager
from app.opportunities import CATEGORIES, DEFAULT_HIDDEN_CATEGORIES
from app.schemas import Category

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
LOGGER = logging.getLogger(__name__)

settings.prepare_directories()
MONITOR_REPORT_ROOT = settings.report_dir
MONITOR_REPORT_ROOT.mkdir(parents=True, exist_ok=True)
database = Database(settings.database_path)
database.initialize()
agent = NewspaperAgent(settings, database)
jobs = JobManager()
availability = AvailabilityCache(MONITOR_REPORT_ROOT / "_cache" / "availability.json")

app = FastAPI(title="News Hunter", version="0.3.0")
APP_ROOT = Path(__file__).resolve().parent
app.mount("/static", StaticFiles(directory=APP_ROOT / "static"), name="static")
templates = Jinja2Templates(directory=APP_ROOT / "templates")

IMAGE_HOSTS = {"d1dtajqni3g43z.cloudfront.net", "dnsofs.avahan.net", "epaperst.lakehouse.lk"}
MAX_IMAGE_BYTES = 16 * 1024 * 1024
THUMBNAIL_WIDTHS = {360, 640}
_image_locks: dict[str, threading.Lock] = {}
_image_locks_guard = threading.Lock()


def _cache_root() -> Path:
    return MONITOR_REPORT_ROOT / "_cache"


def _validate_image_url(source_url: str) -> None:
    parsed = urlsplit(source_url)
    try:
        valid_port = parsed.port in {None, 443}
    except ValueError:
        valid_port = False
    if (
        parsed.scheme != "https" or parsed.hostname not in IMAGE_HOSTS
        or parsed.username or parsed.password or not valid_port
    ):
        raise HTTPException(400, "Invalid publisher image source.")


def _download_publisher_image(source_url: str) -> bytes:
    _validate_image_url(source_url)
    try:
        with requests.get(
            source_url, timeout=(5, 30), stream=True, allow_redirects=False,
            headers={"Referer": "https://epaperst.lakehouse.lk/", "User-Agent": "Mozilla/5.0"},
        ) as upstream:
            if upstream.status_code != 200:
                raise HTTPException(502, "Publisher image unavailable; review the source link.")
            media_type = upstream.headers.get("Content-Type", "").split(";", 1)[0].lower()
            if media_type not in {"image/jpeg", "image/png", "image/webp", "image/gif"}:
                raise HTTPException(502, "Publisher did not return an article image.")
            content = bytearray()
            for chunk in upstream.iter_content(65536):
                content.extend(chunk)
                if len(content) > MAX_IMAGE_BYTES:
                    raise HTTPException(502, "Article image exceeds delivery limit; use the source link.")
            if not content:
                raise HTTPException(502, "Publisher returned an empty image.")
    except requests.RequestException as exc:
        raise HTTPException(502, "Publisher image request failed; use the source link.") from exc
    return bytes(content)


def _cached_image(source_url: str) -> Path:
    """Fetch a publisher image once and keep it in the local cache."""
    path = image_cache_path(_cache_root(), source_url)
    if path.is_file():
        return path
    with _image_locks_guard:
        lock = _image_locks.setdefault(path.name, threading.Lock())
    with lock:
        if not path.is_file():
            content = _download_publisher_image(source_url)
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_name(f".{secrets.token_hex(5)}.part")
            try:
                temporary.write_bytes(content)
                temporary.replace(path)
            finally:
                temporary.unlink(missing_ok=True)
    return path


def _thumbnail(source: Path, width: int) -> Path:
    destination = source.parent.parent / "thumbs" / f"{source.stem}-{width}.jpg"
    if destination.is_file():
        return destination
    pixmap = pymupdf.Pixmap(source.read_bytes())
    if pixmap.alpha:
        pixmap = pymupdf.Pixmap(pixmap, 0)
    factor = 0
    while pixmap.width >> (factor + 1) >= width:
        factor += 1
    if factor:
        pixmap.shrink(factor)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{secrets.token_hex(5)}.part")
    try:
        temporary.write_bytes(pixmap.tobytes("jpg", jpg_quality=78))
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    return destination


def _parse_edition_date(value: str) -> date:
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise HTTPException(400, "Invalid edition date.") from exc
    if parsed.isoformat() != value:
        raise HTTPException(400, "Invalid edition date.")
    # The publisher often uploads tomorrow's paper the evening before.
    if parsed > colombo_today() + timedelta(days=1):
        raise HTTPException(400, "Edition date cannot be in the future.")
    return parsed


def _publication(key: str):
    selected = PUBLICATIONS.get(key)
    if selected is None:
        raise HTTPException(400, "Unknown newspaper selection.")
    return selected


def _manifest_item(publication_key: str, edition_date: str, uid: str) -> dict:
    publication = _publication(publication_key)
    manifest = load_manifest(MONITOR_REPORT_ROOT, publication, _parse_edition_date(edition_date))
    if manifest is None:
        raise HTTPException(404, "This edition has not been checked yet.")
    for item in manifest.get("articles", []):
        if item.get("uid") == uid:
            return item
    for item in manifest.get("unscreened", []):
        if f"unscreened-{item.get('object_id')}-{item.get('page_id')}" == uid:
            return item
    raise HTTPException(404, "Item not found in this edition.")


@app.get("/api/image/{publication}/{edition_date}/{uid}")
def edition_image(publication: str, edition_date: str, uid: str, w: int = 0, download: int = 0):
    """Same-origin image so the browser can crop and resize it on a canvas."""
    item = _manifest_item(publication, edition_date, uid)
    source_url = item.get("image_url") or ""
    if not source_url:
        raise HTTPException(404, "Article image unavailable; use the source link.")
    path = _cached_image(source_url)
    if w:
        if w not in THUMBNAIL_WIDTHS:
            raise HTTPException(400, "Unsupported thumbnail width.")
        try:
            path = _thumbnail(path, w)
        except (RuntimeError, ValueError) as exc:
            LOGGER.warning("Thumbnail failed for %s: %s", uid, exc)
    headers = {"Cache-Control": "private, max-age=86400"}
    if download:
        headers["Content-Disposition"] = f'attachment; filename="{publication}-{edition_date}-{uid}.jpg"'
    return Response(path.read_bytes(), media_type="image/jpeg", headers=headers)


def _unauthorized() -> Response:
    return Response(
        status_code=401,
        headers={"WWW-Authenticate": 'Basic realm="Daily Brief Editor"'},
        content="Editor authentication required.",
    )


def _same_origin_request(request: Request) -> bool:
    expected = f"{request.url.scheme}://{request.url.netloc}".lower()
    origin = request.headers.get("origin", "").rstrip("/").lower()
    if origin:
        return origin == expected
    referer = request.headers.get("referer", "")
    if referer:
        parsed = urlsplit(referer)
        return f"{parsed.scheme}://{parsed.netloc}".lower() == expected
    return False


@app.middleware("http")
async def protect_admin(request: Request, call_next):
    public_path = (
        request.url.path.startswith("/brief/")
        or request.url.path.startswith("/static/")
        or request.url.path == "/health"
    )
    if public_path or not settings.admin_password:
        return await call_next(request)

    authorization = request.headers.get("Authorization", "")
    if not authorization.startswith("Basic "):
        return _unauthorized()
    try:
        username, password = b64decode(authorization[6:]).decode("utf-8").split(":", 1)
    except (ValueError, UnicodeDecodeError):
        return _unauthorized()
    if not (
        secrets.compare_digest(username, "admin")
        and secrets.compare_digest(password, settings.admin_password)
    ):
        return _unauthorized()
    if request.method not in {"GET", "HEAD", "OPTIONS"} and not _same_origin_request(request):
        return Response(status_code=403, content="Cross-site request blocked.")
    return await call_next(request)


@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    response.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; "
        "script-src 'self'; object-src 'none'; base-uri 'self'; "
        "form-action 'self'; frame-ancestors 'none'",
    )
    if request.url.path.startswith("/admin"):
        response.headers.setdefault("Cache-Control", "no-store")
    return response


@app.get("/", include_in_schema=False)
def root() -> RedirectResponse:
    return RedirectResponse("/monitor", status_code=303)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


def _web_manifest(publication_key: str, manifest: dict) -> dict:
    """Manifest plus the same-origin URLs the page needs."""
    edition_date = manifest["edition_date"]
    for item in manifest.get("articles", []):
        base = f"/api/image/{publication_key}/{edition_date}/{item['uid']}"
        item["image_web_url"] = base if item.get("image_url") else ""
        item["thumb_web_url"] = f"{base}?w=640" if item.get("image_url") else ""
    for item in manifest.get("unscreened", []):
        item["uid"] = f"unscreened-{item.get('object_id')}-{item.get('page_id')}"
        base = f"/api/image/{publication_key}/{edition_date}/{item['uid']}"
        item["thumb_web_url"] = f"{base}?w=360" if item.get("image_url") else ""
        item["source_url"] = (
            f"https://epaperst.lakehouse.lk/home/ArticleView?eid="
            f"{PUBLICATIONS[publication_key].edition_id}"
            f"&edate={date.fromisoformat(edition_date):%d/%m/%Y}&pgid={item.get('page_id')}"
        )
    return manifest


def _js_item(item: dict) -> dict:
    keys = (
        "uid", "kind", "page_number", "title", "categories", "primary_category", "institutions",
        "dates_mentioned", "review_required", "match_reason", "source_url", "image_web_url",
        "borderline",
    )
    return {key: item.get(key) for key in keys}


@app.get("/monitor", response_class=HTMLResponse)
def monitor_home(
    request: Request,
    publication: str = DEFAULT_PUBLICATION.key,
    edition_date: str = "",
    requested_date: str = "",
    error: str = "",
):
    selected = PUBLICATIONS.get(publication, DEFAULT_PUBLICATION)
    manifest = None
    if edition_date:
        try:
            parsed = _parse_edition_date(edition_date)
        except HTTPException:
            edition_date = ""
        else:
            manifest = load_manifest(MONITOR_REPORT_ROOT, selected, parsed)
            if manifest:
                manifest = _web_manifest(selected.key, manifest)
    return templates.TemplateResponse(
        request,
        "monitor.html",
        {
            "publications": PUBLICATIONS.values(),
            "selected_publication": selected,
            "edition_date": edition_date,
            "selected_date": requested_date or edition_date,
            "today": colombo_today().isoformat(),
            "max_date": (colombo_today() + timedelta(days=1)).isoformat(),
            "manifest": manifest,
            "articles": (manifest or {}).get("articles", []),
            "js_items": [_js_item(item) for item in (manifest or {}).get("articles", [])],
            "categories": CATEGORIES.values(),
            "categories_by_key": CATEGORIES,
            "hidden_categories": DEFAULT_HIDDEN_CATEGORIES,
            "error": error,
            "is_vercel": IS_VERCEL,
            "ocr_available": ad_ocr.ocr_available(),
            "ai_configured": settings.ai_configured,
        },
    )


@app.post("/monitor/check")
def monitor_check(publication: str = Form(...), edition_date: str = Form("")):
    """Form fallback for browsers without JavaScript: checks synchronously."""
    selected = _publication(publication)
    issue_date = None
    if edition_date:
        parsed_date = _parse_edition_date(edition_date)
        issue_date = datetime.combine(parsed_date, datetime.min.time(), tzinfo=COLOMBO)
    try:
        if issue_date is None:
            _, result_date = generate_latest_report(MONITOR_REPORT_ROOT, selected)
        else:
            _, result_date = generate_report_for_date(MONITOR_REPORT_ROOT, selected, issue_date)
    except Exception as exc:
        LOGGER.exception("Manual e-paper check failed for %s", selected.name)
        requested = f"&requested_date={edition_date}" if edition_date else ""
        return RedirectResponse(
            f"/monitor?publication={selected.key}{requested}&error={quote('Check incomplete; human review required. ' + str(exc))}",
            status_code=303,
        )
    return RedirectResponse(
        f"/monitor?publication={selected.key}&edition_date={result_date:%Y-%m-%d}",
        status_code=303,
    )


@app.post("/api/check")
def api_check(publication: str = Form(...), edition_date: str = Form(""), force: str = Form("")):
    selected = _publication(publication)
    parsed = _parse_edition_date(edition_date) if edition_date else None
    refresh = force in {"1", "true", "on"}

    def runner(progress) -> str:
        if parsed is None:
            _, result = generate_latest_report(MONITOR_REPORT_ROOT, selected, progress=progress)
        else:
            _, result = generate_report_for_date(
                MONITOR_REPORT_ROOT,
                selected,
                datetime.combine(parsed, datetime.min.time(), tzinfo=COLOMBO),
                progress=progress,
                force=refresh,
            )
        return result.strftime("%Y-%m-%d")

    job = jobs.submit(selected.key, edition_date or "latest", runner)
    return JSONResponse(job.to_dict(), status_code=202)


@app.get("/api/jobs/{job_id}")
def api_job(job_id: str):
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(404, "Check not found; it may have finished before a restart.")
    data = job.to_dict()
    if job.status == "done":
        data["result_url"] = f"/monitor?publication={job.publication}&edition_date={job.result_date}"
    return data


def _publisher_failure(exc: Exception) -> str:
    """Short, non-sensitive reason a publisher request failed, for the screen."""
    if isinstance(exc, requests.HTTPError) and exc.response is not None:
        return f"publisher answered HTTP {exc.response.status_code}"
    if isinstance(exc, requests.Timeout):
        return "publisher did not answer in time"
    if isinstance(exc, requests.ConnectionError):
        return "connection to the publisher was refused or dropped"
    return type(exc).__name__


@app.get("/api/availability")
def api_availability(publication: str, month: str):
    selected = _publication(publication)
    try:
        year, month_number = (int(part) for part in month.split("-", 1))
        first = date(year, month_number, 1)
    except ValueError as exc:
        raise HTTPException(400, "Month must look like 2026-10.") from exc
    if first < date(2015, 1, 1) or first > colombo_today() + timedelta(days=32):
        raise HTTPException(400, "Month is outside the supported range.")
    try:
        dates = availability.month(selected, year, month_number)
    except (requests.RequestException, RuntimeError, ValueError) as exc:
        LOGGER.warning("Availability lookup failed: %s", exc)
        raise HTTPException(
            502,
            f"The publisher's edition calendar could not be reached ({_publisher_failure(exc)}).",
        ) from exc
    checked = {}
    for value in dates:
        manifest = load_manifest(MONITOR_REPORT_ROOT, selected, value)
        if manifest:
            checked[value.isoformat()] = {
                "complete": bool(manifest.get("complete")),
                "count": manifest.get("article_count", 0),
                "tenders": manifest.get("category_counts", {}).get("medical_tender", 0),
            }
    return {
        "publication": selected.key,
        "month": f"{year:04d}-{month_number:02d}",
        "dates": [value.isoformat() for value in dates],
        "checked": checked,
        "today": colombo_today().isoformat(),
    }


@app.get("/api/editions/{publication}/{edition_date}")
def api_edition(publication: str, edition_date: str):
    """Machine-readable results (for spreadsheets, scripts or an MCP wrapper)."""
    selected = _publication(publication)
    manifest = load_manifest(MONITOR_REPORT_ROOT, selected, _parse_edition_date(edition_date))
    if manifest is None:
        raise HTTPException(404, "This edition has not been checked yet.")
    return _web_manifest(selected.key, manifest)


@app.get("/admin", response_class=HTMLResponse)
def admin_home(request: Request):
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "issues": database.list_issues(),
            "today": datetime.now(COLOMBO).date().isoformat(),
            "max_upload_mb": settings.max_upload_mb,
            "auth_warning": not bool(settings.admin_password),
            "ai_provider": settings.ai_provider,
            "ai_model": settings.active_model,
            "ai_configured": settings.ai_configured,
        },
    )


async def _save_upload(upload: UploadFile, destination: Path, max_bytes: int) -> str:
    hasher = hashlib.sha256()
    total = 0
    first = True
    try:
        with destination.open("xb") as output:
            while chunk := await upload.read(1024 * 1024):
                if first:
                    first = False
                    if not chunk.startswith(b"%PDF-"):
                        raise HTTPException(400, "The uploaded file is not a PDF.")
                total += len(chunk)
                if total > max_bytes:
                    raise HTTPException(413, f"PDF exceeds the {settings.max_upload_mb} MB limit.")
                hasher.update(chunk)
                output.write(chunk)
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    finally:
        await upload.close()
    if total == 0:
        destination.unlink(missing_ok=True)
        raise HTTPException(400, "The uploaded file is empty.")
    return hasher.hexdigest()


@app.post("/admin/issues")
async def upload_issue(
    background_tasks: BackgroundTasks,
    publication: str = Form(...),
    edition_date: str = Form(...),
    source_url: str = Form(""),
    pdf: UploadFile = None,
):
    publication = publication.strip()[:200]
    source_url = source_url.strip()[:1000]
    if not publication:
        raise HTTPException(400, "Publication name is required.")
    if source_url:
        parsed_url = urlsplit(source_url)
        if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
            raise HTTPException(400, "Official source URL must use http or https.")
    try:
        date.fromisoformat(edition_date)
    except ValueError as exc:
        raise HTTPException(400, "Edition date must be a valid ISO date.") from exc
    if pdf is None or not pdf.filename:
        raise HTTPException(400, "A PDF file is required.")

    temp_name = f"{secrets.token_hex(16)}.pdf"
    destination = settings.upload_dir / temp_name
    file_hash = await _save_upload(pdf, destination, settings.max_upload_mb * 1024 * 1024)
    try:
        issue_id = database.create_issue(
            publication, edition_date, source_url, str(destination), file_hash
        )
    except Exception as exc:
        if "UNIQUE constraint failed: issues.file_hash" in str(exc):
            existing = database.get_issue_by_hash(file_hash)
            if existing and existing["status"] == "failed":
                database.prepare_failed_issue_for_retry(existing["id"], str(destination))
                background_tasks.add_task(agent.process_issue, existing["id"], destination)
                return RedirectResponse(f"/admin/issues/{existing['id']}", status_code=303)
            destination.unlink(missing_ok=True)
            raise HTTPException(409, "This exact PDF has already been uploaded.") from exc
        destination.unlink(missing_ok=True)
        raise

    background_tasks.add_task(agent.process_issue, issue_id, destination)
    return RedirectResponse(f"/admin/issues/{issue_id}", status_code=303)


@app.get("/admin/issues/{issue_id}", response_class=HTMLResponse)
def issue_detail(request: Request, issue_id: int, message: str = ""):
    issue = database.get_issue(issue_id)
    if not issue:
        raise HTTPException(404, "Issue not found.")
    return templates.TemplateResponse(
        request,
        "issue.html",
        {
            "issue": issue,
            "stories": database.get_stories(issue_id),
            "categories": [item.value for item in Category],
            "message": message,
            "source_exists": Path(issue["file_path"]).is_file(),
        },
    )


@app.post("/admin/issues/{issue_id}/overview")
def update_overview(issue_id: int, overview: str = Form(...)):
    overview = " ".join(overview.split())[:3000]
    if not overview:
        raise HTTPException(400, "Edition overview is required.")
    if not database.update_issue_overview(issue_id, overview):
        raise HTTPException(404, "Issue not found.")
    return RedirectResponse(
        f"/admin/issues/{issue_id}?message={quote('Overview saved.')}", status_code=303
    )


@app.post("/admin/issues/{issue_id}/retry")
def retry_issue(background_tasks: BackgroundTasks, issue_id: int):
    issue = database.get_issue(issue_id)
    if not issue:
        raise HTTPException(404, "Issue not found.")
    source_path = Path(issue["file_path"])
    if issue["status"] != "failed" or not source_path.is_file():
        raise HTTPException(409, "This failed issue has no retained source PDF. Upload it again.")
    database.set_issue_status(issue_id, "queued")
    background_tasks.add_task(agent.process_issue, issue_id, source_path)
    return RedirectResponse(f"/admin/issues/{issue_id}", status_code=303)


@app.post("/admin/issues/{issue_id}/stories/{story_id}")
def update_story(
    issue_id: int,
    story_id: int,
    title: str = Form(...),
    summary: str = Form(...),
    category: str = Form(...),
    approved: str | None = Form(None),
):
    if category not in {item.value for item in Category}:
        raise HTTPException(400, "Invalid story category.")
    title = " ".join(title.split())[:300]
    summary = " ".join(summary.split())[:2000]
    if not title or not summary:
        raise HTTPException(400, "Title and summary are required.")
    if not database.update_story(issue_id, story_id, title, summary, category, approved == "on"):
        raise HTTPException(404, "Story not found.")
    return RedirectResponse(
        f"/admin/issues/{issue_id}?message={quote('Story saved.')}", status_code=303
    )


@app.post("/admin/issues/{issue_id}/approve-all")
def approve_all(issue_id: int):
    if not database.get_issue(issue_id):
        raise HTTPException(404, "Issue not found.")
    database.approve_all(issue_id)
    return RedirectResponse(
        f"/admin/issues/{issue_id}?message={quote('All stories approved.')}", status_code=303
    )


@app.post("/admin/issues/{issue_id}/publish")
def publish_issue(issue_id: int):
    _, message = database.publish(issue_id)
    return RedirectResponse(f"/admin/issues/{issue_id}?message={quote(message)}", status_code=303)


@app.get("/brief/{issue_id}", response_class=HTMLResponse)
def public_brief(request: Request, issue_id: int):
    issue = database.get_issue(issue_id, published_only=True)
    if not issue:
        raise HTTPException(404, "Published brief not found.")
    return templates.TemplateResponse(
        request,
        "brief.html",
        {"issue": issue, "stories": database.get_stories(issue_id, approved_only=True)},
    )
