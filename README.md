# Gogo

![Gogo — go / no-go surf windows](img/header-banner.png)

Go / no-go surf windows for the **Lisbon–Ericeira–Peniche** coast.

Not a generated surf report and not another Surfline. The unit is a **time window**: where to go, when, and why — for you.

```text
Saturday 07:00–10:00 · Ribeira d'Ilhas · 78
offshore, incoming mid, 1.3 m @ 11 s NW
```

That is what the page shows: a ranked list of **time ranges** for any day in the next
week, one per spot, each with the hour it peaks — and a card that asks how it actually
was, without showing you what we predicted first.

## Status

**Phase 2 — store, fetch, windows.** Forecasts persist and are served as ranges. Labels are the bottleneck.

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
| `gogo weekend --db` / `GET /api/windows` | done (read stored rows) |
| Windows as time ranges (score v2) | done |
| Any day, not just Saturday — `/api/windows?day=` | done |
| Mobile page (React + TS): windows + blind post-session card | done |
| `gogo worker` — fetch loop with backoff + coverage check | done |
| `gogo health` — freshness + coverage, exit 1 when the box is not working | done |
| Bounded tables — changed-payload snapshots, pruned `forecast_current` | done |
| `gogo demo` — quarantined fixture labels for harness work | done |
| A host to run the worker on, container image, backups | image + dump ready; you provision the VPS |
| ~100 observations — the Stage 1 gate | **not yet** |
| Accounts and invite-only groups, deploy | later |

