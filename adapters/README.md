# adapters/

Every external dependency, isolated behind a small interface. Nothing else
in the project talks to Firestore, Grafana, or Gemini directly.

| File | Wraps |
|---|---|
| `firestore.py` | Reading/writing productions, people, shifts, violations |
| `grafana_push.py` | Pushing metrics (Influx line protocol → Grafana Cloud) |
| `grafana_mcp.py` | The `mcp-grafana` client the agent queries through |
| `gemini.py` | PDF extraction for call sheets and uploaded agreements |

**Why this layer exists.** Three concrete payoffs:

1. `core/` stays testable with zero cloud — no emulator, no credentials, no
   network. The part that must be correct becomes the easiest part to test.
2. The metrics ingestion route is still undecided (Influx line protocol vs.
   Prometheus `remote_write` vs. Grafana Alloy). With this layer it is one
   file to change; without it, the choice leaks into every service.
3. Tests swap a real Firestore for an in-memory fake for free.

**Rule:** adapters may import from `core/`, never the reverse. If `core/`
ever needs to import an adapter, the dependency is pointing the wrong way.
