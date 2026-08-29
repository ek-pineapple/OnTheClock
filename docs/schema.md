# On the Clock — Data Schema

**Status:** Week 1 deliverable. Supersedes Section 6 of [plan.md](plan.md).
**Last updated:** Aug 28, 2026

---

## 0. The governing principle

> **The granularity you *record* is not the granularity you *monitor*.**

There are two stores, and they are not redundant — they hold different things for different reasons.

| | **Firestore** (operational) | **Grafana** (observability) |
|---|---|---|
| Holds | Exact, durable record of every person, shift, and violation | Clocks, thresholds, trends |
| Optimized for | Correctness, referential integrity, audit | Time-range queries, alert evaluation |
| Retention | Indefinite | Bounded by plan (verify in account) |
| Precision | Exact | Approximate / downsampled |
| Queried by | Backend API, ADK agent (resolve tool) | Grafana alert engine, ADK agent (MCP) |

**Why this split is not a shortcut:** this is a compliance and payroll-adjacent tool. If a production disputes a $900 penalty six weeks after the shoot, "our metrics store aged that data out" is not an acceptable answer. The audit trail must not live only in Prometheus. Metrics stores are designed for *roughly right, fast* — which is disqualifying for the record of record.

**Why Grafana is still load-bearing, not decorative:**
- It runs the **alert engine** — threshold evaluation over time that we would otherwise have to write ourselves.
- It owns the **time dimension** — trend and rollup across the shoot.
- It is the **agent's live query surface** via MCP. The agent asking *"who is closest to a violation right now, across both productions"* is a real MCP call returning real data, not a contrivance for the submission checklist.

---

## 1. Firestore — operational store

Firestore is document-oriented: collections contain documents, documents may contain subcollections. All timestamps are **UTC**; see §5 for timezone handling.

### 1.1 `productions/{production_id}`

| Field | Type | Notes |
|---|---|---|
| `title` | string | Fictional. Never a real production. |
| `budget_tier` | enum | `theatrical \| low_budget \| ultra_low_budget \| student_short` |
| `cast_size` | int | Principals only; background counted via call groups |
| `shoot_start` | date | |
| `shoot_end` | date | |
| `timezone` | string | IANA, e.g. `America/Los_Angeles`. Display + shoot-day boundaries. |
| `is_synthetic` | bool | Always `true`. Surfaced in the UI as a label. |
| `created_at` | timestamp | |

### 1.2 `productions/{production_id}/people/{person_id}`

| Field | Type | Notes |
|---|---|---|
| `name` | string | Fictional |
| `performer_category` | enum | `principal \| background \| stunt_coordinator \| stunt_performer` |
| `contract_type` | enum | `day \| weekly` |
| `contract_schedule` | string \| null | e.g. `H-II`, `K-III`. Drives the misclassification check. |
| `call_group_id` | string \| null | Set for background performers; null otherwise |
| `active` | bool | |

**Design note — subcollection vs. top-level.** People are nested under a production, which means a single human working two productions is modeled as two documents. That is a deliberate scope cut. It also means we structurally *cannot* detect the most interesting real-world forced call: a performer dismissed from Show A at 11pm and called to Show B at 6am, where neither production's 2nd AD can see the other's clock. Promoting `people` to a top-level collection with a `production_id` field would enable it. Flagged as out of scope, not as an oversight.

### 1.3 `productions/{production_id}/call_groups/{call_group_id}`

Background performers are called and released in batches with a shared clock. The group is the monitored unit.

| Field | Type | Notes |
|---|---|---|
| `label` | string | e.g. "Background — Diner Ext." |
| `headcount` | int | Multiplier for exposure |
| `performer_category` | enum | `background` for now |
| `shoot_day` | int | |

Individual background performers still exist as `people` documents — the audit trail is per-person even though the clock is per-group. Same principle as §0.

### 1.4 `productions/{production_id}/shifts/{shift_id}`

One shift = one subject (person **or** call group) on one shoot day. **This is the core record.**

