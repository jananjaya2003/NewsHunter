# News Hunter

News Hunter finds **medical tenders and healthcare news** in the Sri Lankan
*Daily News* and *Sunday Observer* e-papers for a medical-supplies sales team.
It reads every page of the main paper **and every section published that day**
(ADZ, Advertisements, Observer Jobs, Business Observer, supplements). It reads
advert images with OCR, sorts each item into categories with Gemini AI plus
local safety rules, and shows the original clippings for human review.

Medical completeness comes first. A possibly relevant item is kept and marked
for review rather than dropped, and any part of the paper that could not be
read is listed on the result (see [AGENTS.md](AGENTS.md)). Results are
research leads, not medical advice.

## Quick start on any Windows PC

Needs Windows 10/11, internet access and Python 3.11 or newer
([python.org](https://www.python.org/downloads/), tick *Add python.exe to
PATH*, or `winget install Python.Python.3.12`).

Open **Command Prompt** or **PowerShell** in the project folder and run:

```bat
.\setup.cmd
.\start.cmd
```

(Type the `.\` too: some company PCs block running files from the current
folder without it.)

- `setup.cmd` checks Python, creates `.venv`, installs everything, creates
  `.env` and checks that the app loads. It is safe to run again; use
  `.\setup.cmd -Test` to also run the test suite.
- `start.cmd` starts the app and opens <http://127.0.0.1:8000/monitor>.
  Close the window to stop it.
- Put your **Gemini API key** in `.env` (`GEMINI_API_KEY=`). Without it, items
  are sorted by keyword rules only and every one is marked *Needs review*.

From a fresh copy on GitHub:

```bat
git clone https://github.com/jananjaya2003/NewsHunter.git
cd NewsHunter
.\setup.cmd
.\start.cmd
```

## Daily use

1. Open the **Newspaper check** page, choose the paper and a date in the
   calendar, then press **Check now** (tick *Re-check* to refresh a saved result).
2. Use the category tiles (ticked = shown), *Needs review only* and *Not viewed
   yet* to narrow the list.
3. **View** opens an article with a short AI summary. Viewed articles get a
   ✓ Viewed mark in this browser.
4. Tick articles and press **Download N selected**, or **Download all shown
   (ZIP)**. Every image carries the paper, date and page, for example
   *Daily News · Monday, 26 October 2026 · Page 17*.

Command-line checks (also used by the scheduled task):

```bat
.venv\Scripts\python.exe -m app.monitor.epaper_monitor --publication daily-news
.venv\Scripts\python.exe -m app.monitor.epaper_monitor --publication sunday-observer --date 2026-10-25 --force
```

## How a check works

```text
publisher e-paper ──► main paper + every section published that day
                      (Matrimonial classifieds listed as not screened)
        │
        ├─ news articles (text) ─────────────┐
        └─ advert images ─► OCR (Windows OCR,├─► keyword safety rules + Gemini AI
                            or Gemini)       │   (uncertain items kept, marked for review)
                                             ▼
                     result saved in reports\manual\<paper>\<date>\manifest.json
                     ▲ shown on the web page, uploaded to the online site
```

A result counts as **complete** only when every section was read, every advert
was screened and the AI classified every item. Otherwise the page shows why
and suggests a re-check.

## The online site

The publisher's website refuses cloud servers (HTTP 403), so the online site
(<https://newshunters.vercel.app>) shows results that the office PC checks
and uploads to Vercel Blob. Its **Check now** leaves a request that the office
PC runs with `python -m app.monitor.request_worker`. See
[docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).

## Project structure

```text
NewsHunter/
├── app/
│   ├── main.py               Web app: pages, API, image labels, online-check flow
│   ├── monitor/              E-paper checker, sections, result upload, request worker, summaries
│   ├── medical/              Medical scope rules and tender categories
│   ├── extraction/           Advert OCR (Windows OCR or Gemini) and PDF text extraction
│   ├── ai/                   Gemini/OpenAI clients and the PDF brief agent
│   ├── core/                 Settings, data models, SQLite, background jobs
│   ├── templates/, static/   HTML, CSS and JavaScript
├── tests/                    pytest suite, including medical false-negative tests
├── docs/                     Architecture, operations, deployment, medical and AI rules
├── scripts/windows/          setup, runners and scheduled-task installers
├── setup.cmd, start.cmd      One-step setup and start on Windows
├── .env.example              Every setting, with defaults (copy to .env)
└── pyproject.toml            Dependencies and tool settings
```

`.venv\`, `data\`, `reports\` and `.env` stay on the PC and are never
committed.

## Development

```bat
.\setup.cmd -Dev
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\python.exe -m ruff check .
```

GitHub Actions runs the same lint and tests on every push. The tests never
call Gemini, OpenAI or the publisher. VS Code settings, debug configurations
and tasks are in `.vscode/`.

## Limits

- **Gemini free tier:** 5 requests a minute and 500 a day per model. A month
  of checks in one day uses it up; affected results are marked incomplete and
  should be re-checked the next day, or use a paid plan.
- **Vercel Blob free tier:** limited monthly uploads. Going over suspends the
  store, and the online site cannot load results until the monthly reset.
- OCR and AI can misread names, dates and amounts. Always check against the
  image or the e-paper before acting.
- A newspaper subscription does not grant redistribution rights. Keep
  clippings for internal use.

## Documentation

- [Architecture](docs/ARCHITECTURE.md) · [Operations](docs/OPERATIONS.md) ·
  [Deployment](docs/DEPLOYMENT.md)
- [Medical selection rules](docs/MEDICAL_RULES.md) · [AI engine](docs/AI_ENGINE.md) ·
  [Publishing checklist](docs/PUBLISHING_CHECKLIST.md)
- The older PDF brief workflow (upload a PDF at `/admin`) is described in
  [docs/OPERATIONS.md](docs/OPERATIONS.md).
