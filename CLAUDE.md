# Agentic Data-Analyst — build notes for Claude Code

Portfolio project for Carlos Rubio-Marroquin (data-science track). An agent that
answers natural-language questions about Seattle building-energy data — explores
the schema, writes and runs its own SQL, calls a trained model, draws charts,
self-corrects on errors. Hand-written tool-use loop on the Claude API.

**Separate repo** from the portfolio site and from the Flask/CI-CD project. It
ships its own thin CLI/Flask interface behind a clean `answer_question(q)`
boundary, but nothing here depends on the other projects.

## Status

_M1 merged to `main`. M2 (`m2-eda`) and M3 (`m3-model`) are open PRs — the M1/M2
detail below predates those merges; treat the milestone table as current._

**M3 — baseline model, complete** (branch `m3-model`, PR open).

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
- ⚠️ **For M2 EDA:** `ghg_emissions_intensity` has property-type outliers — e.g.
  "Non-Refrigerated Warehouse" avg ≈ 121 vs. single digits elsewhere (likely a
  unit inconsistency upstream). This is why we need our own derived outlier flag.

- **Repo:** https://github.com/CarlosRM25/analytics-AI-Agent-  (`main`; M1 on `m1-ingestion`)
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
| M4 | Agent loop | `describe_schema` + `run_sql` (guardrails, `ro_engine`); hand loop with `MAX_ITERS`; `trace.py` → `runs.jsonl` |
| M5 | `predict` + `make_chart` | both tools registered; agent picks the right one per question |
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
