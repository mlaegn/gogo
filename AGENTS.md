# Gogo

Personal surf go / no-go planner. Lisbon–Ericeira–Peniche only.

Thesis `surfreporter` is reference, not a dependency. Do not copy Pinecone, Streamlit, RAG, or IPMA-as-primary.

`docs/plan.md` is the current contract. Read it before starting work: it says which slice is next and why. Community input is training signal for the score, not a feed.

## Frozen decisions

- **One repo.** `api` and `worker` are processes, not repositories. A new forecast source is `src/gogo/ingest/<name>.py` behind `ForecastSource`.
- **Open-Meteo only for v1.** Marine + weather forecast. `cell_selection=sea` on marine. `best_match`. Land cell for wind. Batch unique grid points. `forecast_days=7`, `timezone=Europe/Lisbon`, `timeformat=unixtime`.
- **UTC inside, Lisbon at the edges.** Every stored and passed timestamp is timezone-aware UTC. Local time exists only in CLI output, API responses, and later the UI. "Saturday 08:00" is a *local* concept and must be resolved in `Europe/Lisbon`.
- **Tide** from `sea_level_height_msl` is a phase (low/mid/high), not a navigation table. Compare to Hidrográfico before trusting the tide term. S12 replaces it with height + rate.
- **Score decides.** LLM is not in the path. Reasons must explain a rank in one sentence.
- **`forecast_current` is for serving. Evaluation reads `forecast_snapshots` at an as-of.** Backtesting against current leaks hindsight, because current is overwritten by later runs.
- **Local engine is OrbStack**, not Docker Desktop. Same `docker compose` file.
- **Worker writes, API reads.** `/windows` must not call Open-Meteo.
- **No EKS, Redis, Kafka, ClickHouse, Pinecone, Next.js, Auth0.** Identity is an app account (magic link or OAuth); invite-only is the anti-spam design. No Telegram client — the UI is the surface people check.
- **Ratings pool globally, interpretation can be private.** Observations are anonymous global training signal. Group-scoped spec *overlays* (personal → group → base) are how private local knowledge works. Never fragment the label pool.
- **CI** runs `pytest` against compose Postgres on GitHub Actions. Do not skip store tests locally if Postgres is up.
- **Deps** come from `uv.lock`. `make install` is `uv sync`. After changing `pyproject.toml`, run `uv lock` and commit the lockfile. `numpy`/`scipy` belong to the `eval` group only — never to the API or worker runtime.

## Layout

```text
docs/plan.md             # the contract — which slice is next
spots/coast.yml          # the content — edit here first
src/gogo/score.py        # pure, tested
src/gogo/ingest/         # Open-Meteo adapter
src/gogo/store.py        # Postgres
src/gogo/worker.py       # fetch_once; loop is next
migrations/              # SQL applied on first postgres start
tests/fixtures/          # golden weekends
```

## How to work

1. Change the spot file or the score.
2. Add or adjust a fixture in `tests/test_score.py`.
3. `make test`. If a rank needs a paragraph of justification, the score is not done.

Do not add a CSS framework, a vector DB, or an LLM while the score is still being argued. Do not add a UI until `/windows` returns time ranges, not a single Saturday 08:00.

**No score improvements before the evaluation harness exists** (`docs/plan.md`, Stage 2 gate). A weight change without a backtest number is an opinion.
