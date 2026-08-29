# On the Clock — Feature Breakdown

**How to use this doc:** Work top-to-bottom within each category. Every P0 across all categories should be done before any P1 — a fully working core beats a partially working core + extras. P2s are explicitly optional; skip without guilt if time runs short.

**Priority key:**
- 🔴 **P0 — Critical path.** Without this, there is no working demo. Non-negotiable.
- 🟡 **P1 — Strengthens the pitch significantly.** Do these if P0 is done and stable.
- 🟢 **P2 — Stretch.** Only if P0 + P1 are done with real time to spare.

---

## Category 1: Data & Simulation
*Generates the fictional shift/production data everything else depends on.*

- 🔴 **Person data model** — schema for a person record (`performer_category`, `contract_type`, name). Nothing else can be built without this existing first.
- 🔴 **Shift/day data model** — call time, dismissal time, meal break timestamps, zone. This is the actual "clock" data.
- 🔴 **Basic synthetic day generator** — produces one realistic day of shift data for a small principal cast (5-10 people). Get this generating *plausible* numbers before worrying about edge cases.
- 🔴 **Violation-triggering scenarios** — hand-craft a few days where a violation is *about* to happen (not just random data) — you need this to exist reliably for the demo, not hope random generation produces it.
- 🟡 **Budget tier variation** — generate data for 2 fictional productions at different budget tiers (Theatrical vs. Low Budget), affecting penalty lookup values.
- 🟡 **Call sheet PDF parsing** — instead of purely code-generated data, use Gemini document processing to extract structured shift data (names, call times, locations) from a realistic (fictional) call sheet PDF. Replaces part of the synthetic-data-generator work rather than adding to it — a stronger, more grounded demo than an invisible code-only pipeline.
- 🟡 **Zone variation** — some generated days use Distant/Overnight location logic, not just flat Studio Zone.
- 🟡 **Background actor volume simulation** — generate a large batch (50-200) background performers for one demo day, to justify the aggregated view.
- 🟢 **Stunt coordinator edge case** — one person in the dataset with the 9-hour rest threshold instead of 11/12.
- 🟢 **Stunt performer edge case** — one person with `performer_category: stunt_performer` (distinct from stunt_coordinator) plus a `contract_schedule` field, including at least one deliberately-mismatched record (H-II performer tagged with K-III schedule) to demo the misclassification check.
- 🟢 **Multi-day continuity** — data spans several consecutive days so the "4th consecutive day" rest exception can actually be demonstrated, not just asserted.

---

## Category 2: Rule Engine (the agent's "brain")
*Pure logic — given shift data, decide what's compliant, what's approaching violation, what's violated.*

- 🔴 **Meal break rule: basic 6-hour clock** — flag when approaching/crossing the 6-hour-since-call threshold.
- 🔴 **Meal break rule: second break clock** — 6 hours from return-from-first-break, not just once per day.
- 🔴 **Rest period rule: flat 12-hour Studio Zone check** — the simplest, most common case.
- 🔴 **Penalty amount lookup** — given category + budget tier + violation type, return the correct dollar figure.
- 🟡 **Rest period exceptions** — Distant Location (10h, once/4 days), Overnight (11h, 2 non-consecutive days/week), weekly rest (56h/54h).
- 🟡 **Non-deductible meal (NDB) handling** — don't false-positive on early arrivals within the 15-min NDB window.
- 🟡 **Grace period (12 min) + extension (30 min) handling** — don't false-positive on legitimate delay mechanisms.
- 🟡 **Stacking violations** — correctly detect both a forced call AND meal penalty on the same person/day as independent events.
- 🟡 **Performer-category-specific thresholds** — stunt coordinator's 9-hour rule, background actor's lower penalty rate. Confirm stunt performer's own rest threshold via source-check before hardcoding (do not assume it matches stunt coordinator).
- 🟢 **Contract-schedule mismatch check** — flag when a `stunt_performer` record is tagged with the coordinator-only `K-III` flat-deal schedule instead of the correct weekly `H-II` — a real, documented misclassification pattern SAG-AFTRA actively pursues. Cheap validation rule, distinct from the clock-based checks.
- 🟢 **Consecutive-day tracking** — rolling counter enabling the "once every 4th day" exception logic.

---

## Category 3: Grafana Integration
*Where your data actually lives and where the agent queries/alerts from.*

