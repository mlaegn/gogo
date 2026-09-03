# Gogo

![Gogo — go / no-go surf windows](img/header-banner.png)

Go / no-go surf windows for the **Lisbon–Ericeira–Peniche** coast.

Not a generated surf report and not another Surfline. The unit is a **time window**: where to go, when, and why — for you.

```text
Saturday 07:00–10:00 · Ribeira d'Ilhas · 78
offshore, incoming mid, 1.3 m @ 11 s NW
```

The API already returns a ranked list (today: **one hour**, Saturday 08:00). Grouping hours into ranges like the example above is next, then the phone page — the UI is how this is meant to be used.

## Status

**Phase 2 — store + fetch.** Forecasts persist. The score is unchanged.

| Piece | State |
|---|---|
| 16 spots in `src/gogo/data/coast.yml` | done |
| Deterministic score + tests | done |
| Open-Meteo marine + wind | done |
| Postgres (`spots`, `spot_grid`, `forecast_snapshots`, `forecast_current`) | done |
| `gogo migrate` — numbered SQL, `schema_migrations` | done |
| Versioned score + spot specs | done |
| `window_impressions` — what we told you, append-only | done |
| `gogo log` — observations, residual, fault codes | done |
| `gogo import` — bulk CSV of remembered sessions | done |
| `gogo fetch` | done (one-shot) |
| `gogo backfill` — ERA5 reanalysis, never served | done |
| `gogo weekend --db` / `GET /windows` | done (read stored rows) |
| Hourly worker process | **not yet** |
| Windows as time ranges | **not yet** |
| UI, accounts, session log, deploy | later |

Thesis [`surfreporter`](https://github.com/MaximilianLae/surfreporter) is reference only.

Where this is going: [`docs/plan.md`](docs/plan.md). Short version — locals' post-session
feedback becomes training signal for the score, not a social feed, and no score change
lands without a backtest number.

## How it works

```text
gogo fetch          → Open-Meteo → snapshots + current (worker writes)
gogo backfill       → ERA5 archive → snapshots only, is_analysis
gogo weekend --db   → read current → score Saturday 08:00 → print
GET /windows        → same as --db, JSON
```

`backfill` exists so past sessions can be labelled without waiting for new ones. It never
writes `forecast_current`: reanalysis is what happened, and serving it would make the
score look clairvoyant.

The API does **not** call Open-Meteo. If current is empty, `/windows` returns 503 until you `fetch`.

## Stack

| Piece | Choice | Why |
|---|---|---|
| Language | Python 3.12 | Score and domain are the hard part |
| API | FastAPI | `/health`, `/windows` |
| Store | Postgres 16 | Snapshots + current row + later session log |
| Forecast | Open-Meteo Marine + Forecast | Hourly swell, wind-sea, wind, sea-level |
| Local engine | OrbStack | Runs the same `docker compose` file |
| UI | Vite + React + TypeScript | Phone list + detail — the primary surface |
| Alerts (later) | Web push + email | The evening go / no-go |
| Host (later) | Fly.io or a VPS | Not a personal EKS cluster |

One git repo. `api` and `worker` are processes, not extra repositories.

## Run locally

Needs [uv](https://docs.astral.sh/uv/) and [OrbStack](https://orbstack.dev) (for Postgres).

```bash
make install
make up                 # Postgres; waits until healthy, then gogo migrate
make test
make weekend            # fixture, no network
make fetch              # Open-Meteo → Postgres
make weekend-db         # score stored rows
make api                # http://127.0.0.1:8000/windows
make down               # stop Postgres; volume (data) stays
```

Recording what you saw, which is what the score gets calibrated against:

```bash
gogo log ribeira --start 07:15 --end 09:00 --residual -1 --fault tide:-1 --crowd busy
gogo log coxos --start 08:00 --end 08:30 --kind checked --residual -2
```

`--residual` is how it compared to what we predicted, −2 much worse to +2 much better.
`--fault` names the gate we got wrong. `--kind checked` is looked-at-and-did-not-surf —
the only trace a wrongly-vetoed spot ever leaves.

To label a session from *before* the forecast was being stored, pull the reanalysis for
those days first — then the hours have features to be judged against:

```bash
gogo backfill --from 2026-08-01 --to 2026-08-31
```

Use `--rating` rather than `--residual` for those: nothing was predicted to you at the
time, so there is no residual to give. Swell and wind go back to at least 2022, tide only
to late 2022.

A season's worth at once, from a CSV — `date,spot,start,end` required, the rest optional:

```csv
date,spot,start,end,kind,rating,crowd,faults,note
2026-03-14,ribeira,07:15,09:00,surfed,4,ok,,clean lines on the point
2026-03-14,coxos,09:15,09:30,checked,2,busy,size:-1,smaller than it should have been
```

```bash
gogo import sessions.csv --dry-run   # check it, write nothing
gogo import sessions.csv
```

Rows land unanchored, since no score was visible to you at the time. Re-running an edited
file corrects it rather than duplicating it. The output counts **same-day spot pairs**,
not just rows — ranking accuracy compares two spots on one day, so fifty one-spot days
are fifty labels and no pairs. That is what the `checked` rows are for.

If your database predates `gogo migrate`, baseline it once: `gogo migrate --baseline 001_init.sql`.

Copy `.env.example` to `.env` if you change the database URL. Defaults match compose (`gogo` / `gogo` / `gogo` on `localhost:5432`).

## Layout

```text
src/gogo/data/coast.yml      # curated gates — edit here first
src/gogo/score.py            # pure, tested
src/gogo/ingest/openmeteo.py # the only forecast source for v1
src/gogo/ingest/archive.py   # ERA5 reanalysis — past hours, never served
src/gogo/store.py            # seed, persist, load current
src/gogo/worker.py           # fetch_once, backfill (loop comes next)
src/gogo/importer.py         # CSV of remembered sessions → labels
src/gogo/cli.py              # gogo weekend | fetch | backfill | migrate | log | import
src/gogo/api.py              # GET /health, GET /windows
src/gogo/clock.py            # UTC inside, Lisbon at the edges
src/gogo/versioning.py       # spec_version for a spot
src/gogo/migrate.py          # numbered SQL, schema_migrations
src/gogo/migrations/         # applied by gogo migrate, never by Postgres
uv.lock                      # pinned Python deps; make install / CI use this
.github/workflows/test.yml   # pytest + compose Postgres
tests/                       # ranks, ingest mocks, store roundtrip
```

How to change the product: edit the spot file or the score, add a fixture in `tests/test_score.py`, run `make test`. If a rank needs a paragraph of justification, the score is not done.

## Forecast notes

- Marine: `cell_selection=sea`, `best_match`, `timezone=Europe/Lisbon`, `timeformat=unixtime`, `forecast_days=7`
- Timestamps are timezone-aware **UTC** everywhere inside; Lisbon is rendered at the edges. `unixtime` avoids the ambiguous local hour on the autumn DST fold
- Wind: weather API, knots, land cell
- Tide is a **phase** from `sea_level_height_msl`, not a Hidrográfico table
- Nearby spots often share one wave-model cell. Ranking between two Ericeira reefs on the same hour comes from the spot file

Free Open-Meteo is **non-commercial**, CC BY 4.0. Attribution is required. If this ever has ads or a paid plan, use their customer endpoint.

## Attribution

Forecasts from [Open-Meteo](https://open-meteo.com/) (CC BY 4.0), using national wave and weather models (Météo-France, DWD, ECMWF, NCEP, and others).
