# Handoff — continuation brief

**Last updated:** 28 Aug 2026
**Branch:** `feature/core-rule-engine` (off `develop`, not pushed)
**State:** Rings 0–1 complete, Ring 2 ~80% (offline portion only). 95 tests passing, no credentials required.

Read this before touching anything. It records what is built, what is
deliberately not, and — most importantly — **which of the older planning
documents contain figures that were later found to be wrong.**

---

## 1. Read these in this order

| File | Why |
|---|---|
| `docs/structure.md` | Layout, the ring model, and the one hard rule |
| `core/rulebook.py` | The verified SAG-AFTRA figures — **this is the source of truth, not `plan.md`** |
| `docs/schema.md` | Storage design, Firestore + Grafana split, PromQL shapes |
| `docs/setup.md` | Everything needed from Grafana Cloud and GCP |
| `docs/plan.md`, `README.md` | Original vision. **Contains superseded figures — see §4.** |

## 2. Run it

```bash
cd OnTheClock
python3 -m venv .venv && .venv/bin/pip install pytest
.venv/bin/python -m pytest -q                          # 95 tests, no cloud
.venv/bin/python -m services.ingestion.generate        # 14-day synthetic shoot
.venv/bin/python -m services.ingestion.scenarios       # single-day demo, human-readable
.venv/bin/python -m services.ingestion.scenarios --json
```

`.env` exists locally and is gitignored. `.env.example` is committed and must
contain **placeholders only** — real values leaked into it once already.

## 3. Ring status

| Ring | What | State |
|---|---|---|
| 0 | Domain types + encoded rulebook | **Done** |
| 1 | Rule engine (rest, weekly rest, meals, classification, combined) | **Done** |
| 2 | Persistence + generator | **Offline part done.** `adapters/firestore.py` not written |
| 3 | Metrics push + monitor loop | Not started |
| 4 | ADK agent + Grafana MCP | Not started |
| 5 | FastAPI + React views | Not started (two view mockups exist — see §8) |
| 6 | Cloud Run deploy | Not started |

## 4. Corrections to the original planning docs — read this

`plan.md` and `README.md` were written before the rules were verified against
source. Several figures in them are **wrong**. The verified values live in
`core/rulebook.py`, each with a `source_url` and a `confidence` grade.

| Claim in the old docs | Actual |
|---|---|
| Meal penalty is "$75/half-hour principal, $15/half-hour background" | An **escalating cumulative schedule**: 25 / 35 / 50 / 50 / 75… Five increments = **$235**, not 5×$75. Those flat figures are only the 5th-and-beyond tier. |
| Forced call costs "$900" | `min(one day's pay, cap)`. The cap binds only at higher scale rates. A weekly performer at Basic Theatrical prices at **$891.20**; Ultra Low Budget at **$256.60**. |
| Overnight location: 11h, two non-consecutive days | **Theatrical only.** Television permits no overnight reduction. This dimension (`ProductionType`) was missing entirely. |
| Distant location: 10h, once per four consecutive days | Also requires **exterior photography on both adjacent days**. Both conditions, not just the limit. |
| Weekly rest: 56h reducible to 54h | Also **36h** after a six-day location workweek. |
| "Grafana's MCP server requires interactive browser OAuth, no machine token" | False for `grafana/mcp-grafana`, which accepts `GRAFANA_SERVICE_ACCOUNT_TOKEN`. Unattended deployment is fine. |
| Deadline Sep 7 | Devpost says **Sep 9, 2026 @ 2:00pm PDT**. |

Because meal penalties are a piecewise cumulative sum, **they cannot be
computed in PromQL**. The emitter calculates the dollar figure and pushes the
answer; Grafana stores a number, not an equation.

## 5. Standing constraints

**No rule value may appear inside `core/rules/`.** Thresholds and rates come
from the `RuleBook`; the logic only dispatches on machine-readable condition
keys (`ReductionCondition`, `ReductionLimit`). A number hardcoded in the rule
engine is a number no uploaded agreement can ever change — and
upload-your-own-contract is the product direction, not a bolt-on. An unknown
condition key must fail loudly, never be skipped: silently ignoring an
unevaluated rule means reporting "all clear" on a check that never ran.

**Cost must stay near zero.** No always-on Cloud Run services, no
`min-instances=1`. Prefer free tiers and scale-to-zero. See §7.

**Money is `Decimal`, never `float`.** `serialization.py` refuses to encode or
decode money as a float. Background penalties accumulate in $7.50 steps across
85 people; the loss would be silent.

**All datetimes are UTC and timezone-aware.** Naive datetimes are rejected at
construction. Production-local time is applied only at display — except the
weekly-rest 6am rule, which is genuinely stated in local terms and converts
via `Production.timezone`.

## 6. Known defects and open items