- 🔴 **Grafana Cloud account + Assistant terms accepted** — blocking setup step, do this literally first.
- 🔴 **Push synthetic data into Grafana as metrics/logs** — data has to live there before anything can query it.
- 🔴 **Grafana Cloud MCP server connection working end-to-end** — the single highest-risk dependency in the whole project. Prove this early.
- 🔴 **Basic query via MCP from your agent** — confirm the agent can pull live data through MCP tools, not just that the connection exists.
- 🟡 **Alert/annotation via MCP** — agent actually creates a Grafana alert or dashboard annotation when a violation approaches (not just detects it internally).
- 🟢 **AI Observability instrumentation** — monitor your own agent's behavior (token cost, latency) as a demo bonus, per Grafana's optional add-on.

---

## Category 4: Agent Orchestration (ADK)
*Wraps the rule engine + Grafana calls into an actual agent that reasons and acts.*

- 🔴 **ADK agent skeleton** — basic agent that can call a function and return a response.
- 🔴 **Function calling: query live shift data** — agent pulls current data via Grafana MCP tool call.
- 🔴 **Function calling: evaluate rules** — agent runs data through the rule engine (Category 2) and gets a verdict.
- 🟡 **Forced function calling: always re-check before "all clear"** — guardrail so the agent can't skip the compliance check step.
- 🟡 **Natural-language alert generation** — agent produces a human-readable summary, not just raw JSON ("Actor X is 20 min from a forced call").
- 🟢 **Spoken alerts via Gemini TTS** — pipe existing alert text through TTS. Real justification, not novelty: a busy, loud set means people aren't staring at a dashboard — a spoken alert reaches a 2nd AD moving between departments better than another screen. Cheap once text alerts (above) already work.
- 🟢 **Agreement-upload extraction (stretch feature)** — Gemini document processing extracts candidate rule thresholds from an uploaded PDF, surfaced as an editable confirmation screen, never auto-applied silently.

---

## Category 5: Frontend — Ops View (2nd AD)
*Live, tactical, per-person.*

- 🔴 **Basic live countdown display** — one person, one countdown, updating in real time. Prove the core visual works before scaling it up.
- 🔴 **Multiple simultaneous countdowns** — the real demo needs several people tracked at once, not just one.
- 🟡 **Visual alert state** — clear color/state change when a person crosses from "safe" → "approaching" → "violated."
- 🟡 **Aggregated background-actor view** — a count/summary rather than individual cards, given volume.
- 🟢 **Filtering/sorting** (e.g., "show only people approaching violation") — polish, not core.

---

## Category 6: Frontend — Executive View (UPM / Producer)
*Aggregated, financial, trend-oriented.*

- 🔴 **Today's $ exposure summary** — total penalty risk/incurred for the current simulated day.
- 🟡 **Weekly trend view** — how exposure is trending across the shoot, not just a single-day snapshot.
- 🟢 **Budget-tier comparison** — showing how the same violation pattern costs differently across your two fictional productions.

---

## Category 7: Deployment & Infra

- 🔴 **Backend deployed and reachable** (Cloud Run) — a real URL, not localhost.
- 🔴 **Frontend deployed and reachable** — same requirement.
- 🔴 **No secrets committed** — Secret Manager or environment variables only.
- 🟡 **Clean redeploy from scratch works** — test that a fresh deploy doesn't depend on manual steps you forgot to script/document.

---

## Category 8: Repo & Submission Compliance
*Not glamorous, but any single miss here can disqualify an otherwise great project.*

- 🔴 **Public repo**
- 🔴 **MIT LICENSE file at root, GitHub-detected in About section**
- 🔴 **README with working setup instructions** (test from a clean clone)
- 🔴 **Code visibly imports/calls Grafana MCP + Google Cloud at runtime** — not just mentioned in prose
- 🔴 **3-minute demo video** — public, YouTube/Vimeo, functional (not cinematic), English/subtitled
- 🔴 **Devpost form completed, Grafana track selected**
- 🟡 **.env.example committed** with placeholder values

---

## Suggested Build Order (pulling all P0s into one sequence)

1. Data models (Cat. 1) → 2. Basic data generator (Cat. 1) → 3. Grafana account + push data (Cat. 3) → 4. MCP connection proven (Cat. 3) → 5. Core rule engine: meal + basic rest (Cat. 2) → 6. ADK agent skeleton + function calling (Cat. 4) → 7. Ops view basic countdown (Cat. 5) → 8. Executive view basic summary (Cat. 6) → 9. Deploy both (Cat. 7) → 10. Repo compliance pass (Cat. 8) → **then loop back for P1s in the same order, then P2s if time remains.**
