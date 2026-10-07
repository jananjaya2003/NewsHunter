# Setup and operations

## Prerequisites

- Python 3.11 or newer
- A Gemini API key with access to the configured model
- A text-based PDF obtained through a lawful workflow
- Written publisher permission before public/commercial operation

## Configuration

Copy `.env.example` to `.env` and configure:

| Setting | Purpose |
|---|---|
| `AI_PROVIDER` | Active engine: `gemini` or `openai` |
| `GEMINI_API_KEY` | Gemini credential; never place it in source control |
| `GEMINI_MODEL` | Gemini structured-output capable model |
| `GEMINI_FALLBACK_MODELS` | Comma-separated transient-failure/model fallback order |
| `OPENAI_API_KEY` | Optional fallback credential |
| `OPENAI_MODEL` | Optional fallback model |
| `ADMIN_PASSWORD` | HTTP Basic password for editor routes |
| `KEEP_SOURCE_PDF` | Retain or delete source files after processing |
| `MAX_UPLOAD_MB` | Hard upload size limit |
| `MAX_CHUNK_CHARS` | Approximate maximum text sent per extraction call |
| `DATABASE_PATH` | SQLite file location |
| `UPLOAD_DIR` | Private temporary PDF directory |

## Run locally

```powershell
.\.venv\Scripts\Activate.ps1
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

The default host is deliberately local-only. Do not change it to `0.0.0.0`
without authentication, TLS, a firewall, and an explicit deployment review.
Set a strong, unique `ADMIN_PASSWORD` even for local use. Unsafe authenticated
requests are rejected unless their browser Origin or Referer matches the app.

## Processing behavior

Uploads are queued as a FastAPI background task. This is suitable for one
editor and a single process. Do not run multiple Uvicorn workers with this
in-process queue. A server restart can interrupt a processing issue; use the
**Retry processing** button after confirming the source file still exists.

If `KEEP_SOURCE_PDF=false`, a failed issue cannot be retried because the source
is deleted. Upload it again after correcting the underlying problem.

The e-paper monitor is fail-closed: every candidate must receive an AI decision.
If Gemini and its configured fallback models cannot complete all batches, the
run exits with an error and keeps the prior report and images unchanged. Local
rules are a completeness safety net, not an API-outage replacement; local-only
matches are visibly marked for human review.

## Common problems

### “GEMINI_API_KEY is not configured”

Add the key to `.env` and restart Uvicorn. Do not paste a key into the UI.
If a key is ever exposed in chat, logs, screenshots, or source control, revoke
it in Google AI Studio and create a replacement before restarting the service.

### “No readable text was found”

The PDF is likely an image scan. Run an approved OCR process first and upload
the OCR-enhanced PDF. Verify OCR quality, especially names and numbers.

### Stories have mixed columns

Multi-column newspaper extraction is imperfect. Reduce `MAX_CHUNK_CHARS`, edit
the resulting draft, or integrate a layout-aware document parser.

### An issue remains on Processing

Check the terminal log. With this MVP, restarting the process does not resume a
background task automatically. Re-upload the edition if the temporary file was
deleted.

## Backup

Stop the application and copy `data/news_agent.db` to protected storage. Source
PDFs are not part of the default backup because they are deleted after use.

## Production checklist

Before public deployment, add:

- Publisher content licence and documented retention rules
- Reverse proxy with HTTPS
- Individual editor accounts, roles, token-based CSRF protection, and audit logs
- Durable job queue and retry/dead-letter behavior
- Encrypted database and private storage backups
- Monitoring for failed or unusually expensive model requests
- Privacy notice, corrections policy, terms, and publisher attribution rules