| Field | Type | Notes |
|---|---|---|
| `subject_type` | enum | `person \| call_group` |
| `subject_id` | string | |
| `shoot_day` | int | 1-based |
| `date` | string | `YYYY-MM-DD`, production-local |
| `zone` | enum | `studio \| distant \| overnight` |
| `scheduled_call_at` | timestamp | **Known in advance. Drives prediction.** |
| `actual_call_at` | timestamp \| null | Null until they arrive |
| `dismissed_at` | timestamp \| null | Null while still working |
| `non_deductible_meal` | `{start_at, end_at}` \| null | 15-min NDB within 2 hrs of call |
| `meals` | array | `[{index, out_at, in_at, extension_granted}]` |
| `headcount` | int \| null | Only when `subject_type == call_group` |
| `status` | enum | `scheduled \| on_clock \| wrapped` |

**Four fixes to the Section 6 model, made explicit:**

1. **`scheduled_call_at` is new and is the most important field in the schema.** The entire pitch is *predictive*. You cannot warn about a forced call without knowing the next scheduled call versus the earliest legal one. Section 6 had `call_time` only, which can only describe the past.
2. **Dismissal belongs to the shift that ends it**, not to the next day as `dismissal_time (previous day)`. Rest is a *pairwise* computation across consecutive shifts — see §1.5. This also makes the 56h weekly-rest rule computable, which the old shape did not.
3. **Meals are a list, not fixed `meal_break_1/2` slots** — so extensions and a possible third break don't require schema surgery.
4. **NDB is its own record**, not a boolean. We need its window to avoid false-positiving on early arrivals.

### 1.5 Derived: the rest calculation

Rest is not stored on a shift — it is computed between two:

```
required_rest = lookup(performer_category, zone)          # §1.7
earliest_legal_call = shift[n-1].dismissed_at + required_rest
rest_margin        = shift[n].scheduled_call_at - earliest_legal_call

rest_margin < 0  →  forced call
```

`consecutive_day_count` is likewise derived by walking shifts backward, not stored — a stored counter drifts the moment any record is corrected.

### 1.6 `penalty_rates/{rate_id}` — top-level reference

| Field | Type | Notes |
|---|---|---|
| `performer_category` | enum | |
| `budget_tier` | enum | |
| `violation_type` | enum | `forced_call \| meal_penalty` |
| `amount_usd` | number | |
| `unit` | enum | `per_increment_30min \| flat_day` |
| `effective_from` / `effective_to` | date | |
| `source_url` | string | Link to the SAG-AFTRA page it came from |
| `rule_version` | string | |

**Why a versioned collection instead of a hardcoded dict:** rates change between agreements, and a violation recorded today must not silently change value when a future rate is loaded. It also sets up the Week 5 stretch feature cleanly — an uploaded agreement produces *new versioned rows pending confirmation*, rather than mutating live constants.

### 1.7 `rule_thresholds/{threshold_id}` — top-level reference

| Field | Type | Notes |
|---|---|---|
| `performer_category` | enum | |
| `zone` | enum | |
| `required_rest_hours` | number | |
| `constraint` | string \| null | e.g. `once_per_4_consecutive_days`, `2_non_consecutive_days_per_week` |
| `source_url` | string | |
| `rule_version` | string | |
| `confidence` | enum | `confirmed \| needs_source_check` |

**`confidence` exists for a specific reason.** plan.md §5 notes that the stunt *performer* rest threshold must be source-checked and not assumed to match the stunt *coordinator's* 9 hours. Encoding "we have not verified this" in the data — and surfacing it in the UI — is more honest than hardcoding a guess, and it is demo-able as a feature rather than hidden as a gap.

### 1.8 `productions/{production_id}/violations/{violation_id}`

