on-the-clock/
├── README.md
├── PROJECT_PLAN.md
├── FEATURES.md
├── LICENSE
├── .gitignore
├── .env.example
│
├── data-generator/              # Category 1: Data & Simulation
│   ├── generate.py              # main entry point
│   ├── models.py                # Person, Shift, Production dataclasses/schemas
│   ├── scenarios.py             # hand-crafted violation-triggering days
│   └── requirements.txt
│
├── agent/                       # Categories 2 & 4: Rule Engine + ADK Agent
│   ├── main.py                  # ADK agent entry point
│   ├── rules/
│   │   ├── meal_breaks.py       # 6-hour clock, NDB, grace period, extension
│   │   ├── rest_periods.py      # 12h/10h/11h zone logic, weekly rest
│   │   └── penalties.py         # dollar-amount lookup by category/tier
│   ├── tools/
│   │   └── grafana_mcp.py       # Grafana MCP connection + query/alert calls
│   ├── requirements.txt
│   └── tests/
│       └── test_rules.py        # edge case tests — meal/rest/stacking/etc.
│
├── backend/                     # Category 7: API layer serving both views
│   ├── main.py                  # FastAPI app
│   ├── routes/
│   │   ├── ops.py               # endpoints for the Ops view
│   │   └── executive.py         # endpoints for the Executive view
│   └── requirements.txt
│
├── frontend/                    # Categories 5 & 6: Ops View + Executive View
│   ├── src/
│   │   ├── views/
│   │   │   ├── OpsView.jsx
│   │   │   └── ExecutiveView.jsx
│   │   ├── components/
│   │   │   ├── CountdownCard.jsx
│   │   │   └── ExposureSummary.jsx
│   │   └── App.jsx
│   ├── package.json
│   └── vite.config.js
│
└── deploy/                      # Category 7: Deployment configs
    ├── backend.Dockerfile
    ├── frontend.Dockerfile
    └── cloudrun-deploy.sh
