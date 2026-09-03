# Revision plan

Agreed 2026-09-02. This document is the contract for the next four stages.
When a slice lands, tick it here. When reality disagrees with this file, edit this file.

## Why

Community input is **training signal, not content**. There is no feed, no follower graph,
no posts. A local answers one question after a session and the score gets better at
ranking spots. `forecast_snapshots` joined to human observations on (spot, hour) is a
supervised dataset — that is the product, and nothing else in surf forecasting has it.

Two layers, and they scope differently:

- **Observations** (a session rating) **pool globally and anonymously.** They are
  statistical fuel, not directions to a spot. Splitting them across groups starves the
  model — 20 labels per spot was already the floor.
- **Interpretation** (what a spot needs, notes, hazards) **can be group-scoped**, because
  publishing local knowledge is socially costly and people otherwise won't do it.

## The rule

**No score improvements until the harness exists.** Improving the score feels productive
in a way that building a metric does not. Stage 3 does not start before Stage 2's gate.

## Two questions, kept apart

| | Question | Features from | Answers |
|---|---|---|---|
| **Q1** | Is the *score* right? | best known estimate of the hour (last forecast, or reanalysis) | change the score or the spot spec |
| **Q2** | Is the *recommendation* right? | only what was known at decision time | change lead time, show uncertainty, use an ensemble |

Never evaluate against `forecast_current` — it is upserted by later runs, so a past hour
holds a near-nowcast. Using it silently answers an easier question than the product asks.
`forecast_current` is for serving. Evaluation reads `forecast_snapshots` at an *as-of*.

---

## Stage 0 — foundations

Everything here gets more expensive the longer we wait, because it changes stored history.

- [x] **S1 · Timestamp discipline.** `timeformat=unixtime` on both Open-Meteo calls; aware
  UTC end to end via `clock.py`; Lisbon rendered only at the edges.
  `timezone=Europe/Lisbon` stays in the request so `forecast_days` keeps local day
  boundaries. No migration needed — `TIMESTAMPTZ` was already right and the fold bug was
  in Python. *Tests:* `test_clock.py`, plus a store roundtrip proving the two local 01:00s
  on 25 Oct 2026 stay distinct rows.
- [x] **S2 · Versioning.** `SCORE_VERSION` in `score.py`, bumped by hand; `spec_version`
  per spot from a canonical hash in `versioning.py`, stored on `spots`, returned per
  window with `score_version` on the envelope. Cosmetics (`name`) and identity (`id`) are
  excluded, tide order is normalised, floats are pinned by the declared types.
  *Tests:* `test_versioning.py`, including a pinned digest as the canary.
- [x] **S3 · Observations.** `sessions` replaced by `users` / `observations` /
  `observation_faults`: interval not instant, user id, residual −2..+2, fault codes drawn
  from the score's own reason codes, `kind` in surfed|checked|cam, `anchored` flag, lag
  from `reported_at − ended_at`, and a **`scope`** column written `global`.
  `gogo log <spot> --start 07:15 --end 09:00 --residual -1 --fault tide:-1`.
  `POST /observations` deferred to S5b — an unauthenticated write endpoint is a spam hole.
  *Tests:* `test_observations.py`, including one asserting `FaultCode` is exactly the set
  of reason codes `score.py` emits.
- [x] **S4 · Impressions.** `window_impressions`, written on `/windows` and
  `weekend --db`, stamped with `as_of`, `surface`, rank, reasons, `score_version`,
  `spec_version`. Append-only, never recomputed. Audit log, not eval output.
  *Tests:* `test_impressions.py`.
- [x] **S4b · Migration runner.** `gogo migrate` + `schema_migrations`. Postgres no longer
  self-applies SQL: the initdb mount is gone, so a fresh database and an existing one
  take the same path. `--baseline 001_init.sql` records files up to that one as applied
  without running them, then applies the rest — used once, on the pre-existing local
  database. No destructive re-init was needed after all.

