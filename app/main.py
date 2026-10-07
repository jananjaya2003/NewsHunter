from __future__ import annotations

import hashlib
import json
import logging
import secrets
from base64 import b64decode
from datetime import date, datetime
from pathlib import Path
from urllib.parse import quote, urlsplit

from fastapi import BackgroundTasks, FastAPI, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.agent import NewspaperAgent
from app.config import settings
from app.database import Database
from app.epaper_monitor import COLOMBO, DEFAULT_PUBLICATION, PUBLICATIONS, generate_latest_report
from app.schemas import Category

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
LOGGER = logging.getLogger(__name__)

settings.prepare_directories()
MONITOR_REPORT_ROOT = Path("reports/manual")
MONITOR_REPORT_ROOT.mkdir(parents=True, exist_ok=True)
database = Database(settings.database_path)
database.initialize()
agent = NewspaperAgent(settings, database)

app = FastAPI(title="News Hunter", version="0.2.0")
app.mount("/static", StaticFiles(directory="app/static"), name="static")
app.mount("/monitor-files", StaticFiles(directory=MONITOR_REPORT_ROOT), name="monitor-files")
templates = Jinja2Templates(directory="app/templates")


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
        "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
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


@app.get("/monitor", response_class=HTMLResponse)
def monitor_home(
    request: Request,
    publication: str = DEFAULT_PUBLICATION.key,
    edition_date: str = "",
    error: str = "",
):
    selected = PUBLICATIONS.get(publication, DEFAULT_PUBLICATION)
    articles: list[dict] = []
    generated_at = ""
    matching_method = ""
    matching_model = ""
    matching_warning = ""
    if edition_date:
        try:
            date.fromisoformat(edition_date)
        except ValueError:
            edition_date = ""
        if edition_date:
            manifest_path = MONITOR_REPORT_ROOT / selected.key / edition_date / "manifest.json"
            if manifest_path.is_file():
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                generated_at = str(manifest.get("generated_at") or "")
                matching_method = str(manifest.get("matching_method") or "")
                matching_model = str(manifest.get("matching_model") or "")
                matching_warning = str(manifest.get("matching_warning") or "")
                for item in manifest.get("articles", []):
                    article = dict(item)
                    article["image_web_url"] = (
                        f"/monitor-files/{selected.key}/{edition_date}/{item['image_file']}"
                    )
                    articles.append(article)
    return templates.TemplateResponse(
        request,
        "monitor.html",
        {
            "publications": PUBLICATIONS.values(),
            "selected_publication": selected,
            "edition_date": edition_date,
            "articles": articles,
            "generated_at": generated_at,
            "matching_method": matching_method,
            "matching_model": matching_model,
            "matching_warning": matching_warning,
            "error": error,
        },
    )


@app.post("/monitor/check")
def monitor_check(publication: str = Form(...)):
    selected = PUBLICATIONS.get(publication)
    if selected is None:
        raise HTTPException(400, "Unknown newspaper selection.")
    try:
        _, edition_date = generate_latest_report(
            MONITOR_REPORT_ROOT / selected.key,
            selected,
        )
    except Exception as exc:
        LOGGER.exception("Manual e-paper check failed for %s", selected.name)
        return RedirectResponse(
            f"/monitor?publication={selected.key}&error={quote(str(exc))}",
            status_code=303,
        )
    return RedirectResponse(
        f"/monitor?publication={selected.key}&edition_date={edition_date:%Y-%m-%d}",
        status_code=303,
    )


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
