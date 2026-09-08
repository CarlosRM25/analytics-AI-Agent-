# Agentic Data-Analyst — build notes for Claude Code

Portfolio project for Carlos Rubio-Marroquin (data-science track). An agent that
answers natural-language questions about Seattle building-energy data — explores
the schema, writes and runs its own SQL, calls a trained model, draws charts,
self-corrects on errors. Hand-written tool-use loop on the Claude API.

**Separate repo** from the portfolio site and from the Flask/CI-CD project. It
ships its own thin CLI/Flask interface behind a clean `answer_question(q)`
boundary, but nothing here depends on the other projects.

## Status

_M1–M4 merged to `main`. M5 (`m5-tools`) is an open PR. Some prose below predates
the merges; the milestone table is current._

**M5 — `predict` + `make_chart`, complete** (branch `m5-tools`, PR open).

- ✅ `agent/tools.py` — `predict` (loads latest `model-*.joblib`, rejects unknown
  feature keys, coerces numerics / builds the `category`-dtype frame, returns
  `is_high_emitter_proba` + the ROC-AUC-0.76 caveat) and `make_chart` (constrained
  spec → Plotly → `outputs/charts/<run_id>-N.html`). `predict` + `make_chart` are
  **not `strict`** (open feature dict / `anyOf` field — the API rejects those
  under strict; handlers validate every field).
- ✅ `RunContext` threads per-question state through `run_tool` — `run_sql`
  stashes its result so `make_chart` can chart `data="last_query"`.
- ✅ `tool_schemas(source_key)` — `predict` offered only when the source has a
  `model_module` (seattle_energy does).
- ✅ `conftest.py` — autouse `_isolate_source_registry` (every test starts with an
  empty `sources.REGISTRY`; `tool_schemas` registers via `load()`).
- **Live-verified on Haiku:** the predictive question ("what would the model
  expect for a 1965 office…") → `predict` → 0.80 proba, surfaced the caveat; the
  chart question → `run_sql` then `make_chart` with `data="last_query"` → wrote
  the HTML and returned its path. Median questions still take ~3 SQL retries
  (SQLite has no `MEDIAN()`; prompt hint added, Haiku still fiddles it).
- ⚠️ **`model-*.joblib` is gitignored** — `predict` works locally but a fresh
  clone / CI has no model (tests use a toy model). **Decision deferred to M7:**
  commit the joblib, or `train.py` in the Docker build.
- 56 tests green (10 new).

**M4 — agent loop, complete** (merged).

- ✅ `agent/catalog.py` — reads `catalog/<key>.yaml`; `summary_text()` for the
  system prompt.
- ✅ `agent/tools.py` — `list_datasets`, `describe_schema` (catalog + live
  `PRAGMA table_info` + row counts), `run_sql`. **`run_sql` guardrails:**
  `sqlparse` — one statement, must start SELECT/WITH, no DDL / non-SELECT DML /
  PRAGMA / ATTACH; wrap in `SELECT * FROM (...) LIMIT n+1` (honest `truncated`);
  per-connection statement timeout via SQLite progress handler; runs on
  `ro_engine()`; **errors returned structured** (`{error_type, message}`) for the
  self-correction loop.
- ✅ `agent/prompts.py` — one stable system block (role + catalog schema summary
  + rules) with a `cache_control` breakpoint.
- ✅ `agent/trace.py` — per-run `Trace`: turns, tokens, cost (per-model rate
  table), latency → one JSON line to `logs/runs.jsonl`; `.summary()` one-liner.
- ✅ `agent/loop.py` — hand loop. `MAX_AGENT_ITERS` cap; execute *all* `tool_use`
  blocks, return *all* results in one user message; adaptive thinking gated to
  Opus/Sonnet/Fable (Haiku dev path sends none); specific exception chain
  (`NotFoundError`→`RateLimitError`→`APIStatusError`→`APIConnectionError`), one
  rate-limit retry. `python -m agent.loop "question"` prints answer + SQL + trace.
- **Live-verified** on `claude-haiku-4-5`: 4 questions, all correct vs. the M1/M2
  numbers, **$0.06 total**. The `MEDIAN()` question self-corrected (SQLite has no
  MEDIAN) across 3 retries to a window-function query. Simple Qs ≈ 2 turns /
  $0.007; hard Q ≈ 6 turns / $0.04.
- ⚠️ **Prompt caching not engaging** — the ~1.5k-token `tools`+`system` prefix is
  under the cache minimum. Fix at M6: fold the full `describe_schema` output into
  the system prompt (bigger prefix → cacheable, and fewer tool round-trips).
- 46 tests green (16 new; loop tested with a fake client, no API).

**M3 — baseline model, complete** (merged).

- ✅ `model/seattle_energy/features.py` — pure `build_features(raw)` → model
  frame; `temporal_split` (train ≤2022 / valid 2023 / test 2024) **and**
  `building_disjoint_split` (GroupShuffleSplit on `ose_building_id`). Target +
  `is_outlier` (Tukey 3×IQR, computed here, **not** a DB column) + the M2 feature
  list with hygiene rules.
- ✅ `model/seattle_energy/train.py` — HGB (native NaN + categoricals) + LogReg
  baseline. Writes `artifacts/metrics-*.json`, `permutation-importance-*.png`,
  ships `model-*.joblib` (HGB refit on all years; gitignored), regenerates
  `model_card.md` from `model_card.template.md`.
- **Result — two numbers, both honest:** temporal test ROC-AUC **0.857**, but
  that's flattered by recurring buildings (96% of test buildings are also in
  train, efficiency rank is sticky). **Building-disjoint ROC-AUC 0.760** is the
  estimate for a genuinely new building; temporal-new (n=150) is 0.65. LogReg
  baseline ≈0.64 both ways. This **corrects the arch doc §4.4** claim that the
  temporal split "prevents the same building's other years leaking".