1. **Explanation strings render UTC, not production-local.** `"past the 19:00
   UTC deadline"` where a 2nd AD needs `"past the 12:00 deadline"`. Cosmetic in
   the UI (structured fields carry local time) but **not** cosmetic for the
   agent, which paraphrases these strings aloud. `check_meal_period()` does not
   receive the `Production`, so it cannot localise. ~20 min fix. **Do this
   before Ring 4.**
2. **`adapters/firestore.py` does not exist.** Everything it needs
   (`core/serialization.py`, the `Repository` protocol, `InMemoryRepository`)
   is built and tested. Blocked only on `gcloud auth application-default login`.
3. **Rulebook `Confidence` needs extraction states** — currently grades *our
   research* provenance (`confirmed` / `corroborated` / `needs_source_check`).
   The upload feature will also need `extracted_pending_confirmation` and
   `user_confirmed`.
4. **Three rules remain unverified** and are surfaced by
   `RuleBook.unverified_rules()`: the stunt *performer* rest threshold (falls
   back to the zone default — do **not** assume it matches the coordinator's
   9h), the meal sub-rules (12-min grace, 30-min extension, NDB window), and
   the Student/Short scale rate. sagaftra.org returns 403 to automated
   fetching; these came from the support KB and payroll-industry sources.
5. **Documented simplifications**, commented where they occur: the workweek is
   a rolling seven-entry window for *reduction limits* (not a calendar week);
   "consecutive day" means consecutive entries in a subject's shift sequence.
   Both err toward requiring more rest, which is the safe direction.

## 7. Two design findings that change Ring 3

Both were discovered by running the rule engine over generated data, and both
must shape the monitor loop:

**The 15-second tick is a *computation* cadence, not a *read* cadence.**
Re-reading Firestore each tick is ~180 docs × 5,760 ticks ≈ **1M reads/day**
against a 50k/day free tier — roughly $18/month for a demo. Deadlines are pure
functions of stored timestamps, so the monitor must cache roster and shifts in
memory and re-read only when something actually changes (call, dismissal,
meal in/out). This is both the cheap design and the correct one.

**Evaluating only the current shift misses violations.** A weekly-rest breach
sits on the *first shift of the new workweek*; if the monitor checks only
today, it never sees Monday's. The loop must evaluate a window back to the
workweek start. Relatedly, **static checks need deduping** —
`check_contract_schedule` is per-subject, not per-shift, and fires once per
shift if evaluated naively.

## 8. What exists outside the repo

Two published artifacts (private to the user unless shared):

- **Architecture map** — rings, runtime data flow, the two-store split
  https://claude.ai/code/artifact/119b3208-1b1a-483b-bc8b-f0e386592a2f
- **Both product views** — Ops and Executive, live countdowns, driven by real
  rule-engine output
  https://claude.ai/code/artifact/1f0e802e-ba69-472a-92dc-0c2c44028f20

The view mockups are the agreed design for Ring 5; the HTML/CSS translates
directly into React components.

## 9. Blocked on the user

| Item | Blocks | Status |
|---|---|---|
| `gcloud auth application-default login` | Ring 2 completion | **Not done** — needed next |
| GCP billing (account `01B959-554E01-D1F5BD` is **closed**) | Rings 4 and 6 | Not done |
| $100 hackathon credit | Billing | Unknown |
| Grafana service account token + Assistant terms | Rings 3–4 | Not done |
| Grafana metrics/logs push credentials | Ring 3 | Not done |

Billing is **not** needed for Ring 2 — Firestore is already enabled and runs on
the free tier. If billing stays blocked, ADK can use the Gemini Developer API
(`GOOGLE_GENAI_USE_VERTEXAI=FALSE` + `GOOGLE_API_KEY` from AI Studio), which
has its own free tier and needs no GCP billing. Two lines in `.env`, no
architectural change.

## 10. Environment gotchas

- **The git repo is `OnTheClock/`**, not its parent `on-the-clock/`. Tooling has
  reported the parent as non-git; check `git status` before moving files.
- **Project ID is `ontheclock-506923`**, not the display name "OnTheClock".
  Client libraries reject the display name and IDs cannot contain uppercase.
- **Firestore database is named `on-the-clock-db`**, not `(default)`, in
  `us-east5`. The client will not find a named database without
  `FIRESTORE_DATABASE` set.
- **`GOOGLE_CLOUD_LOCATION=us-central1` deliberately differs from Firestore's
  `us-east5`.** Vertex AI and Firestore are located independently; us-central1
  has the broadest Gemini model availability.
- Python 3.14 via Homebrew is externally managed — use the `.venv`.

## 11. How the user prefers to work

Hands-on and learning-oriented: the point of the project is to understand
GCP / ADK / MCP deeply, not only to ship a demo. Explain concepts before
building on them, propose one piece at a time and confirm before writing, and
name trade-offs rather than quietly picking. Flag corrections plainly and move
on. Do not run ahead into unrequested work.