Thesis [`surfreporter`](https://github.com/MaximilianLae/surfreporter) is reference only.

Where this is going: [`docs/plan.md`](docs/plan.md). Short version — locals' post-session
feedback becomes training signal for the score, not a social feed, and no score change
lands without a backtest number.

## How it works

```text
gogo fetch          → Open-Meteo → snapshots + current (worker writes)
gogo worker         → the same on a loop; snapshot history is unrecoverable
gogo health         → is the forecast fresh and is every spot still ranked?
gogo backfill       → ERA5 archive → snapshots only, is_analysis
gogo weekend --db   → read current → group Saturday into ranges → print
GET /api/windows    → same as --db, JSON (cookie-gated)
```

`backfill` exists so past sessions can be labelled without waiting for new ones. It never
writes `forecast_current`: reanalysis is what happened, and serving it would make the
score look clairvoyant.

The API does **not** call Open-Meteo. If current is empty, `/api/windows` returns 503 until you `fetch`.

## Stack

| Piece | Choice | Why |
|---|---|---|
| Language | Python 3.12 | Score and domain are the hard part |
| API | FastAPI | `/health` open, `/api/*` behind the cookie |
| Store | Postgres 16 | Snapshots + current row + later session log |
| Forecast | Open-Meteo Marine + Forecast | Hourly swell, wind-sea, wind, sea-level |
| Local engine | OrbStack | Runs the same `docker compose` file |
| UI | Vite + React + TypeScript | Phone list + detail — the primary surface |
| Alerts (later) | Web push + email | The evening go / no-go |
| Host | Hetzner/OVH VPS (or Fly) | One box, two processes. Not EKS |

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
make api                # http://127.0.0.1:8000/api/windows
make ui                 # build the page (needs Node; once, then when the UI changes)
make web                # serve it, key "devkey"
make phone              # same, reachable from your phone on this wifi
make down               # stop Postgres; volume (data) stays
```

## Host (VPS)

One image, two processes, one Postgres. The worker is what you turn on first — snapshot
history is unrecoverable. The page is optional (`--profile web`). Not EKS.

On a small Debian box (Hetzner CX22 is enough):

1. Create the VPS, SSH in, install Docker (and git). Do not open port 5432.
2. Clone the repo. Copy `.env.example` to `.env` and set **only** on the host:

   ```bash
   openssl rand -hex 32   # POSTGRES_PASSWORD
   openssl rand -hex 32   # GOGO_WEB_SECRET, only if you bring the page up
   ```

   Hex on purpose: it is safe inside `DATABASE_URL`. Never commit `.env`.
3. `make host` (or `docker compose -f docker-compose.prod.yml up -d --build`).
   That is Postgres + the hourly fetch. `make host-web` also serves the page on
   `127.0.0.1:8000` — put Caddy in front, or `ssh -L 8000:127.0.0.1:8000`.
4. Cron the dump: `15 3 * * * /path/to/gogo/scripts/backup.sh`  
   Files land in `backups/` (gitignored, mode 600), last 14 days kept.

An unset `GOGO_WEB_SECRET` still means the page is **off**, not open. The worker does
not need it.

```bash
make host               # Postgres + worker
make host-web           # same, plus the page on 127.0.0.1:8000
make backup             # pg_dump to backups/
```

Is it working? Both failure modes here are silent — a stopped worker still leaves a page
that renders, and a spot that drops out of the ranking is just one fewer row.

```bash
docker compose -f docker-compose.prod.yml exec worker gogo health
```

That is also the worker's container healthcheck, so `docker compose ps` says `unhealthy`
when the forecast stops moving. Docker will not restart it on that, deliberately: the
loop is built to ride out a bad hour at Open-Meteo rather than exit on one.

The page is where labels come from, so it is the way to use this. `make phone` prints a
LAN address and a key — open it on your phone and add it to the home screen. An unset
`GOGO_WEB_SECRET` means the page is **off** rather than open, so set a real one anywhere
that is not your laptop.

The frontend is Vite + React + TypeScript in `web/`, built into the Python package and
served same-origin, so the cookie is the only auth and there is no CORS. `make install`
stays Python-only — the score, the worker and the tests never need Node.

```bash
make ui-dev             # Vite on :5173 with `make web` behind it, hot reload
make ui-types           # regenerate client types from FastAPI's schema; CI diffs this
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

### Fixture labels, and why they are fenced off

The evaluation harness cannot be written against an empty table, so `gogo demo` invents
sessions from stored reanalysis — real spots, real days, same-day pairs, three raters.

```bash
gogo demo --days 40     # write fixture labels
gogo demo --purge       # remove every one of them; real labels untouched
```

Every row carries `is_synthetic`, and `load_observations` and `count_observations`
exclude it by default. This matters more than it sounds: the ratings are derived from our
own score, so **any metric that counts them is measuring our own assumptions** and will
come back flattering with nothing to reveal the error. A column rather than a naming
convention, because a filter someone forgot is exactly how that happens.

The synthetic rater has an opinion we wrote down — it likes size more than the score
does, and it is noisy. A harness that reports perfect agreement with it has a bug, since
it is supposed to detect exactly that disagreement. Feeding a measuring device a known
quantity is the only way to trust its reading before pointing it at real labels.

Fixture labels can validate the harness. They can never say whether the score is any
good; only real ones can do that.

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
src/gogo/worker.py           # fetch_once, run_forever, backfill
src/gogo/demo.py             # FIXTURE labels for harness work, never real data
src/gogo/importer.py         # CSV of remembered sessions → labels
src/gogo/cli.py              # gogo weekend | fetch | backfill | migrate | log | import
src/gogo/serving.py          # pick a day, score it, record what we showed
src/gogo/api.py              # /health + /api: days, windows?day=, spots, observations
src/gogo/schemas.py          # the wire format; web/src/api/schema.d.ts is generated from it
src/gogo/web.py              # the login form and the app shell; templates/ + static/ ship
web/                         # Vite + React + TS → built into src/gogo/static/app
src/gogo/clock.py            # UTC inside, Lisbon at the edges
src/gogo/versioning.py       # spec_version for a spot
src/gogo/migrate.py          # numbered SQL, schema_migrations
src/gogo/migrations/         # applied by gogo migrate, never by Postgres
Dockerfile                   # worker + api, secrets from env at run time
docker-compose.prod.yml      # VPS stack; local `make up` still uses docker-compose.yml
scripts/backup.sh            # pg_dump from inside Postgres, no password on argv
uv.lock                      # pinned Python deps; make install / CI use this
.github/workflows/test.yml   # pytest + compose Postgres; typecheck, build, type drift
tests/                       # ranks, ingest mocks, store roundtrip
```

How to change the product: edit the spot file or the score, add a fixture in `tests/test_score.py`, run `make test`. If a rank needs a paragraph of justification, the score is not done.

## Forecast notes

- Marine: `cell_selection=sea`, `best_match`, `timezone=Europe/Lisbon`, `timeformat=unixtime`, `forecast_days=7`
- Timestamps are timezone-aware **UTC** everywhere inside; Lisbon is rendered at the edges. `unixtime` avoids the ambiguous local hour on the autumn DST fold
- Wind: weather API, knots, land cell
- Tide is a **phase** from `sea_level_height_msl`, not a Hidrográfico table
- Six fields are **stored and not scored**: peak period, the combined sea (height and
  period), and a secondary swell train. A forecast is the one thing the archive cannot
  give back, so they are kept from today and left out of the score until the harness can
  say whether they read the water better. Measured before adding them: peak period runs a
  median 1.5 s above the mean period the score gates on, and the size gate disagrees with
  the combined sea on 14% of hours — always in the same direction, vetoing as too small a
  spot whose actual sea is in range
- Peak period has a **shorter horizon than everything else**, about 69 h against 145 h.
  A null there usually means "too far out", not "flat", and anything gating on it needs a
  fallback to the mean period past day three
- Nearby spots often share one wave-model cell. Ranking between two Ericeira reefs on the same hour comes from the spot file

Free Open-Meteo is **non-commercial**, CC BY 4.0. Attribution is required. If this ever has ads or a paid plan, use their customer endpoint.

## Attribution

Forecasts from [Open-Meteo](https://open-meteo.com/) (CC BY 4.0), using national wave and weather models (Météo-France, DWD, ECMWF, NCEP, and others).
