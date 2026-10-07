# Architecture and agent design

## Goal

Produce short, attributable summaries of medical and healthcare-service news
while excluding unrelated newspaper content and keeping publication decisions
under human control.

## Pipeline

```text
Editor upload
    |
    v
PDF validation -> SHA-256 duplicate check -> private temporary storage
    |
    v
Page-aware text extraction (PyMuPDF)
    |
    v
Bounded text chunks with explicit --- PAGE N --- markers
    |
    v
Selected AI engine (Gemini by default): medical candidates, evidence, and citations
    |
    v
Exact medical-evidence validation -> duplicate merging -> SQLite draft records
    |
    v
Selected AI engine: edition overview and top-story ordering
    |
    v
Editor review/edit/approval -> publish gate -> read-only public brief
```

## Why this is a controlled workflow, not an autonomous publisher

The model has one narrow permission: transform supplied text into a predefined
data structure. It cannot download editions, browse the web, publish a draft,
or alter the database through model-selected tools. Application code performs
each action and validates model output.

## Main modules

- `app/main.py`: HTTP interface, authentication, upload validation, approval,
  editing, and publishing routes.
- `app/pdf_extract.py`: PDF validation and page-aware text extraction/chunking.
- `app/agent.py`: prompts, structured schemas, model calls, citation checks,
  deduplication, and the final overview.
- `app/ai_engines.py`: provider boundary with Gemini and OpenAI implementations.
- `app/database.py`: small SQLite repository with parameterized queries.
- `app/config.py`: environment-based configuration.
- `app/epaper_monitor.py`: fail-closed Gemini matching, local safety review,
  atomic image/report replacement, and per-result provenance.

## Prompt-injection boundary

Newspaper text is untrusted data. The system prompt explicitly tells the model
that commands embedded in the paper are article content and must not override
the task. No model-call tools are enabled. Returned page numbers are intersected
with pages that actually exist in the input chunk.

## Data model

An `issue` stores metadata, processing state, an overview, editor notes, and a
cryptographic file hash. A `story` stores an original AI summary, category,
byline, source pages, confidence, sensitivity flag, and approval state.

States are:

```text
queued -> processing -> review -> published
                     \-> failed
```

Publishing is rejected unless the issue is in `review` and every story has been
approved by a human.

## Data minimization

The database does not keep full article text. By default, the original PDF is
deleted in a `finally` block after processing succeeds or fails. Set
`KEEP_SOURCE_PDF=true` only when you have a documented reason and appropriate
access controls.

## Scaling beyond the MVP

For a production deployment, move background work to a durable queue, store
encrypted source files in private object storage, add named editor accounts and
audit logs, replace origin checking with token-based CSRF protection, and place
the service behind TLS. Obtain a publisher-provided feed instead of automating
subscriber access.