- Top features (permutation importance): `building_age` 0.17, `primary_property_type`
  0.16, `log_gfa_total` 0.09, `number_of_floors` 0.07.
- 30 tests green.

**M2 — EDA, complete** (branch `m2-eda`, stacked on `m1-ingestion`; PR open).

- ✅ `notebooks/01_eda.ipynb` — Jupyter, Plotly, static-PNG outputs (GitHub
  strips interactive Plotly). Lints + formats with `ruff` like the rest; a test
  (`test_notebook.py`) guards that it's committed executed and error-free.
  Rebuilt headless (`nbconvert --execute`) before each commit — no hidden state.
- **Findings that change M3:**
  - Target `is_high_emitter` is **50.7 / 49.3** overall and balanced within every
    major property type — the within-type×year normalisation works.
  - The M1 "warehouse ≈ 121" was **not a unit bug** — reported
    `ghg_emissions_intensity` matches `total_ghg_emissions / GFA` within ±10% for
    ~90% of rows (the residual is building-GFA vs. total-GFA as denominator). It's
    a handful of extreme rows. `is_outlier` = Tukey 3×IQR within type×year flags
    **1,032 rows (2.8%)** → exclude from training, keep in the DB.
  - **Signal is weak** — every numeric feature correlation with the target is
    < 0.13 (`log_gfa_total` strongest). M3 model card should target ROC-AUC
    ≈ 0.60–0.68, leaning on `primary_property_type` + non-linear interactions.
  - `energy_star_score` present for **75%** of rows, structured by property type
    (Multifamily 84%, Hotel 96%; Data Center / Self-Storage / Lab ~0–14%) → keep
    with a `has_energy_star` indicator, never impute.
  - `building_age` has negatives (reported pre-completion) → NaN alongside the
    `year_built < 1850` rule. Train/test share 96% of buildings — **not leakage**
    here (relative label, no feature encodes it).

**M1 — ingestion, complete** (branch `m1-ingestion`, PR open).

- ✅ **Dataset verified** against data.seattle.gov: resource id **`teqw-tu6e`**
  ("Building Energy Benchmarking Data, 2015-Present"), 38,309 rows, 3,871
  buildings, `datayear` 2015–2025, 47 source columns. Deltas from the v0.1
  assumed schema: `osebuildingid`/`datayear`/`yearbuilt` are **text** upstream
  (cast in `field_map`); no `primary_property_type` column (use `epapropertytype`,
  ">50% of floor area"); no `outlier_flag` upstream; `steam_kbtu` =
  `steamuse_kbtu`; added `demolished`, `number_of_floors`, `tax_parcel_id`.
- ✅ **SQLite everywhere** (was MySQL local + SQLite prod — switched 2026-09-07
  for simplicity; kills the dialect gap). One file, `SQLITE_PATH`. No server, no
  bootstrap, no DB creds. `db/engine.py` gives `rw_engine()` and `ro_engine()`
  (`PRAGMA query_only`).
- ✅ `sources/seattle_energy.py` (`SourceSpec` + `FIELD_MAP`, 34 mappings) +
  `catalog/seattle_energy.yaml` (curated column docs for `describe_schema`).
  `FieldMap` values can be a tuple → one source column to several tables
  (`osebuildingid` → `buildings` + `energy_records`).
- ✅ `db/migrate.py` (schema + numbered migrations, idempotent) and
  `ingest/run.py` (`python -m ingest.run --source seattle_energy [--full-refresh]
  [--since YEAR] [--limit N]`). **Load verified**: 38,309 / 3,871 / 2015–2025 all
  match the portal; compliance split 35,796 / 2,513 matches; FK integrity clean;
  `ingestion_runs` row `success`. 21 tests green.
- **Repo:** https://github.com/CarlosRM25/analytics-AI-Agent-  (`main`; M1 → `m1-ingestion`, M2 → `m2-eda`)
- **Architecture doc:** `analytics-agent-architecture.md` — lives with the
  portfolio planning docs (`../portfolio/Claude outputs/`), **follow it.**
  §11 milestone table; §12 open decisions. Amended 2026-09-07: SQLite everywhere.

## Stack & conventions

