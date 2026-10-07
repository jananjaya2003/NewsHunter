# News Hunter

A private-first MVP that extracts only medical and healthcare-service reporting
from a lawfully obtained newspaper PDF and creates a concise, page-cited brief.
The agent uses Gemini as its default AI brain with schema-constrained output,
saves draft stories in SQLite, and requires human approval before a brief can
be published. An OpenAI engine remains available as an optional fallback.

This project does not share subscription credentials or republish a source PDF.
It also contains an optional, personal-use Daily News monitor that creates a
local screenshot-only report from the publisher's article viewer. It does not
automate a login or store a newspaper password.

## What the MVP does

1. An editor manually downloads a newspaper using their valid subscription.
2. The editor uploads that PDF to the local admin interface.
3. The app extracts text page by page without sending the original PDF onward.
4. The AI applies high-recall medical-scope rules, retains uncertain medical
   candidates for human review, and ignores demonstrably unrelated news.
5. Duplicate stories are merged and every summary keeps its source page number.
6. An editor checks and approves every story.
7. Only an approved issue can be published at a read-only public URL.
8. By default, the uploaded PDF is deleted when processing finishes.

## Quick start on Windows PowerShell

```powershell
cd D:\Automation
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
Copy-Item .env.example .env
```

Edit `.env` and add your `GEMINI_API_KEY`. Keep `AI_PROVIDER=gemini`. Also replace
`ADMIN_PASSWORD` before making the app reachable by anyone else.

Start the application:

```powershell
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Open <http://127.0.0.1:8000/admin>. If authentication is enabled, use username
`admin` and the password from `.env`.

## Daily operation

1. Download the edition manually from the publisher.
2. Open `/admin` and enter the publication name, edition date, and optional
   official source URL.
3. Upload the PDF. The page will show `Processing`; refresh it periodically.
4. Review every generated headline and summary against the cited PDF page.
5. Edit inaccurate wording and approve the story.
6. Use **Approve all visible stories** only after reviewing them.
7. Click **Publish brief**. The public URL appears on the issue page.

The public page contains summaries and attribution only. It never serves the
source PDF or extracted article text.

## Important limitations

- Text extraction works best with text-based PDFs. Image-only scans require an
  OCR stage, which is intentionally not hidden inside this MVP.
- Newspaper columns can be extracted in an imperfect reading order. This is why
  the approval gate is mandatory.
- An AI summary can still be factually wrong even when it follows the JSON
  schema. Always compare sensitive stories with the source.
- A personal newspaper subscription does not itself establish republication
  rights. Obtain written permission/licensing before operating a public or
  commercial service.

## Documentation

- [Architecture and data flow](docs/ARCHITECTURE.md)
- [Setup and operations](docs/OPERATIONS.md)
- [Publishing and safety checklist](docs/PUBLISHING_CHECKLIST.md)
- [Medical-only selection rules](docs/MEDICAL_RULES.md)
- [Gemini and AI-engine configuration](docs/AI_ENGINE.md)

## Tests

```powershell
pytest
ruff check .
```

The tests mock provider calls and do not contact Gemini or OpenAI.

## Daily News screenshot report

The local monitor checks the English *Daily News* edition, selects articles
with Gemini AI plus local high-recall medical safety rules, and saves the
publisher's original cropped article images. Local rules retain possible items
that Gemini rejects, but clearly mark them for human review. If AI matching is
incomplete, generation fails closed and preserves the previous complete report
instead of silently publishing a partial local-only result. It does not
summarize articles.

Run it manually:

```powershell
.\run_epaper_monitor.ps1 -OpenReport
```

Install or refresh the Windows schedule (daily at 8:00 PM local time):

```powershell
.\install_daily_schedule.ps1
```

Reports are written to `reports\YYYY-MM-DD\index.html`; `reports\latest.html`
always contains the latest report. Logs are written to
`data\epaper-monitor.log`. The scheduled task runs only while the Windows user
is logged on and opens the finished report in the default browser.

The report is for the subscriber's private use. Do not redistribute the saved
article images without the publisher's permission.

## Manual web checker

Start the local web application and open <http://127.0.0.1:8000/>. The home
page lets you select **Daily News** or **Sunday Observer** and click
**Check now**. It searches backward for that publication's latest available
edition and displays the matching original article images directly in the web
page. This manual option does not replace the 8:00 PM Daily News schedule.

Install the local site to start automatically after Windows sign-in:

```powershell
.\install_web_app_startup.ps1
```

Use `Open News Hunter.url` in this folder to open the checker.
