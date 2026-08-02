# On the Clock

**An agent that watches cast and crew shift clocks in real time and flags a labor-compliance violation *before* it happens — not after it's already cost the production money.**

Built for [Agentic Cinema: The Blockbuster Hackathon](https://agentic-cinema.devpost.com/) — **Grafana track.**

---

## The Problem

Under SAG-AFTRA's collective bargaining agreement, performers are entitled to specific rest periods and meal breaks. Violating them isn't a soft guideline — it's a contractual penalty with a fixed dollar cost:

- **Forced call (rest period violation):** a performer must get 12 hours of rest between dismissal and their next call (reducible to 10–11 hours in specific location/day scenarios). Violating this costs a full day's pay, up to **$900–$950 per performer, per violation**. ([SAG-AFTRA: Rest Periods](https://www.sagaftra.org/rest-periods-forced-calls-0))
- **Meal penalty:** performers must get a meal break within 6 hours of call, and again within 6 hours of returning from the first. Violations start at **$25 for the first half hour** and escalate every 30 minutes. ([SAG-AFTRA: Meal Periods](https://www.sagaftra.org/meal-periods))

This isn't a hypothetical inefficiency — it's a widely-acknowledged blind spot in how productions currently operate:

- *"Forced calls are a commonly missed item when budgeting for SAG payroll, and it is a completely avoidable expense through proper scheduling."* — [ABS Payroll & Accounting](https://abspayroll.com/budgeting-sag-aftra-payroll/)
- *"Meal penalties can quickly add up to thousands of dollars in a given week... without a reliable pulse on set workflow and detailed documentation."* — [Media Services](https://www.mediaservices.com/blog/production-meal-penalties-iatses-new-rules/)

Today, this is tracked manually — a script supervisor or 2nd AD watching a stopwatch, cross-referenced against payroll after the fact. **On the Clock** turns it into a live, monitored system instead.

> **Note on data:** All productions, cast members, and schedules used in this project are entirely fictional, generated to reflect realistic industry patterns (budget tiers, shoot lengths, cast sizes) based on publicly available industry statistics. No real production's actual data is used or represented.

---

## How It Works

1. A synthetic data generator simulates a production day: call times, meal breaks, wrap times, per-performer contract type (day vs. weekly).
2. This shift data is pushed into **Grafana Cloud** as live metrics/logs.
3. An **ADK-based agent** connects to the **Grafana Cloud MCP server** at runtime, continuously querying live shift data.
4. The agent evaluates SAG-AFTRA rest-period and meal-break rules per person, per rolling clock — including exceptions (studio zone vs. distant location, 4th-consecutive-day rule, non-deductible meals, grace periods).
5. When a violation is approaching, the agent fires an alert **before** the threshold is crossed, not after.
6. Two role-based views surface this data differently:
   - **Ops view** (2nd AD) — live per-person countdowns, "who's at risk right now"
   - **Executive view** (UPM / Line Producer) — aggregated dollar exposure, daily/weekly penalty risk trend

---

## Tech Stack

| Layer | Tech |
|---|---|
| Agent framework | Google Agent Development Kit (ADK), Python |
| Cloud platform | Gemini Enterprise Agent Platform / Google Cloud |
| Observability & alerting | Grafana Cloud + Grafana Cloud MCP server |
| Backend API | FastAPI |
| Frontend | React + Vite |
| Deployment | Cloud Run |

---

## Project Structure

```
on-the-clock/
├── agent/              # ADK agent logic, rule engine, Grafana MCP integration
├── data-generator/      # Synthetic production/shift data generator
├── backend/             # FastAPI service exposing agent + data to frontend
├── frontend/             # React app — Ops view + Executive view
├── .env.example
├── .gitignore
├── LICENSE
└── README.md
```

---

## Setup

### Prerequisites
- Python 3.11+
- Node.js 18+
- A free [Grafana Cloud](https://grafana.com/products/cloud/) account (stack admin must accept the Grafana Assistant terms once)
- Google Cloud account with billing enabled ([free trial](https://cloud.google.com/free) or hackathon credit)

### 1. Clone and configure
```bash
git clone https://github.com/<your-username>/on-the-clock.git
cd on-the-clock
cp .env.example .env
# fill in .env with your Grafana Cloud stack URL and GCP project ID
```

### 2. Backend
```bash
cd agent
python -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
python main.py
```

### 3. Frontend
```bash
cd frontend
npm install
npm run dev
```

### 4. Generate synthetic data
```bash
cd data-generator
python generate.py --production studio-tentpole   # or --production indie
```

---

## Hackathon Track

Submitting to the **Grafana** partner track. This project uses the Grafana Cloud MCP server (`grafana/mcp-grafana`) at runtime to query live metrics/logs representing cast and crew shift data, and to manage alerts for approaching violations.

---

## License

MIT — see [LICENSE](./LICENSE).

## Disclaimer

This project is a hackathon prototype demonstrating a compliance-monitoring pattern. It is not affiliated with, endorsed by, or built using any real data from SAG-AFTRA, any studio, or any real production. Rule logic is based on publicly published SAG-AFTRA contract terms and is intended as a proof of concept, not legal or payroll advice.
