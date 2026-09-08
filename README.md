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

> **Status: M8 — public-demo hardening (built, not yet deployed).** M1 loads
> 38,309 rows to SQLite; M2 settles the target + features; M3 trains the
> `is_high_emitter` classifier (temporal ROC-AUC 0.86, building-disjoint 0.76);
> **M4–M5 are a hand-written tool-use loop on the Claude API** — `list_datasets`
> / `describe_schema` / `run_sql` (guardrailed), `predict` (the M3 model),
> `make_chart` (Plotly). **M6** adds a `click` CLI and a 15-question eval harness
> (~$0.004/q on Haiku with prompt caching, 15/15). **M7** is the Cloud Run image
> (slim + gunicorn, read-only baked-in DB, inline `data:` charts). **M8** wraps
> `/ask` with a response cache, per-visitor + global rate limits, a monthly
> budget cutoff, CORS lock, and an offline-first `frontend/` gallery — all inert
> without `REDIS_URL`, verified against a real Redis. `ruff` + `pytest` green
> (105 tests). Next: an actual Cloud Run deploy (needs a GCP project). Full
> design: `analytics-agent-architecture.md`.

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
```

`analytics.db` ships in the repo (a read-only snapshot). To rebuild it from
Socrata — do this when Seattle republishes the dataset (~annual):

```bash
python -m db.migrate                          # (re)create the schema
python -m ingest.run --source seattle_energy --full-refresh  # ~20s, ~38k rows
```

Ask the agent a question (needs `ANTHROPIC_API_KEY` in `.env`):

```bash
python -m app.cli ask "which property types have the worst emissions intensity?"
python -m app.api            # serve POST /ask + GET /health on :8000
python -m evals.run --dry-run # list the eval question set; drop --dry-run to grade (spends API $)
```

No `.env` needed for local queries against SQLite; the agent needs
`ANTHROPIC_API_KEY` (copy `.env.example`).

### Deploy (Cloud Run)

```bash
docker build -f deploy/Dockerfile -t analytics-agent .
docker run --rm -p 8080:8080 -e ANTHROPIC_API_KEY=sk-ant-... analytics-agent
curl localhost:8080/health

gcloud run deploy analytics-agent --source . --region us-west1 \
  --allow-unauthenticated --min-instances 0 \
  --set-secrets ANTHROPIC_API_KEY=anthropic-api-key:latest
```

The image bakes in `analytics.db` (opened `mode=ro`) and the model; it runs
`claude-haiku-4-5`, and `make_chart` returns charts as inline `data:` URIs.
Rate limits, response cache, and CORS lock-down are M8.

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
| **M3** | Baseline model | ✅ HGB + LogReg baseline; temporal + building-disjoint splits; ROC-AUC 0.86 / 0.76; `model_card.md` + metrics artifacts |
| **M4** | Agent loop | ✅ `agent/` — 3 tools + guardrails, hand loop, `trace.py`; `python -m agent.loop "question"`; live-verified, self-correcting SQL |
| **M5** | `predict` + `make_chart` | ✅ `predict` (M3 model, gated on `model_module`) + `make_chart` (Plotly, charts `data="last_query"`); agent picks the right tool per question |
| **M6** | Interface + evals | ✅ `app/cli.py` + `app/api.py` (`POST /ask`, `GET /health`); `evals/` — 15 questions, grades pass rate / iterations / $-per-q; prompt caching engaged (~$0.004/q on Haiku) |
| **M7** | Containerize + deploy | ✅ `deploy/Dockerfile` (slim + gunicorn) + `.dockerignore` + `cloudrun.yaml`; `analytics.db` + model committed and baked in; `mode=ro` DB + inline `data:` charts on `DEPLOY_MODE=deployed`; `docker build` + container run verified (`/health`, `/ask`) |
| **M8** | Public-demo hardening | ✅ `/ask` middleware — response cache, per-visitor + global rate limits, monthly-budget cutoff, CORS lock, optional Turnstile (all inert without `REDIS_URL`); offline-first `frontend/` gallery + `examples.json`; `warm_cache.py` + `MONITORING.md`. Verified vs a real Redis; not yet deployed |
| M9 | Expansion (stretch) | a second `SourceSpec`, or the permits join |