- [x] **S4c · Test isolation.** `tests/conftest.py` builds `gogo_test` from the migration
  files, redirects `DATABASE_URL`, and truncates between tests. Added the moment the
  suite began writing labels rather than only forecast rows: the Stage 2 backtest reads
  `observations`, where a fabricated session is indistinguishable from a real one.
  `test_migrate.py` proves the schema builds from an empty database, which the baselined
  local database never did.
- [x] **S4d · Package data.** `coast.yml` and the migrations moved under `src/gogo/`, so
  they ship in the wheel and resolve relative to `__file__`. Both had been found by
  walking up to `parents[2]`, which is the repo root from a checkout and nothing at all
  from `site-packages` — the first container would have started with no spots and no
  migrations. *Tests:* `test_packaging.py` pins the paths inside the package.

**Gate:** an observation can be logged in under 15 s, and every recommendation ever shown
is recorded with its inputs. **Met**, verified end to end: a logged session pairs with the
impression that predicted it, stamped with the score and spec versions behind it.

## Stage 1 — get labels flowing

The only stage with a deadline. Labels are unrecoverable; code is not.

There is no Telegram client. The UI is the surface people check, which means this stage
carries the cost Telegram would have absorbed: identity, notifications, and a frontend.
To stop the deadline slipping behind a web app, `gogo log` (S3) is the day-one path for
one user — start labelling with it immediately, and let the UI unblock everybody else.

- [ ] **S5 · Windows as time ranges.** Promoted from "later": a UI showing a single
  Saturday 08:00 is pointless, and an observation is an interval, so the prediction has to
  be one too. The interval-aggregation rule (max? mean? worst hour? trend-weighted?) is
  part of the score's definition — pick one, write it down, bump `SCORE_VERSION`, and treat
  changing it as a version bump. This is the product unit, not a calibration change, so it
  is not blocked by the Stage 2 gate. (The past-hours bug was pulled forward into S4:
  `saturday_morning` takes `not_before`, because impressions were recording
  recommendations for a Saturday that had already been and gone.)
- [ ] **S5b · Accounts and invites.** Magic-link email or GitHub/Google OAuth, plus login
  sessions and an invite table. Invite-only *is* the anti-spam design — it makes Sybil
  resistance a non-problem for years. No Auth0. (Note the word collision: a *login*
  session is not a *surf* session. The surf one is an `observation` now.)
- [ ] **S5c · Minimal mobile web UI.** One screen: verdict, window, drive, one sentence of
  why. Detail behind a tap. Plus the post-session card — five buttons from much worse to
  much better, then optional fault codes. Ask the quality question *before* revealing the
  score, and keep an unanchored control slice. Notifications are web push (PWA on the home
  screen for iOS) with email as the fallback for the evening message.
- [x] **S6 · Archive backfill.** `gogo backfill --from --to` against the Open-Meteo
  archive + marine archive, written with `source='archive-era5'` and `is_analysis = true`.
  Reanalysis is not a forecast; without the flag, Q2 numbers quietly assume hindsight.
  Chunked, and re-runnable: one analysis row per hour, updated in place when the archive
  revises it.

  Probed before building, and the answers shaped it: the marine **archive returns the
  same grid cells as the marine forecast**, so analysis and forecast rows join on
  `(grid_lat, grid_lon, valid_at)` with no extra mapping. Swell and wind are complete
  back to at least 2022; tide only from late 2022, and earlier hours load without a tide
  phase rather than failing. There is no hole at the recent end — a range ending today
  came back complete — so what the last few days lack is finality, not data.

  *Tests:* `test_archive.py`, headed by the negative one — an analysis row must never
  reach `forecast_current`, or `/windows` starts serving hours that already happened.
