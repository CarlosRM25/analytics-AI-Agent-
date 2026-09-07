# Agentic Data-Analyst — build notes for Claude Code

Portfolio project for Carlos Rubio-Marroquin (data-science track). An agent that
answers natural-language questions about Seattle building-energy data — explores
the schema, writes and runs its own SQL, calls a trained model, draws charts,
self-corrects on errors. Hand-written tool-use loop on the Claude API.

**Separate repo** from the portfolio site and from the Flask/CI-CD project. It
ships its own thin CLI/Flask interface behind a clean `answer_question(q)`
boundary, but nothing here depends on the other projects.

## Status

**M0 — scaffold shipped.** Repo structure per §6 of the architecture doc,
`config.py` (pydantic-settings, all §9 vars, `DEPLOY_MODE` local/deployed
subsets), `.env.example`, `SourceSpec` + empty `REGISTRY` in `sources/base.py`,
README with the §3 diagram, smoke tests. `ruff check .` and `pytest` are green.
No feature logic yet.

**M1 in progress** (branch `m1-ingestion`) — source spec + ingestion.

- ✅ **Dataset verified** against data.seattle.gov: resource id **`teqw-tu6e`**
  ("Building Energy Benchmarking Data, 2015-Present"), ~38,309 rows, 3,871
  buildings, `datayear` 2015–2025. All 47 columns + types captured. Deltas from
  the v0.1 assumed schema: source fields `osebuildingid`/`datayear`/`yearbuilt`
  are **text** upstream (cast in `field_map`); no `primary_property_type` column
  (use `epapropertytype`); no `outlier_flag` upstream (dropped for M1, revisit in
  M2); `steam_kbtu` = `steamuse_kbtu`; added `demolished`, `number_of_floors`,
  `tax_parcel_id`. `db/schema.sql` reflects all of this.
- ✅ **MySQL 8.0** already installed + running here (`MySQL80` service). Bootstrap
  script at `db/bootstrap.sql` — **Carlos runs it once as root**, sets the two
  user passwords, mirrors them into `.env`.
- ⬜ Build `sources/seattle_energy.py` (`SourceSpec` + `field_map`),
  `catalog/seattle_energy.yaml`, `db/migrate.py`, `ingest/run.py`. Then load and
  check row counts / year range against the portal; `ingestion_runs` populated.
- ⬜ Optional: register a free Socrata app token (lifts the anon rate limit).

- **Repo:** https://github.com/CarlosRM25/analytics-AI-Agent-  (`main`; M1 on `m1-ingestion`)
- **Architecture doc:** `analytics-agent-architecture.md` — lives with the
  portfolio planning docs (`../portfolio/Claude outputs/`), v1.1, **follow it.**
  §11 has the milestone table; §12 has the open decisions.

## Stack & conventions

- **Python 3.11+** (3.13 locally). Dependencies in `requirements.txt`, unpinned
  until M4; `pyproject.toml` holds only ruff + pytest config, not packaging.
- **`ruff`** (line length 100, rules `E F I UP B SIM`) + **`pytest`**. Keep both
  green from every commit. `tests/` must run with no API key and no database.
- **Config:** never instantiate `Settings()` at import time — entry points call
  `config.get_settings()` so a bad env fails fast at startup, not on import.
- **SQL dialect:** MySQL 8 locally, SQLite (baked, read-only) in the deployed
  demo. Keep queries ANSI-ish; the agent is told its dialect in the system prompt.
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

The agent connects as a **`SELECT`-only** MySQL user — it *cannot* write, by
construction. On top: single-statement `SELECT`/`WITH` allowlist via `sqlparse`,
auto-`LIMIT`, statement timeout. The model sees a curated catalog, never raw
`information_schema`. User input reaches the model only as a user message and is
never string-formatted into SQL by our code.

## Milestones

| # | Milestone | Done when |
|---|---|---|
| **M0** | Scaffold | ✅ structure, config, tooling, one passing test — `pytest`/`ruff` green |
| M1 | Source spec + ingest | `SourceSpec` for energy; **resource_id + fields confirmed**; `ingest/run.py` loads MySQL; `ingestion_runs` populated |
| M2 | EDA notebook | distributions, missingness (esp. `energy_star_score`), age-vs-EUI, type-vs-emissions |
| M3 | Baseline model | `features.py` + `train.py`; `is_high_emitter` classifier; 2022/23/24 split; metrics logged; `model_card.md` |
| M4 | Agent loop | `describe_schema` + `run_sql` (guardrails, RO user); hand loop with `MAX_ITERS`; `trace.py` → `runs.jsonl` |
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
