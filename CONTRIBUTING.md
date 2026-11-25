# Contributing

News Hunter handles medical-news detection and editorial review. Changes should
be small, traceable, and supported by source-grounded behavior.

## Development setup

Requirements: Python 3.11 or 3.12 and network access for dependency installation.

On Windows:

```powershell
.\setup.cmd -Dev
.\.venv\Scripts\Activate.ps1
```

On other platforms:

```sh
python -m venv .venv
. .venv/bin/activate
python -m pip install -e ".[dev]"
```

Run the static checks and test suite before submitting a change:

```sh
python -m ruff check .
python -m pytest -q
```

The tests must use fake AI engines and storage backends. They must not call a
live model provider, newspaper publisher, or production storage account.

## Engineering boundaries

- Preserve the medical recall requirements in [AGENTS.md](AGENTS.md) and
  [the medical rules](docs/MEDICAL_RULES.md). Borderline candidates must remain
  visible for human review.
- Keep source evidence tied to its cited page. Do not invent or normalize
  medicine names, amounts, dates, or attribution.
- Add a database migration when changing persisted fields. Migrations must keep
  existing editions and must be safe to run more than once.
- Keep provider credentials and source PDFs out of logs, tests, fixtures, and
  commits.
- Keep HTTP routes thin. Put reusable behavior in the corresponding `core`,
  `medical`, `monitor`, `extraction`, or `ai` module.
- Update the relevant guides when behavior, configuration, or deployment
  requirements change.

## Pull requests

Include the problem, the behavior changed, and the checks performed. Review the
staged file list before committing; do not include `.env`, local databases,
reports, PDFs, or downloaded images. For medical detection or reporting changes,
include regression cases for inclusion, ambiguity, evidence validation, and
false-negative risk.