- [x] **S7 · Bulk import.** `gogo import sessions.csv` — `date,spot,start,end` required,
  everything else optional. Local times, per-row errors naming the column and the value,
  and `--dry-run` because a hand-written file gets checked before it lands.

  Two things are decided here rather than left to whoever writes the file. Imported rows
  are **unanchored**: our score cannot have been visible for a session last March, and
  importing them as anchored would poison the control slice. And they carry **`rating`,
  not `residual`** — a residual is "better or worse than predicted", and nothing was
  predicted to you at the time.

  The importer reports **same-day spot pairs**, not just a row count, because that is the
  sample size of S10's headline metric: fifty one-spot days are fifty labels and zero
  pairs. `kind=checked` rows are where pairs come from. It also names days with no
  reanalysis stored and prints the `gogo backfill` command that fixes them.

  `004` adds `unique (user_id, spot_id, started_at)`, so re-running an edited file
  corrects it instead of appending a second copy of every earlier row. Duplicated labels
  are worse than missing ones — they reweight the metric and nothing looks wrong
  afterwards. `record_observation` returns `None` on a duplicate rather than raising, and
  `gogo log` is now safe to fat-finger twice. *Tests:* `test_importer.py`.

**Gate:** ~100 observations across more than one swell event, and ≥1 month of backfilled
features.

## Stage 2 — the harness

- [ ] **S8 · As-of features.** `features.py`: build features for (spot, hour range) using
  only snapshots with `fetched_at <= as_of`. Policies: `best_known`, `lead_24h`,
  `evening_before`. Index extended to `(grid_lat, grid_lon, valid_at, fetched_at DESC)`.
  *Test:* a later fetch for the same hour is invisible at an earlier as-of.
- [ ] **S9 · Dataset.** `eval/dataset.py` joins observations to features and predictions
  and assigns **event ids** — hours inside one swell are not independent samples. v1 rule:
  new event after a gap > 36 h. Documented as v1, revisited with more data.
- [ ] **S10 · Metrics and baselines.** `eval/metrics.py`: pairwise ranking accuracy
  (headline), reliability curve, Brier on *would return*, veto precision/recall, NDCG@3
  where ≥3 spots are labelled the same day. `eval/baselines.py`: random,
  always-Carcavelos, biggest-swell-wins, height×period with onshore veto, incumbent score.
  Bootstrap CIs resampled over **events**, not rows.
- [ ] **S11 · `gogo backtest`.** `--score-version --spec-mode --as-of-policy --from --to`
  → `eval_runs` + `eval_predictions` + a diffable markdown report. Deterministic, offline.
  Spec modes: `as_of` reproduces history, `pinned:<version>` applies a proposal to old data.
  CI splits: golden-fixture regression without Postgres on every push; full eval on demand.

**Gate:** `make backtest` prints pairwise accuracy ± CI for the current score against every
baseline, under both as-of policies. If the hand-tuned score loses to biggest-swell-wins,
that is the finding we needed.

## Stage 3 — improve the score, with a number attached

Each lands only if the backtest improves, or is neutral for a written reason.

- [ ] **S12** Daylight veto (a bug — ship regardless of the number) and continuous tide:
  height plus rate of change, replacing the three-phase proxy.
- [ ] **S13** Per-spot face-height transfer, seeded from an analytic exposure factor off
  coastline orientation before anything is fitted. Offshore `swell_wave_height` is not the
  wave at the beach, and today that difference hides inside `size_min_m`/`size_max_m`.
- [ ] **S14** Multi-partition swell, directional spread, wind-sea ratio.
- [ ] **S15** Spot-facing normal in `coast.yml` → shadowing and substitution
  ("too big here, go round the peninsula"). Baleal works when Supertubos does not.
- [ ] **S16** Personalization: profile, travel time, crowd penalty. Ranking becomes
  *utility*, not quality — a 75 at Baleal loses to a 68 at Carcavelos before work.
- [ ] **S17** Ensemble spread as displayed uncertainty. Surfline's single star cannot do
  this; honesty about not knowing is a differentiator.