| Field | Type | Notes |
|---|---|---|
| `type` | enum | `forced_call \| meal_penalty \| schedule_mismatch` |
| `subject_type` / `subject_id` | | |
| `shift_id` | string | |
| `shoot_day` | int | |
| `status` | enum | `approaching \| violated \| cleared \| resolved` |
| `threshold_at` | timestamp | When the line is/was crossed |
| `detected_at` | timestamp | |
| `half_hour_increments` | int | Meal penalties escalate every 30 min |
| `headcount_at_detection` | int | 1 for a person, N for a group |
| `rate_per_increment_usd` | number | **Snapshotted from §1.6 at detection** |
| `penalty_amount_usd` | number | `increments × rate × headcount` |
| `computed_at` | timestamp | |
| `rule_version` | string | Which ruleset produced this verdict |
| `narrative` | string | Agent-generated NL summary |

**Two fixes to Section 6:** `status` gains the terminal states `cleared` and `resolved` (a violation that was approaching and then avoided is a *success*, and the product needs to show that). And `penalty_amount` alone was a snapshot with no provenance — escalating penalties need the increment count, the rate that produced it, and when it was computed, or the number is stale and unauditable the moment it is written.

---

## 2. Grafana / Prometheus — metrics

### 2.1 How Prometheus actually stores things

```
(metric_name + label set)  →  stream of (timestamp, float64)
```

The value is **always a number**. No strings, no nested objects, no joins, no foreign keys. One unique combination of metric name + label values is a **series**, and series are the unit of both cost and pain.

**Three rules this schema obeys:**

1. **Never put a date, `shoot_day`, or `shift_id` in a label.** Each new day would mint an entirely new set of series and cardinality would grow without bound forever. This is the most common way people blow up a metrics bill. Day lives in Firestore and in the Loki body.
2. **Deadlines are emitted as absolute unix timestamps, not as remaining seconds.** The dashboard computes `deadline - time()`. This is the standard Prometheus idiom (cf. `kube_pod_start_time_seconds`) and it means the countdown ticks smoothly at display refresh rate rather than at emit rate.
3. **Label sets are identical across every per-subject metric.** This is what makes exposure a plain `a * b * c` in PromQL instead of a `group_left` join — see §4.2.

### 2.2 The shared label set

Applied identically to every metric in §2.3:

| Label | Cardinality | Notes |
|---|---|---|
| `production_id` | ~2 | |
| `subject_type` | 2 | `person \| call_group` |
| `subject_id` | ~20 | |
| `performer_category` | 4 | |
| `contract_type` | 2 | |
| `zone` | 3 | |
| `budget_tier` | 4 | |

**Deliberately not labels:** `name`, `shoot_day`, `date`, `shift_id`, any timestamp.