- **Python 3.11+** (3.13 locally). Dependencies in `requirements.txt`, unpinned
  until M4; `pyproject.toml` holds only ruff + pytest config, not packaging.
- **`ruff`** (line length 100, rules `E F I UP B SIM`; `ruff format` too) +
  **`pytest`**. Keep green from every commit. `tests/` run with no API key and no
  network — DB tests use a temp SQLite file via the `sqlite_env` fixture.
- **Config:** never instantiate `Settings()` at import time — entry points call
  `config.get_settings()` so a bad env fails fast at startup, not on import.
  Local mode requires nothing (SQLite has no creds); deployed requires
  `ANTHROPIC_API_KEY` + `REDIS_URL` + `CORS_ALLOWED_ORIGIN`.
- **Storage:** SQLite everywhere, one file. `ingest`/`migrate` use `rw_engine()`;
  the agent uses `ro_engine()` only (`PRAGMA query_only = ON`). SQLite has window
  functions + CTEs; the agent is told its dialect in the system prompt.
- **Datasets are plug-ins:** a new civic dataset = one `sources/<key>.py` that
  builds a `SourceSpec` and calls `register()`, one `catalog/<key>.yaml`, and
  optionally one `model/<key>/`. No changes to ingestion, storage, agent, or app.
- **Model knob:** `ANALYST_MODEL` env var — `claude-opus-5` for local use,
  `claude-haiku-4-5` for the eval loop and the public demo.
- Secrets only in `.env` (gitignored). Never put a secret in a prompt.

## Working style

Carlos does architecture/planning in the main chat and building in focused
sessions (this repo is one). Each milestone is its own session; stop at the
milestone boundary and report rather than rolling ahead.

## Security posture (the interview talking point — architecture doc §8)

The agent's DB connection is **physically read-only** — `ro_engine()` sets
`PRAGMA query_only = ON`, so any write raises `SQLITE_READONLY` from SQLite
itself, and the agent module never imports `rw_engine()`. On top: single-statement
`SELECT`/`WITH` allowlist via `sqlparse`, auto-`LIMIT`, statement timeout. The
model sees the curated `catalog/*.yaml`, never raw schema internals. User input
reaches the model only as a user message and is never string-formatted into SQL
by our code. *(This replaces the two-DB-user least-privilege control from the
MySQL design — §8.8 anticipated the swap.)*

## Milestones

| # | Milestone | Done when |
|---|---|---|
| **M0** | Scaffold | ✅ structure, config, tooling, one passing test — `pytest`/`ruff` green |
| **M1** | Source spec + ingest | ✅ `teqw-tu6e` verified; `sources/seattle_energy.py` + catalog; `db/migrate.py` + `ingest/run.py`; 38,309 rows loaded to SQLite, counts match portal; `ingestion_runs` populated |
| **M2** | EDA notebook | ✅ `notebooks/01_eda.ipynb` — target balance, missingness, outlier rule, weak-signal finding, leakage check (`m2-eda`) |
| **M3** | Baseline model | ✅ `features.py` + `train.py`; HGB + LogReg baseline; temporal **and** building-disjoint splits; metrics/importance artifacts; `model_card.md` (`m3-model`) |
| **M4** | Agent loop | ✅ `agent/` — `list_datasets`/`describe_schema`/`run_sql` (guardrails, `ro_engine`); hand loop with `MAX_ITERS`; `trace.py` → `runs.jsonl`; live-verified on Haiku, self-correcting SQL |
| **M5** | `predict` + `make_chart` | ✅ both tools; `predict` gated on `model_module`, `make_chart` charts `data="last_query"` via `RunContext`; live-verified — agent picks the right tool per question |
| M6 | Interface + evals | `cli.py`, `POST /ask`; `evals/questions.yaml` (12-15) + `run.py` reporting pass rate / iterations / cost |
| M7 | Containerize + deploy | `deploy/Dockerfile` (Flask + agent + SQLite `mode=ro`); Cloud Run; `/ask` + `/health`; `claude-haiku-4-5`; charts inline |
| M8 | Public-demo hardening | `frontend/` offline gallery live; rate limits + response cache (Redis); `DEMO_ENABLED` + budget cutoff; CORS locked; alerts |
| M9 | Expansion (stretch) | *either* a second `SourceSpec` (SPD crime `tazs-3rd5`, or Metro transit) *or* the `permits` join — not both |

## Open decisions (architecture doc §12 — defaults chosen, Carlos can change)

- **Prediction target:** classification (`is_high_emitter`) first; regression on
  `ghg_emissions_intensity` is the v1.1 stretch.
- **M9 direction:** default = a second `SourceSpec` over the permits join.
- **Deploy host:** Cloud Run assumed (scale-to-zero); Fly.io / paid Render if
  cold starts annoy. Redis (Upstash) vs. Firestore for counters/cache still open.
  Cap numbers (5 / 300 / $15) are starting points.
- **Frontend integration:** the chat UI is stack-agnostic static files; where it
  slots into the portfolio site (Astro/Vercel) is decided when M8 starts — it
  gets a `/demo` page there.
