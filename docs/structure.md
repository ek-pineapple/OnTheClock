# Project Structure

```
OnTheClock/
├── README.md
├── LICENSE                       # MIT — must stay at root for GitHub detection
├── .gitignore
│
├── core/                         # pure logic — no I/O, no network, no cloud
│   ├── models.py                 #   Ring 0: the domain objects
│   ├── rulebook.py               #   Ring 0: the encoded SAG-AFTRA agreement
│   └── rules/                    #   Ring 1: rest.py, meals.py, classification.py
│
├── adapters/                     # every external dependency, isolated
│   ├── firestore.py
│   ├── grafana_push.py
│   ├── grafana_mcp.py
│   └── gemini.py
│
├── services/                     # runnable units — compose core + adapters
│   ├── ingestion/                #   synthetic data, call sheets, agreement upload
│   ├── monitor/                  #   the evaluate-and-push loop
│   ├── agent/                    #   ADK agent, tools, prompts
│   └── api/                      #   FastAPI, serves both views
│
├── web/                          # React + Vite — Ops view, Executive view
├── deploy/                       # Dockerfiles, Cloud Run config
├── tests/
└── docs/
    ├── plan.md
    ├── features.md
    ├── schema.md
    ├── structure.md
    └── journal.md
```

## Why it is arranged this way

The system has five external dependencies (Firestore, Grafana push, Grafana
MCP, Gemini, Cloud Run) and one thing that must never be wrong: the rules.
Organising top-level by *service* would scatter the rule engine into
whichever service used it most and grow duplicate cloud calls in each one —
so testing the rules would mean booting a service.

Organising by **purity** instead gives a strict dependency direction:

```
core/  ←  adapters/  ←  services/  ←  web/
```

Arrows point one way only. `core/` imports nothing from the project.
If it ever needs an adapter, the dependency is backwards.

## The ring model

Build order runs from the centre outward. Rings 0–1 have no cloud
dependency at all, which is deliberate — they are both the part that cannot
be rescued by good infrastructure and the part that is fastest to test.

| Ring | What | Lives in | Cloud? |
|---|---|---|---|
| 0 | Domain types + encoded rulebook | `core/models.py`, `core/rulebook.py` | No |
| 1 | Rule evaluation | `core/rules/` | No |
| 2 | Persistence + synthetic data | `adapters/firestore.py`, `services/ingestion/` | Yes |
| 3 | Metrics emission | `adapters/grafana_push.py`, `services/monitor/` | Yes |
| 4 | Agent + MCP wiring | `adapters/grafana_mcp.py`, `services/agent/` | Yes |
| 5 | API + the two views | `services/api/`, `web/` | Yes |
| 6 | Deployment | `deploy/` | Yes |

## The one hard rule

**No rule value may appear inside `core/rules/`.** Thresholds and rates are
read from the `RuleBook`; the logic dispatches only on machine-readable
condition keys. A number hardcoded in the rule engine is a number no
uploaded agreement can ever change — and upload-your-own-contract is the
long-term direction for the product, not a bolt-on.
