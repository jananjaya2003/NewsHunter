# GitHub and Vercel

## Layout

- `app/`: FastAPI source, templates and static assets.
- `tests/`: regression tests including medical completeness checks.
- `docs/`: operations, safety and deployment documentation.
- `.github/workflows/ci.yml`: lint and tests on pushes and pull requests.
- `.github/dependabot.yml`: weekly dependency and action update PRs.
- `.env.example`: every supported setting; copy to `.env` locally.
- `.vercelignore`: keeps local data, tests and scripts out of CLI uploads.
- `pyproject.toml`: dependencies and Vercel entrypoint.
- `vercel.json`: FastAPI framework selection.
- `scripts/windows/`: optional local Windows runners and scheduled-task installers.

Local secrets, browser profiles, databases, uploads, screenshots and reports
are excluded from Git and Vercel. Keep these on your machine. Do not upload
this entire workspace through GitHub's web file uploader.

## GitHub

Create an empty private repository, then run from the project root:

```sh
git init -b main
git add .
git status --short
git commit -m "Prepare News Hunter for GitHub and Vercel"
git remote add origin https://github.com/YOUR_ACCOUNT/YOUR_REPOSITORY.git
git push -u origin main
```

Review staged files before committing. This cleanup does not create a remote
repository or deploy the app.

## Vercel preview

1. Import the GitHub repository. Select its root and the FastAPI framework.
   Leave build and install command overrides empty.
2. Optional: set `ADMIN_PASSWORD` to require the `admin` login; without it the
   site is open to anyone with the URL. Configure `AI_PROVIDER`
   and its API key and model settings using `.env.example` as a reference.
3. Omit `DATABASE_PATH`, `UPLOAD_DIR` and `REPORT_DIR`: their Vercel defaults
   use writable temporary storage. Do not copy the local paths.
4. Deploy and verify `/health`, `/monitor`, `/static/styles.css` and `/admin`.
   The editor/checker username is `admin`.

## Check now on the online site

The publisher's website refuses Vercel's servers (HTTP 403), so the online
site works from results stored in Vercel Blob. Every date from the last 45
days can be selected. **Check now** first tries the check on Vercel. That
works once the publisher allows this server. If it is refused, or the check
cannot finish within the function time limit, the date is saved as a request
and marked "Check requested" in the calendar.

The office PC runs those requests and uploads the results:

```powershell
.\.venv\Scripts\python.exe -m app.monitor.request_worker            # once
.\.venv\Scripts\python.exe -m app.monitor.request_worker --watch 5  # every 5 minutes
```

Nothing runs automatically until you start or schedule the worker. A request
that fails stays on the calendar with its reason (red), and **Check now**
queues it again. Do not try to get around the publisher's block. Ask the
publisher to allow the site instead.

## Production limitations

This supports temporary previews. SQLite, uploads and reports can disappear
or differ between function instances. Do not rely on the preview to retain
medical candidates, review decisions or published briefs. Continue real
editorial work locally until durable storage is implemented.

Production requires a managed database, private durable object storage and a
worker/queue for long AI/PDF and newspaper processing. Preserve evidence,
review warnings and approval gates during migration. Windows scheduled tasks
run locally; Vercel does not execute those scripts. Function duration and
request-size limits apply: the local 80 MB upload setting does not guarantee
an 80 MB cloud upload.

References: [FastAPI deployment](https://vercel.com/docs/frameworks/backend/fastapi),
[runtime filesystem](https://vercel.com/docs/functions/runtimes),
[function limits](https://vercel.com/docs/functions/limitations).