- [ ] **S18** Learned calibration with partial pooling across spots. Monotone and
  interpretable, reasons intact. 16 spots and sparse labels is exactly what pooling is for.

The science stack (`numpy`, `scipy`, later a sampler) lives in an `eval` dependency group
so the API and worker images stay lean.

## Stage 4 — groups and shared knowledge

Not built until Stage 2 produces numbers — this is governance for knowledge we could not
otherwise evaluate. The Stage 0 `scope` column is what makes it a feature rather than a
migration.

**Spec overlays, not silos.** A group holds *overrides* on top of the public spec — a diff,
not a copy. Resolution order is **personal → group → base**, ordinary config inheritance,
one table keyed by (scope type, scope id, spot, `valid_from`). A group is created by
someone who invites others; members edit the group's overlay.

Why overlays rather than separate worlds: because observations pool globally, a group's
override stays **testable**. Backtest it against the global pool and tell the group
"this improves agreement by X" or "this makes it worse". Folklore becomes measurable
without anyone publishing anything. And an overlay that consistently beats the base spec
on global observations is evidence the base spec should change — so groups become the
opt-in nursery for public proposals, which is a better mechanism than appointing stewards.

Guardrails:

- **Sessions stay private, even inside a group.** A group shares interpretation, never who
  was in the water when.
- **Edit rights want small groups.** A 200-person group is a forum with no moderation. Big
  groups get governed like the base spec: proposals, backtested.
- **"Public" splits into readable-by-all vs joinable-by-all.** Open-join is the global
  layer with extra steps.
- **Structured overrides and expiring notes only.** No threads, no comments, no likes. A
  group with members, notes and activity is a feed wearing a hat, and that is the failure
  mode we already refused.

Also in this stage: base-spec proposals gated on backtest improvement; drift detection
(watch the residual per spot per gate — three weeks of "worse than predicted, fault =
tide" at Foz is the system noticing a sandbar moved before anyone volunteers); reputation
derived from predictive agreement, never karma — expect deliberate *deflation*, not
inflation; the substitution graph.

**Fast lane vs slow lane.** Spec overlays are the slow lane: structural, durable,
backtested, weeks to seasons. Ephemeral local facts no model can know — access path washed
out, contest on, brown water after rain, a dangerous rip — are the fast lane: low bar to
post, short expiry, shown as a note or hazard flag on the recommendation, and never
allowed to alter scoring parameters. A hazard may suppress a recommendation for beginners;
it must not silently retrain anything.

## Amendments to frozen decisions

Approved 2026-09-02. Everything else in `AGENTS.md` stands.

1. Open-Meteo requests gain `timeformat=unixtime`; timestamps are UTC internally, Lisbon at
   the edges. The `timezone=Europe/Lisbon` request parameter stays.
2. ~~Telegram moves ahead of windows-as-ranges and the phone page.~~ **Reversed the same
   day.** There is no Telegram client. The UI is the primary surface, so windows-as-ranges
   and accounts are promoted into Stage 1 and notifications become web push plus email.
3. `sessions` is replaced by `observations` in one destructive re-init.
4. `numpy` / `scipy` enter as an `eval`-only dependency group.
5. Knowledge is scoped by **group overlays over a global observation pool** — ratings pool
   globally and anonymously, interpretation can be private to a group. The `scope` column
   lands in Stage 0; groups are built in Stage 4.

## Traps

- **Censoring.** We only get labels where we sent people. A wrong veto leaves no trace, so
  it never gets fixed. Keep an exploration budget ("check this one for us") and keep the
  `checked` observation kind.
- **Anchoring.** Asking "better or worse than predicted" biases labels toward agreement.
  Ask before revealing the score, and keep an unanchored control slice.
- **Random splits.** Adjacent hours are heavily autocorrelated; split by time and group by
  event, or every number is inflated.
- **Position bias.** Which recommendation got tapped is nearly worthless as a label early.