`name` is excluded because it is presentation data. The Ops view joins it from Firestore in the backend. The ADK agent gets a **separate Firestore resolve tool** so it can turn `subject_id="p_07"` into "Dana Reyes" — which is better for the ADK story anyway, since it demonstrates multi-tool reasoning rather than a single MCP call. *(Alternative if that proves awkward: an `otc_subject_info{subject_id, name} 1` metric and a `group_left` join. Standard pattern, but a join we don't currently need.)*

### 2.3 Metrics

| Metric | Type | Value |
|---|---|---|
| `otc_scheduled_call_timestamp_seconds` | gauge | Unix ts of `scheduled_call_at` |
| `otc_earliest_legal_call_timestamp_seconds` | gauge | Unix ts of `prev.dismissed_at + required_rest` |
| `otc_meal_deadline_timestamp_seconds` | gauge | Unix ts. **Extra label: `meal_index` (`1\|2`)** |
| `otc_on_clock` | gauge | `1` while working, `0` otherwise |
| `otc_meal_penalty_increments` | gauge | Count of 30-min increments accrued; `0` while compliant |
| `otc_penalty_rate_usd` | gauge | Applicable rate from §1.6 |
| `otc_subject_headcount` | gauge | `1` for a person, `N` for a call group |

**Why `otc_subject_headcount` is `1` for individuals rather than absent:** it unifies persons and call groups. Exposure is *always* `increments × rate × headcount` with no branching anywhere in the query layer, the alert layer, or the frontend. A person is just a call group of one.

**Why `otc_on_clock` is emitted as an explicit `0` rather than dropped:** Prometheus marks a series stale ~5 min after it stops being written, and stale series behave inconsistently in alert expressions. Explicit `0` is unambiguous. Deadline metrics *are* dropped for wrapped shifts, since a deadline that no longer applies should not be plotted.

**Note that `rest_margin` is not emitted.** It is derived in PromQL (§4.1) from the two timestamps, which we need individually anyway for display ("earliest legal call is 6:00 AM"). One fewer series, and vector matching works cleanly because the label sets are identical.

### 2.4 Cardinality budget

```
Hackathon, this schema:
  ~20 subjects × 7 metrics (+1 for the 2nd meal_index)  ≈  160 series

Hackathon, if background were per-person:
  2 productions × 200 background × 7                    ≈  2,800 series

Real studio, this schema:
  20 productions × (30 cast + 3 call groups) × 7        ≈  4,600 series

Real studio, if background were per-person:
  20 productions × 330 people × 7                       ≈  46,000 series
```

The per-person background variant costs ~10× more at hackathon scale and ~10× more at studio scale, to deliver information the product has already decided to display as an aggregate. Nobody has ever needed background actor #147's individual countdown card.

---

## 3. Grafana / Loki — event log

Loki labels are **far** more cardinality-punishing than Prometheus labels — each unique combination is a separate stream. The line body, by contrast, is arbitrary JSON queried at read time.

**Stream labels (low cardinality only):**

| Label | Values |
|---|---|
| `production_id` | ~2 |
| `event_type` | `call \| meal_out \| meal_in \| dismissal \| violation_opened \| violation_escalated \| violation_cleared \| agent_alert` |
| `env` | `dev \| prod` |

≈ 2 × 8 × 1 = **16 streams.**

**Body (JSON):**

```json
{
  "subject_id": "p_07",
  "subject_type": "person",
  "name": "Dana Reyes",
  "shift_id": "sh_0412",
  "shoot_day": 12,
  "penalty_amount_usd": 150,
  "half_hour_increments": 2,
  "rule_version": "sag-2026.1",
  "narrative": "Second meal deadline passed at 19:42; 2 increments accrued."
}
```

**`subject_id` is a Prometheus label but a Loki body field.** Same value, different rules per store. This is the detail that most often trips people up, so it is stated explicitly rather than left implicit.

---

## 4. Query shapes

Every metric above exists to serve one of these. If a metric doesn't appear here, it shouldn't be emitted.

### 4.1 Ops view (2nd AD)

```promql
# Seconds until each meal deadline, on-clock subjects only
(otc_meal_deadline_timestamp_seconds - time()) and otc_on_clock == 1

# Rest margin — negative means a forced call is already scheduled
otc_scheduled_call_timestamp_seconds - otc_earliest_legal_call_timestamp_seconds

# "Approaching" band: meal deadline inside 30 minutes
(otc_meal_deadline_timestamp_seconds - time()) < 1800
  and (otc_meal_deadline_timestamp_seconds - time()) > 0
  and otc_on_clock == 1

# Currently accruing penalties
otc_meal_penalty_increments > 0
```

Both subtractions rely on default vector matching, which works only because §2.2 gives every metric an identical label set. That is the payoff for the constraint.

### 4.2 Executive view (UPM / Line Producer)

```promql
# Total live exposure — no branching between persons and call groups
sum(otc_meal_penalty_increments * otc_penalty_rate_usd * otc_subject_headcount)

# Split by production
sum by (production_id) (
  otc_meal_penalty_increments * otc_penalty_rate_usd * otc_subject_headcount
)

# Split by performer category — shows background volume vs. principal rate
sum by (performer_category) (
  otc_meal_penalty_increments * otc_penalty_rate_usd * otc_subject_headcount
)
```

A plain three-way multiply, no `group_left` anywhere. That is entirely a consequence of the identical-label-set rule and the headcount-of-1 decision.

### 4.3 Loki

```logql
# Every violation event today for one production
{production_id="prod_a", event_type=~"violation_.*"} | json

# One person's full timeline
{production_id="prod_a"} | json | subject_id = "p_07"

# What the agent actually said
{production_id="prod_a", event_type="agent_alert"} | json | line_format "{{.narrative}}"
```

### 4.4 Alert rules

| Alert | Expression | Fires |
|---|---|---|
| Forced call scheduled | `otc_scheduled_call_timestamp_seconds - otc_earliest_legal_call_timestamp_seconds < 0` | As soon as the schedule is published |
| Meal deadline approaching | `(otc_meal_deadline_timestamp_seconds - time()) < 1800 and otc_on_clock == 1` | 30 min out |
| Penalty accruing | `otc_meal_penalty_increments > 0` | On crossing |

**A product insight that falls out of the schema:** the two violation types have fundamentally different time behavior, and the Ops view should reflect that.

- A **forced call is determined the moment the call sheet is published** — potentially 12+ hours before it happens. It is not a countdown, it is a *standing defect in the schedule* that someone can still fix.
- A **meal penalty is a genuine live countdown** — the clock runs against a person currently working.

Presenting both as identical ticking cards would misrepresent the first. The forced-call card should read "still fixable — X hours to act," not just tick down.

---

## 5. Time, timezones, and the demo clock

**Storage:** all timestamps UTC. Unix seconds in Prometheus are UTC by definition. Firestore timestamps are UTC.

**Display:** production-local, via `productions.timezone` (IANA). A 6:00 AM call is meaningful to a 2nd AD; a UTC instant is not.

**Shoot-day boundaries are not midnight.** A shoot day is delimited by call and dismissal and routinely crosses midnight. `shoot_day` is an explicit integer on the shift; never derive it from a date.

**The demo clock: do not scale time.** Deadlines are absolute unix timestamps and Grafana's `time()` is real wall-clock. The seeder computes offsets from real `now()` at run time, placing demo deadlines 3–10 minutes out. The countdown is then genuinely real, with no fake-clock machinery anywhere.

A simulated clock with a scale factor would require either continuously rewriting historical timestamps or overriding `now` throughout the stack — and it would break Grafana's native `time()`, which is what makes the whole countdown work for free. Rejected.

`productions/{id}.demo_epoch` stores the anchor used at seed time so a run is reproducible.

---

## 6. Ingestion — the open question

Firestore is the write path; the emitter loop recomputes deadlines and writes to Grafana. Getting metrics *into* Grafana Cloud has several routes and **this is not yet decided**:

1. **Prometheus `remote_write`** from the FastAPI backend — canonical, but needs protobuf + snappy encoding.
2. **Grafana Alloy** as a sidecar scraping a `/metrics` endpoint on the backend — more moving parts, less custom code.
3. **Graphite / InfluxDB line-protocol endpoints** on Grafana Cloud — much simpler wire format, less idiomatic.

Loki has a plain JSON HTTP push endpoint, so the log path is straightforward regardless.

**This sits on the critical path and should be resolved in Week 2 alongside the MCP connection**, not deferred. It is the second-highest-risk unknown after MCP itself.

**Emitter cadence:** every 15s. Gauges are rewritten wholesale each cycle from Firestore state — the metrics store holds no state the operational store doesn't already have, so a restart loses nothing.

---

## 7. Open items

- [ ] Stunt *performer* rest threshold — source-check required. Seed as `confidence: needs_source_check`; do **not** assume it matches the stunt coordinator's 9 hours.
- [ ] Confirm Grafana Cloud free-tier metrics retention covers a full 5-week shoot for the executive trend view.
- [ ] Decide the ingestion route (§6).
- [ ] Confirm whether the agent's Firestore resolve tool is smooth in ADK, or whether an `otc_subject_info` metric + `group_left` is less friction.
