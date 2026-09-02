# Revision plan

Agreed 2026-09-02. This document is the contract for the next four stages.
When a slice lands, tick it here. When reality disagrees with this file, edit this file.

## Why

Community input is **training signal, not content**. There is no feed, no follower graph,
no posts. A local answers one question after a session and the score gets better at
ranking spots. `forecast_snapshots` joined to human observations on (spot, hour) is a
supervised dataset — that is the product, and nothing else in surf forecasting has it.

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
- [ ] **S2 · Versioning.** `SCORE_VERSION` bumped by hand; `spec_version` per spot from a
  hash of its `coast.yml` entry, stored on `spots`. Nothing downstream reproduces without
  these. *Test:* editing one spot's `tides` changes only that spot's version.
- [ ] **S3 · Observations.** Replace `sessions` with `users` / `observations` /
  `observation_faults`: interval not instant, user id, residual −2..+2, fault codes drawn
  from the score's own reason codes, `kind` in surfed|checked|cam, `anchored` flag, lag
  from `reported_at − ended_at`. `gogo log` + `POST /observations`.
- [ ] **S4 · Impressions.** `window_impressions`, written on every `/windows` and CLI
  render, stamped with `as_of`, `score_version`, `spec_version`, rank, reasons.
  Append-only, never recomputed. Audit log, not eval output.
- [ ] **S4b · Migration runner.** `gogo migrate` + `schema_migrations`, ~30 lines, no
  Alembic. S1–S4 land as one destructive re-init of `001_init.sql`; the runner lands
  before real data accumulates.

**Gate:** an observation can be logged in under 15 s, and every recommendation ever shown
is recorded with its inputs.

## Stage 1 — get labels flowing

The only stage with a deadline. Labels are unrecoverable; code is not.

- [ ] **S5 · Telegram.** Scheduled go/no-go, plus a post-session prompt whose inline
  buttons map onto the residual scale and the fault codes. Two commands, no conversation
  state machine, no free-text parsing. A Telegram user id *is* the invite-only account.
- [ ] **S6 · Archive backfill.** `gogo backfill --from --to` against the Open-Meteo
  archive + marine archive, written with `source='archive-era5'` and `is_analysis = true`.
  Reanalysis is not a forecast; without the flag, Q2 numbers quietly assume hindsight.
- [ ] **S7 · Bulk import.** A dumb CSV importer for past sessions, so camera-roll
  timestamps become a hundred labels in one sitting.

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

## Stage 4 — community, sketched only

Spot-spec proposals gated on backtest improvement; reputation derived from predictive
agreement (not karma — expect deliberate *deflation*, not inflation); substitution graph;
phone UI. Not designed until Stage 2 produces numbers.

## Amendments to frozen decisions

Approved 2026-09-02. Everything else in `AGENTS.md` stands.

1. Open-Meteo requests gain `timeformat=unixtime`; timestamps are UTC internally, Lisbon at
   the edges. The `timezone=Europe/Lisbon` request parameter stays.
2. Telegram moves ahead of windows-as-ranges and the phone page — it is the label channel.
3. `sessions` is replaced by `observations` in one destructive re-init.
4. `numpy` / `scipy` enter as an `eval`-only dependency group.

## Traps

- **Censoring.** We only get labels where we sent people. A wrong veto leaves no trace, so
  it never gets fixed. Keep an exploration budget ("check this one for us") and keep the
  `checked` observation kind.
- **Anchoring.** Asking "better or worse than predicted" biases labels toward agreement.
  Ask before revealing the score, and keep an unanchored control slice.
- **Random splits.** Adjacent hours are heavily autocorrelated; split by time and group by
  event, or every number is inflated.
- **Position bias.** Which recommendation got tapped is nearly worthless as a label early.
