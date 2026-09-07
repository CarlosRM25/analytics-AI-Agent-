# Agentic Data-Analyst

An AI agent that answers natural-language questions about Seattle's building
stock — *"which property types have the worst emissions intensity?"*, *"has 400
Pine St gotten more efficient since 2015?"*, *"what would the model expect a
1960s office of this size to emit?"* — by exploring a real database schema,
writing and running its own SQL, calling a trained model when the question is
predictive, drawing a chart when a chart helps, and correcting itself when a
query errors.

Public civic data (Seattle Building Energy Benchmarking, via the Socrata SODA
API) → config-driven ingestion → MySQL → a pandas/scikit-learn model → a
hand-written tool-use loop on the Claude API → a thin CLI / Flask interface.

> **Status: M0 — scaffold.** Repo structure, config, and tooling are in place;
> `pytest` and `ruff` are green. No feature logic yet. Ingestion (M1) is next.
> Full design: `analytics-agent-architecture.md` (kept with the portfolio
> planning docs).

## System overview

```
                        ┌─────────────────────────────────────────────────────────────┐
                        │                         CONFIG                                │
                        │  sources/seattle_energy.py   catalog/seattle_energy.yaml      │
                        │  .env  (DB x2, Socrata token, ANTHROPIC_API_KEY, caps)        │
                        └─────────────────────────────────────────────────────────────┘
                                              │ drives
   ┌───────────────┐   paginated GET   ┌──────▼───────┐   upsert    ┌──────────────────┐
   │ Socrata SODA  │ ───────────────►  │  ingest/run  │ ──────────► │   MySQL 8        │
   │ data.seattle  │  $limit/$offset   │  (generic)   │  (etl user) │  buildings       │
   │ .gov          │                   └──────────────┘             │  energy_records  │
   └───────────────┘                                                │  datasets        │
                                                                    │  ingestion_runs  │
                          ┌──────────────────────┐   read (etl)     └────────┬─────────┘
                          │  model/seattle_energy │ ◄─────────────────────────┘
                          │  features.py train.py │
                          │  artifacts/*.joblib   │
                          │  model_card.md        │
                          └───────────┬───────────┘
                                      │ loaded by predict()
   ┌──────────────┐   question   ┌────▼───────────────────────────────────┐
   │  CLI  /      │ ───────────► │            agent loop                    │
   │  Flask /ask  │ ◄─────────── │  Claude API (claude-opus-5, adaptive)    │
   └──────────────┘  answer +    │  tools: list_datasets · describe_schema  │
                     SQL + chart │         run_sql · predict · make_chart   │
                     + usage     │  guardrails · trace · MAX_ITERS          │
                                 └────┬───────────────────────┬────────────┘
                                      │ SELECT only           │ writes
                            ┌─────────▼────────┐     ┌─────────▼──────────┐
                            │ MySQL (agent RO  │     │ outputs/charts/*.html│
                            │ user, timeout,   │     │ logs/runs.jsonl      │
                            │ LIMIT enforced)  │     └────────────────────┘
                            └──────────────────┘
```

*Local / development topology. The public deployment — static page → Cloud Run →
baked-in SQLite — is section 10 of the architecture doc.*

## Quickstart

```bash
python -m venv .venv
.venv\Scripts\activate        # Windows;  source .venv/bin/activate on macOS/Linux
pip install -r requirements.txt
cp .env.example .env           # fill in when ingestion/agent work lands

ruff check .
pytest
```

## Repo layout

```
analytics-agent/
  sources/            # SourceSpec registry — seattle_energy.py (+ future crime/transit)
  ingest/             # generic SODA pull + upsert (ingest/run.py)
  db/                 # schema.sql, migrations/, migrate.py
  catalog/            # seattle_energy.yaml — human column docs, read by describe_schema
  model/seattle_energy/   # features.py, train.py, artifacts/, model_card.md
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
| M1 | Source spec + ingest | confirm the Socrata resource id + fields; load energy into MySQL |
| M2 | EDA notebook | distributions, missingness, age-vs-EUI / type-vs-emissions |
| M3 | Baseline model | `is_high_emitter` classifier, 2022/23/24 split, `model_card.md` |
| M4 | Agent loop | `describe_schema` + `run_sql` + hand loop + `trace.py` |
| M5 | `predict` + `make_chart` | agent picks the right tool per question type |
| M6 | Interface + evals | `cli.py`, `POST /ask`, `evals/` reporting pass rate / cost |
| M7 | Containerize + deploy | Cloud Run, SQLite baked in, `claude-haiku-4-5` |
| M8 | Public-demo hardening | offline gallery, rate limits, response cache, spend caps |
| M9 | Expansion (stretch) | a second `SourceSpec`, or the permits join |
