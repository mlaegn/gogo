# Gogo

Personal surf go / no-go planner. Lisbon–Ericeira–Peniche only.

Thesis `surfreporter` is reference, not a dependency. Do not copy Pinecone, Streamlit, RAG, or IPMA-as-primary.

## Frozen decisions

- **One repo.** `api` and `worker` are processes, not repositories. A new forecast source is `src/gogo/ingest/<name>.py` behind `ForecastSource`.
- **Open-Meteo only for v1.** Marine + weather forecast. `cell_selection=sea` on marine. `best_match`. Land cell for wind. Batch unique grid points. `forecast_days=7`, `timezone=Europe/Lisbon`.
- **Tide** from `sea_level_height_msl` is a phase (low/mid/high), not a navigation table. Compare to Hidrográfico before trusting the tide term.
- **Score decides.** LLM is not in the path. Reasons must explain a rank in one sentence.
- **Local engine is OrbStack**, not Docker Desktop. Same `docker compose` file.
- **Worker writes, API reads.** `/windows` must not call Open-Meteo.
- **No EKS, Redis, Kafka, ClickHouse, Pinecone, Next.js, Auth0.**

## Layout

```text
spots/coast.yml          # the content — edit here first
src/gogo/score.py        # pure, tested
src/gogo/ingest/         # Open-Meteo adapter
migrations/              # SQL you run on purpose
tests/fixtures/          # golden weekends
```

## How to work

1. Change the spot file or the score.
2. Add or adjust a fixture in `tests/test_score.py`.
3. `make test`. If a rank needs a paragraph of justification, the score is not done.

Do not add a CSS framework, a vector DB, or an LLM while the score is still being argued.
