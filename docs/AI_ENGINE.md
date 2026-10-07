# AI engine configuration

The medical-news pipeline is independent of the model provider. `app/agent.py`
asks an `AIEngine` for validated Pydantic results; `app/ai_engines.py` translates
that request into the selected provider's API.

## Gemini setup

1. Create a Gemini API key in Google AI Studio.
2. Copy `.env.example` to `.env` if `.env` does not exist.
3. Configure:

```env
AI_PROVIDER=gemini
GEMINI_API_KEY=your-real-key
GEMINI_MODEL=gemini-3.8-flash
GEMINI_FALLBACK_MODELS=gemini-3.5-flash,gemini-3.5-flash-lite
```

4. Restart Uvicorn. The dashboard should show `GEMINI · gemini-3.8-flash` and
   `API key configured`.

Never put the key in a browser form, PDF, prompt, source file, or Git commit.

## What Gemini does

Gemini receives extracted text chunks, not subscriber credentials. It performs
two schema-constrained tasks:

1. Select medical/healthcare articles and produce page-cited story records.
2. Produce an edition-level medical overview from those draft records.

Application code still validates the returned medical-evidence phrase, page
numbers, titles, and approval state. Gemini cannot upload files, access the
e-paper account, or publish a brief by itself.

For the e-paper monitor, every extracted article must receive a Gemini
decision. Transient errors and unavailable primary models are retried with the
configured fallback model list. A local high-recall rule may retain an item
that Gemini rejected, but the report labels that item **Human review required**.
If any article has no AI decision, the run fails and preserves the last complete
report. The manifest records the model that completed matching.

## Switching provider

The OpenAI implementation remains available as a fallback:

```env
AI_PROVIDER=openai
OPENAI_API_KEY=your-real-key
OPENAI_MODEL=gpt-4o-mini
```

Restart the application after changing providers.

## Troubleshooting

- `GEMINI_API_KEY is not configured`: add the key to `.env` and restart.
- `Unsupported AI_PROVIDER`: use exactly `gemini` or `openai`.
- Model not found/access denied: choose a Gemini model available to your API
  project and update `GEMINI_MODEL` or `GEMINI_FALLBACK_MODELS`.
- Rate limit: wait for the provider window to reset or reduce PDF chunk volume.
- Invalid structured output: retry once; if repeated, inspect the logged error
  and use a stable model with structured-output support.
- Incomplete e-paper matching: no new report is published. Keep the existing
  report, check the provider status/model access, and retry the run.
