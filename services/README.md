# services/

Runnable units. Each composes `core/` (logic) with `adapters/` (I/O) and
owns no rules of its own.

| Service | What it does | How it runs |
|---|---|---|
| `ingestion/` | Synthetic data generation, call-sheet PDF parsing, uploaded-agreement extraction | On demand — CLI now, Cloud Run Job later |
| `monitor/` | The loop: read state → evaluate rules → write violations + push metrics | Continuous (~15s tick) |
| `agent/` | ADK agent — tools, prompts, Grafana MCP wiring | With the API, or on Agent Engine |
| `api/` | FastAPI endpoints backing the Ops and Executive views | Cloud Run |

## Deployables

Three, not six:

```
web      →  static hosting
api      →  Cloud Run   (agent embedded, unless moved to Agent Engine)
monitor  →  Cloud Run   (needs a process that stays alive)
```

`ingestion/` is a job, not a service — contract and call-sheet processing is
on demand and slow (PDF + Gemini), not continuously running. Keeping it as
its own module means it can start as a CLI and become a triggered job later
without restructuring.

## Open decisions

Both are Ring 3–4 questions, deliberately deferred until there is working
code to reason about:

- **Where the agent runs** — embedded in `api/` (simple, one deploy, one log
  stream) vs. deployed to Gemini Enterprise Agent Platform's Agent Engine
  (more idiomatic Google Cloud, more setup). Keep `agent/` free of FastAPI
  imports either way so the choice stays open.
- **How `monitor/` stays alive** — Cloud Run with `min-instances=1` and an
  internal loop (fresh countdowns, small cost) vs. a Cloud Run Job on
  Cloud Scheduler (scales to zero, but 60s minimum granularity makes the
  demo countdown chunky).
