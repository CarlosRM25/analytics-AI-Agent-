# Agentic Data-Analyst

An AI agent that answers natural-language questions about Seattle's building
stock — *"which property types have the worst emissions intensity?"*, *"has 400
Pine St gotten more efficient since 2015?"*, *"what would the model expect a
1960s office of this size to emit?"* — by exploring a real database schema,
writing and running its own SQL, calling a trained model when the question is
predictive, drawing a chart when a chart helps, and correcting itself when a
query errors.

Public civic data (Seattle Building Energy Benchmarking, via the Socrata SODA
API) → config-driven ingestion → SQLite → a pandas/scikit-learn model → a
hand-written tool-use loop on the Claude API → a thin CLI / Flask interface.

> **Status: M2 — EDA complete.** M1 loads 38,309 rows / 3,871 buildings
> (2015–2025) into SQLite (counts verified against the portal); `notebooks/01_eda.ipynb`
> settles the M3 target, feature set, and outlier rule. `ruff` + `pytest` green
> (22 tests). Next: M3 (baseline model). Full design:
> `analytics-agent-architecture.md` (kept with the portfolio planning docs).

## System overview

```
                     ┌────────────────────────────────────────────────────────┐
                     │  CONFIG   sources/seattle_energy.py   catalog/*.yaml     │
                     │           SQLITE_PATH · SOCRATA_APP_TOKEN? · caps        │
                     └────────────────────────────────────────────────────────┘
                                          │ drives
   ┌───────────────┐  paginated GET  ┌─────▼──────┐  upsert     ┌──────────────────┐
   │ Socrata SODA  │ ──────────────► │ ingest/run │ ──────────► │  analytics.db     │
   │ data.seattle  │  $limit/$offset │ (generic)  │  rw_engine  │  buildings        │
   │ .gov          │                 └────────────┘             │  energy_records   │
   └───────────────┘                                            │  datasets         │
                                                                │  ingestion_runs   │
                        ┌──────────────────────┐   read         └────────┬──────────┘
                        │  model/seattle_energy │ ◄───────────────────────┘
                        │  features.py train.py │
                        │  artifacts/*.joblib   │
                        └───────────┬───────────┘
                                    │ loaded by predict()
   ┌──────────────┐  question   ┌───▼────────────────────────────────────┐
   │  CLI  /      │ ──────────► │            agent loop                    │
   │  Flask /ask  │ ◄────────── │  Claude API (claude-opus-5, adaptive)    │
   └──────────────┘  answer +   │  tools: list_datasets · describe_schema  │
                     SQL + chart│         run_sql · predict · make_chart   │
                     + usage    │  guardrails · trace · MAX_ITERS          │
                                └───┬────────────────────────┬────────────┘
                        ro_engine   │ SELECT only            │ writes
                     (query_only) ┌─▼────────────────┐   ┌───▼────────────────┐
                                  │  analytics.db     │   │ outputs/charts/*.html│
                                  │  (read-only)      │   │ logs/runs.jsonl      │
                                  └──────────────────┘   └────────────────────┘
```

*The same SQLite file is used locally and, baked into the image, in the Cloud Run
deployment (section 10 of the architecture doc).*

## Quickstart

```bash
python -m venv .venv
.venv\Scripts\activate        # Windows;  source .venv/bin/activate on macOS/Linux
pip install -r requirements.txt

ruff check . && pytest

python -m db.migrate                        # create analytics.db
python -m ingest.run --source seattle_energy # load ~38k rows (~30s)
```

No `.env` needed for local work — SQLite has no credentials. Add one (copy
`.env.example`) when you get to the agent (M4): it needs `ANTHROPIC_API_KEY`.

## Repo layout

```
analytics-agent/
  sources/            # SourceSpec registry — seattle_energy.py (+ future crime/transit)
  ingest/             # generic SODA pull + upsert (ingest/run.py)
  db/                 # schema.sql, migrations/, migrate.py
  catalog/            # seattle_energy.yaml — human column docs, read by describe_schema
  model/seattle_energy/   # features.py, train.py, artifacts/, model_card.md
  notebooks/          # 01_eda.ipynb — exploratory analysis (M2)
  agent/              # tools.py, loop.py, prompts.py, trace.py
  app/                # cli.py, api.py, limits.py, cache.py
  frontend/           # static chat UI: index.html, examples.json (offline gallery)
  deploy/             # Dockerfile, cloudrun.yaml, warm_cache.py
  evals/              # questions.yaml, run.py, results/
  tests/              # pytest — no API key needed
  logs/               # runs.jsonl  (gitignored)
  outputs/charts/     # generated Plotly HTML/PNG  (gitignored)
  config.py           # pydantic-settings Settings, validated at startup
```

## Milestones

| # | Milestone | |
|---|---|---|
| **M0** | Scaffold | ✅ structure, `config.py`, tooling, one passing test |
| **M1** | Source spec + ingest | ✅ `teqw-tu6e` verified; SourceSpec + catalog; migrate + ingest; 38,309 rows in SQLite, counts match portal |
| **M2** | EDA notebook | ✅ `notebooks/01_eda.ipynb` — target balance, missingness, outlier rule, weak-signal finding, leakage check |
| M3 | Baseline model | `is_high_emitter` classifier, 2022/23/24 split, `model_card.md` |
| M4 | Agent loop | `describe_schema` + `run_sql` + hand loop + `trace.py` |
| M5 | `predict` + `make_chart` | agent picks the right tool per question type |
| M6 | Interface + evals | `cli.py`, `POST /ask`, `evals/` reporting pass rate / cost |
| M7 | Containerize + deploy | Cloud Run, SQLite baked in, `claude-haiku-4-5` |
| M8 | Public-demo hardening | offline gallery, rate limits, response cache, spend caps |
| M9 | Expansion (stretch) | a second `SourceSpec`, or the permits join |
