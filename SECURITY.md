# Security

## Operating the application

- Keep local use bound to `127.0.0.1`. Do not expose the development server
  directly to a network.
- Configure a unique `ADMIN_PASSWORD` for deployments. Vercel editor and check
  actions remain disabled until the password is set.
- Keep `.env`, Vercel credentials, source PDFs, local databases, and generated
  reports out of Git. Use `.env.example` as a template, never as a place for real
  credentials.
- The publisher's text and OCR output are untrusted input. The model has no
  tools, and its output must pass application validation and human review.
- Source PDFs are deleted after processing by default. Enable
  `KEEP_SOURCE_PDF=true` only with an approved retention need and protected
  storage.

## Reporting a security concern

Do not include API keys, passwords, private PDFs, or production data in a public
issue. If GitHub private vulnerability reporting is enabled for this repository,
use it. Otherwise contact the repository maintainer privately with a concise
description and reproduction steps that do not expose credentials or personal
data.

## Scope and limitations

News Hunter is a single-editor application with HTTP Basic authentication and
process-local background jobs. It is not designed for multi-tenant access,
public user registration, or unsupervised medical publication. Review
`docs/DEPLOYMENT.md` before operating it outside a local workstation.
