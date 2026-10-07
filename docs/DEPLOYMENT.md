# GitHub and Vercel

## Layout

- `app/`: FastAPI source, templates and static assets.
- `tests/`: regression tests including medical completeness checks.
- `docs/`: operations, safety and deployment documentation.
- `.github/workflows/ci.yml`: lint and tests on pushes and pull requests.
- `pyproject.toml`: dependencies and Vercel entrypoint.
- `vercel.json`: FastAPI framework selection.
- Root PowerShell scripts: optional local Windows runners and schedules.

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
2. Set `ADMIN_PASSWORD` to a unique strong password. Configure `AI_PROVIDER`
   and its API key and model settings using `.env.example` as a reference.
3. Omit `DATABASE_PATH`, `UPLOAD_DIR` and `REPORT_DIR`: their Vercel defaults
   use writable temporary storage. Do not copy the local paths.
4. Deploy and verify `/health`, `/monitor`, `/static/styles.css` and `/admin`.
   The editor/checker username is `admin`.

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
