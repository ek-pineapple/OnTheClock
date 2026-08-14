# On the Clock — Project Plan

**Hackathon:** Agentic Cinema: The Blockbuster Hackathon
**Track:** Grafana
**Deadline:** Sep 7, 2026 @ 2:00pm PDT
**Repo status:** Public, MIT licensed (required for submission)

---

## 1. The One-Sentence Pitch

> Forced calls and meal penalties cost productions up to $900 per person, per violation — completely avoidable, but currently caught only after the fact by a human with a stopwatch. On the Clock watches it live and catches it before it happens.

---

## 2. The Problem (sourced)

- SAG-AFTRA rest period violations ("forced calls"): performer entitled to 12-hour rest between dismissal and next call (reducible to 10-11 hrs under specific zone/day conditions). Violation penalty: full day's pay, up to $900 (day performers) / $950 (weekly performers), per person, per violation. [SAG-AFTRA: Rest Periods](https://www.sagaftra.org/rest-periods-forced-calls-0)
- Meal penalties: first meal break required within 6 hrs of call, second within 6 hrs of returning from first. Violations escalate every 30 min. As of the 2026 agreement: $75/half-hour for principal performers, $15/half-hour for background performers. Reduced flat $25/half-hour rate applies for Student/Short/Ultra Low Budget films. [SAG-AFTRA: Meal Periods](https://www.sagaftra.org/meal-periods)
- Rules exist for safety, not just pay — fatigue increases risk of on-set accidents, not just discomfort.
- Currently tracked manually (2nd AD / script supervisor with a stopwatch), reconciled against payroll after the fact — not monitored live.
- Industry sources confirm this is a persistent, "commonly missed" cost, and that people are already reaching for basic scheduling alerts (just not agentic ones).
- **Important framing:** this is a cost-avoidance / compliance tool for the **production's budget**, built around a safety-motivated union rule — not a "gift to actors" framing. The performer is typically paid *more* when a violation occurs, so the production (not the performer) is the party motivated to avoid it.
- This is a **US-specific, SAG-AFTRA-specific** contractual (not governmental) rule. Other countries have separate, non-identical union equivalents (UK Equity/Bectu, etc.). Framed honestly as "modeled on SAG-AFTRA, generalizable to similar agreement structures."

---

## 3. Why This Idea (vs. alternatives considered)

Compared against: (1) Script supervisor/continuity agent (ClickHouse), (2) VFX render farm monitor (Grafana — rejected, already productized by AWS Deadline Cloud), (3) DIT footage ingest monitor (Grafana). **This one (SAG-AFTRA compliance) won** on: cleanest Grafana MCP fit (genuine alerting/threshold problem, not a stretch), most dramatic/legible live demo (countdown to a real dollar penalty), strongest "non-obvious tool use" angle (Grafana reframed from infra monitoring to labor compliance), and clearest differentiation (no existing product found doing this).

---

## 4. Industry Hierarchy (who this serves)

```
Producer(s) / Executive Producers
        │
   Line Producer   ← owns budget & logistics; all dept heads report here
        │
     UPM   ← breaks budget into departments; "significant voice" on budget issues
        │
     1st AD   ← runs the floor; safety advocate; keeps schedule on track
        │
     2nd AD   ← creates call sheets; liaises directly with talent;
        │        THE person tracking individual clocks in real time
        │
   2nd-2nd AD / 3rd AD   ← coordinates background actors
```

**Two role-based views map directly onto this hierarchy** (not an arbitrary UI choice):
- **Ops view** → 2nd AD (and 1st AD, adjacent) — live, per-person, tactical
- **Executive view** → UPM / Line Producer — aggregated $ exposure, trend over the shoot

---

## 5. Scope: What's In vs. Explicitly Out

### In scope (build this)
- SAG-AFTRA rest-period rules incl. exceptions: Studio Zone (flat 12h), Distant Location (10h, once per 4 consecutive days), Overnight Location (11h, 2 non-consecutive days/week), weekly rest (56h, reducible to 54h)
- Meal break rules incl.: 6-hour rolling clock, non-deductible meal (15 min, within 2 hrs of call), 12-min grace period, 30-min "extension" (legit, not a violation)
- Performer category dimension: `principal | background | stunt_coordinator | stunt_performer` — different penalty amounts and (for stunt coordinators) different rest-hour threshold (9h vs 11/12h). Stunt performers are a distinct category from stunt coordinators — separate contract schedule (weekly Schedule H-II vs. coordinator's Flat Deal Schedule K-III), separate department reporting line (stunt dept, outside standard crew hierarchy). Exact rest-hour threshold for stunt performers needs a source-check when this is built (don't assume it matches coordinators).
- **Bonus compliance check (cheap, distinctive):** contract-schedule mismatch flag — SAG-AFTRA has actively pursued cases of producers misclassifying stunt performers under the coordinator's flat-deal schedule to avoid proper weekly pay/residual terms. A simple validation (`performer_category == stunt_performer` but `contract_schedule == K-III`) flags this — real, well-documented, and cheap to add alongside the clock-based rules.
- Budget tier dimension: `Theatrical | Low Budget | Ultra Low Budget | Student/Short` — changes penalty dollar amounts via lookup table
- Zone dimension: `Studio Zone | Distant Location | Overnight Location` — drives which rest-hour exception applies
- Stacking violations — a person can incur both a forced call AND a meal penalty same day; evaluated independently
- Aggregated (not per-person) view for background actors given volume (hundreds of people)
- Two fictional demo productions (different budget tiers) — NOT real movie names/data (confidentiality + factual-risk reasons)
- **Stretch (Week 5 only, if core is solid):** Upload-an-agreement feature — Gemini extracts candidate rule thresholds from an uploaded PDF, presented as an editable confirmation screen before going live (never silently auto-applied). Hardcoded SAG-AFTRA ruleset remains the reliable default/fallback.

### Explicitly out of scope (state this clearly in README/pitch — signals maturity, not gaps)
- IATSE (crew) rules — separate rule set from SAG-AFTRA cast rules, not covered
- State/federal labor law operating alongside union rules (e.g., California's independent meal-break law) — real, but a much larger multi-jurisdiction problem
- International union agreements (UK Equity/Bectu, etc.) — separate legal/contractual regimes entirely
- Real production data of any kind — all data is fictional/synthetic, clearly labeled as such

---

## 6. Data Model (core fields)

**Person record:**
- `person_id`, `name` (fictional), `performer_category` (`principal | background | stunt_coordinator | stunt_performer`), `contract_type` (`day | weekly`), `contract_schedule` (e.g., `H-II | K-III` — used for the stunt-performer misclassification check)

**Shift/day record:**
- `call_time`, `dismissal_time` (previous day), `zone` (`studio | distant | overnight`), `consecutive_day_count` (for the 4th-day exception), `meal_break_1_start/end`, `meal_break_2_start/end`, `non_deductible_meal_used` (bool)

**Production record:**
- `production_id`, `title` (fictional), `budget_tier`, `cast_size`, `shoot_start/end`

**Violation/alert record:**
- `type` (`forced_call | meal_penalty`), `person_id`, `threshold_crossed_at`, `penalty_amount`, `status` (`approaching | violated`)

---

## 7. Tech Stack

| Layer | Tech |
|---|---|
| Agent framework | Google ADK (Python) |
| Cloud platform | Gemini Enterprise Agent Platform / Google Cloud |
| Observability/alerting | Grafana Cloud + Grafana Cloud MCP server (`grafana/mcp-grafana`) |
| Backend API | FastAPI |
| Frontend | React + Vite |
| Deployment | Cloud Run |

**Known setup friction to front-load early:** Grafana's hosted MCP server requires one-time interactive browser OAuth — no service-account/machine-token option. Fine for building/demo recording; note if considering unattended deployment (self-hosted MCP + service account token is the alternative).

---

## 8. Build Phases / Timeline (Aug 2 → Sep 7, 2026)

### Week 1 (Aug 2–8): Foundation
- Claim $100 GCP hackathon credit (do immediately — approval takes 1-5 days)
- Set up Grafana Cloud free tier, accept Assistant terms
- Finalize data schema (Section 6 above)
- Choose ADK language (Python locked in)

### Week 2 (Aug 9–15): Data Pipeline + Grafana Connection
- Build synthetic data generator (2 fictional productions, different budget tiers)
- Push synthetic shift data into Grafana as metrics/logs
- **Get Grafana MCP server connection working end-to-end — hardest dependency, do NOT defer this**

### Week 3 (Aug 16–22): Agent Logic
- Build ADK agent: rule engine covering all in-scope edge cases (Section 5)
- Function calling for alert/query actions; forced function calling to always re-check rules before "all clear"
- Natural-language alert summaries

### Week 4 (Aug 23–29): Two Views + Deployment
- Build Ops view (per-person countdowns + aggregated background-actor view)
- Build Executive view (rollup, $ exposure, trend)
- Deploy via Cloud Run; Secret Manager for keys
- Start README/repo polish

### Week 5 (Aug 30–Sep 6): Demo, Stretch Feature, Submission
- Record 3-min demo video (see script outline, Section 9)
- If core is solid: build upload-agreement stretch feature (Section 5)
- Finalize public repo: LICENSE visible in About section, clean README, working setup instructions
- Submit Devpost form (select Grafana track)
- **Sep 6 = buffer day** — deadline is Sep 7 @ 2pm PDT, not end of day

---

## 9. Demo Video Script Outline (3 min)

- **0:00–0:20** — Open on stakes: the $900 penalty figure + "commonly missed" industry quote. Not tech talk yet.
- **0:20–0:45** — The gap: how this is tracked today (manual, after-the-fact)
- **0:45–2:00** — Live demo: Ops view countdown ticking → agent catches it via live Grafana MCP query → cut to Executive view showing the same event as $ exposure/trend
- **2:00–2:40** — Name the clever tool reframe explicitly: "Grafana, normally infra observability, reframed for labor compliance"
- **2:40–3:00** — Close: one line on deliberate scope (what's not covered), one line on $ impact extrapolated across a shoot

---

## 10. Submission Checklist

- [ ] Repo public
- [ ] LICENSE (MIT) at root, GitHub-detected in About section
- [ ] Hosted/deployed project URL, actually working
- [ ] Code demonstrably calls Grafana MCP + Google Cloud/Gemini at runtime (not just named in README)
- [ ] 3-min demo video, YouTube/Vimeo, public, English/subtitled, shows real functioning
- [ ] Partner track selected: Grafana
- [ ] Devpost form fully completed
- [ ] .env.example committed; no secrets/keys in repo history
- [ ] README setup instructions verified to work from a clean clone

---

## 11. Naming

**"On the Clock"** — chosen over "Forced Call" (too jargon-heavy for non-industry judges) and "Turnaround" (too ambiguous with unrelated meanings). Plain language, self-explanatory, doubles as the literal mechanic (everyone tracked against a clock) and the emotional tone (time pressure).

Repo slug suggestion: `on-the-clock`
