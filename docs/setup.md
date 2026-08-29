# Setup — Grafana Cloud & Google Cloud

Everything the project needs from the two platforms, in the order it becomes
necessary. Nothing here is required for Rings 0–1 (`core/` and `tests/` run
with no accounts at all) — this becomes load-bearing at Ring 2.

Values you collect go into `.env` at the repo root. Copy `.env.example` and
fill it in; `.env` is gitignored, `.env.example` is committed with
placeholders only.

---

## Part A — Grafana Cloud

### A1. Stack

1. Create a free account at [grafana.com](https://grafana.com/products/cloud/) — no card required.
2. Note your stack URL: `https://<your-stack>.grafana.net` → `GRAFANA_URL`.
3. **Accept the Grafana Assistant terms.** A stack admin has to do this once,
   and MCP access depends on it. Easy to forget and confusing to debug later.

Free tier gives 10k active metric series, 50 GB logs, and **14-day retention**.
The series budget is generous for this project (~160 series by design). The
retention limit is not cosmetic: it is why the Executive trend view reads
history from Firestore rather than Grafana.

### A2. Service account token — for MCP

This is what `mcp-grafana` authenticates with, and it is the one that makes
unattended deployment possible.

1. **Administration → Users and access → Service accounts → Add**
2. Give it a role. `Viewer` is enough to query; `Editor` is needed if you want
   the agent to create alert rules and annotations (it should — that is the
   more interesting demo).
3. Generate a token → `GRAFANA_SERVICE_ACCOUNT_TOKEN`. Shown once.

> **Note:** `docs/plan.md` records that Grafana's MCP server requires
> interactive browser OAuth with no machine-token option. That is not correct
> for the open-source `grafana/mcp-grafana` server, which takes
> `GRAFANA_SERVICE_ACCOUNT_TOKEN` directly. Nothing about the deployment needs
> to work around it.

### A3. Metrics push credentials

Where the countdowns get written.

1. From the Cloud Portal: **your stack → Prometheus / Mimir → Send Metrics**.
2. That page shows three things:
   - the remote-write endpoint URL
   - a numeric **username / instance ID**
   - a **Generate now** button for an API token
3. Record them as `GRAFANA_PROM_URL`, `GRAFANA_PROM_USER`, `GRAFANA_PROM_TOKEN`.

The same host also exposes an **Influx line-protocol** write path at
`/api/v1/push/influx/write`. That is the route worth trying first from Python:
a plain HTTP POST with basic auth, versus remote-write's protobuf + snappy
framing. Mimir translates it to Prometheus format on ingest, so nothing
downstream can tell the difference.

Read the exact host off your own stack page rather than assuming — it varies
by region.

### A4. Logs push credentials

Where the event stream goes (call, meal out, wrap, violation opened).

1. **your stack → Loki → Send Logs**
2. Same shape: endpoint, user ID, token.
3. → `GRAFANA_LOKI_URL`, `GRAFANA_LOKI_USER`, `GRAFANA_LOKI_TOKEN`

Loki's push API is plain JSON over HTTP, so this path has no protocol
question hanging over it.

### A5. Verify before building on it

```bash
# Should return your Grafana build info, not a 401.
curl -s -H "Authorization: Bearer $GRAFANA_SERVICE_ACCOUNT_TOKEN" \
  "$GRAFANA_URL/api/health"
```

---

## Part B — Google Cloud

### B1. Project and billing

1. Create a project. **Note the project *ID*, not the display name** →
   `GOOGLE_CLOUD_PROJECT`. These differ, and the difference is easy to miss:
   a project displayed as "OnTheClock" has an ID like `ontheclock-506923`.
   IDs are lowercase and usually carry a numeric suffix. The display name is
   not accepted by any client library.

   ```bash
   gcloud projects list --format="table(projectId,name)"
   ```
2. Attach billing (hackathon credit or free trial). Several APIs below will
   not enable without it.
3. Install the [gcloud CLI](https://cloud.google.com/sdk/docs/install), then:

```bash
gcloud auth login
gcloud config set project YOUR_PROJECT_ID
gcloud auth application-default login   # what the Python client libraries use
```

That last command is the one people skip. Without it the Firestore and Vertex
clients fail locally with a confusing credentials error.

### B2. Enable APIs

```bash
gcloud services enable \
  firestore.googleapis.com \
  aiplatform.googleapis.com \
  run.googleapis.com \
  cloudbuild.googleapis.com \
  artifactregistry.googleapis.com \
  secretmanager.googleapis.com
```

| API | Needed for |
|---|---|
| `firestore` | the operational store (Ring 2) |
| `aiplatform` | Gemini, via Vertex AI — what ADK calls (Ring 4) |
| `run` | deploying `api` and `monitor` (Ring 6) |
| `cloudbuild` + `artifactregistry` | building and storing those containers |
| `secretmanager` | Grafana tokens in production, never in the image |

### B3. Firestore database

```bash
gcloud firestore databases create --location=us-central1
gcloud firestore databases list --format="table(name,locationId,type,databaseEdition)"
```

Choices at creation, all of which matter:

| Setting | Pick | Why |
|---|---|---|
| Mode | **Native** | Datastore mode is a legacy App Engine path; the modern client assumes Native |
| Edition | **Standard** | Enterprise buys MongoDB compatibility and an advanced query engine we do not use, and costs more |
| Security rules | **Restrictive** | See note below — it costs us nothing |
| Location type | **Region** | Multi-region buys 99.999% vs 99.99% availability, at higher cost, for a dataset of a few hundred documents |
| Point-in-time recovery | **Off** | 7-day retention billed as storage, for data regenerable from a seed |
| Scheduled backups | **Off** | Same reasoning |
| Encryption | **Google-managed** | Cloud KMS is a key to manage and pay for; all data here is fictional |

**Restrictive rules will not block the backend.** Firestore security rules
apply only to the web and mobile SDKs. The server SDK authenticates with a
service account and bypasses rules entirely. "Open" would let anyone read and
delete everything for 30 days — a bad trade on a repo that must be public.

**The location is permanent.** It cannot be changed without deleting the
database. Prefer the region you will deploy Cloud Run into, since the monitor
loop reads Firestore every 15 seconds.

**Firestore's location and Vertex AI's are independent.** A database in
`us-east5` does not oblige `GOOGLE_CLOUD_LOCATION=us-east5`; Gemini model
availability varies by region and `us-central1` is the broadest. Set them
separately.

**If you named the database**, put that name in `FIRESTORE_DATABASE` — the
client does not find a named database on its own. `(default)` only applies if
you left the field blank.

Free tier covers 1 GiB stored and 50k reads / 20k writes per day — far beyond
what this project uses.

### B4. Gemini access

ADK can reach Gemini two ways. Use **Vertex AI**, since you have GCP credit
and it keeps everything under one project and one set of credentials:

```
GOOGLE_GENAI_USE_VERTEXAI=TRUE
GOOGLE_CLOUD_PROJECT=your-project-id
GOOGLE_CLOUD_LOCATION=us-central1
```

The alternative — an AI Studio API key against the Gemini Developer API — is
fewer steps but a separate billing surface and a key to manage. Not worth it
here.

### B5. Service account for deployment

Local development uses your own ADC credentials from B1. Cloud Run needs its
own identity:

```bash
gcloud iam service-accounts create on-the-clock-runtime \
  --display-name="On the Clock runtime"

PROJECT=$(gcloud config get-value project)
SA="on-the-clock-runtime@${PROJECT}.iam.gserviceaccount.com"

gcloud projects add-iam-policy-binding $PROJECT \
  --member="serviceAccount:${SA}" --role="roles/datastore.user"
gcloud projects add-iam-policy-binding $PROJECT \
  --member="serviceAccount:${SA}" --role="roles/aiplatform.user"
gcloud projects add-iam-policy-binding $PROJECT \
  --member="serviceAccount:${SA}" --role="roles/secretmanager.secretAccessor"
```

Least privilege on purpose — no `roles/editor`.

### B6. Verify

```bash
gcloud services list --enabled | grep -E 'firestore|aiplatform|run'
gcloud firestore databases list
```

---

## When each part is actually needed

| Ring | What you are building | Needs |
|---|---|---|
| 0–1 | types, rulebook, rule engine | **nothing** |
| 2 | Firestore + synthetic data | B1, B2, B3 |
| 3 | metrics push + monitor loop | A1, A3, A4 |
| 4 | ADK agent + MCP | A2, B4 |
| 5 | API + views | — |
| 6 | Cloud Run deploy | B5, plus Secret Manager entries |

Do not front-load all of it. The only item genuinely worth doing early is
**A1** — the Assistant terms acceptance is a one-time admin action that
silently blocks MCP work later if missed.
