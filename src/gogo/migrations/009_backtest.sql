-- Somewhere for evaluation to put its results, and the spec history it needs to run.
--
-- Deliberately separate tables rather than columns on `window_impressions`: that is the
-- record of what was served, and an experiment must never be able to edit it. Backtests
-- are cheap, wrong often, and re-run constantly; the served record is none of those.

-- === the missing half of a replayable verdict =======================================
--
-- S2 stored `spec_version` on every impression so a recommendation stayed replayable
-- after the spot file was edited. It does not. `spots.spec` is overwritten by
-- `seed_spots` on every fetch, and impressions carry only the twelve-character digest,
-- so a version string has nothing to resolve against. S11's `--spec-mode as_of`, which
-- is supposed to reproduce history, is not implementable without this table.
--
-- Nothing has been lost yet, and only by luck: every stored digest still matches a row
-- in `spots` because `coast.yml` has not been edited since those impressions were
-- written. The first edit orphans all of them at once.
--
-- Insert-only and keyed on the digest, so a spec that comes back after being reverted
-- is the same row rather than a second one — which is exactly what a content hash is
-- for.
CREATE TABLE spot_specs (
    spot_id      TEXT        NOT NULL REFERENCES spots (id),
    spec_version TEXT        NOT NULL,
    spec         JSONB       NOT NULL,
    first_seen   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (spot_id, spec_version)
);

-- Capture what is in `spots` right now, so today's specs are recoverable even though
-- everything before today is not.
INSERT INTO spot_specs (spot_id, spec_version, spec)
SELECT id, spec_version, spec FROM spots
ON CONFLICT DO NOTHING;

-- === evaluation output ==============================================================

-- One row per `gogo backtest`. The arguments are columns rather than a blob because
-- the whole point is comparing runs: "the same labels under a different score version"
-- is a WHERE clause, not an archaeology exercise.
CREATE TABLE eval_runs (
    id            BIGSERIAL PRIMARY KEY,
    ran_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    score_version TEXT    NOT NULL,
    spec_mode     TEXT    NOT NULL,
    as_of_policy  TEXT    NOT NULL,
    from_day      DATE,
    to_day        DATE,
    -- Recorded rather than assumed. A number computed over fixture labels measures our
    -- own assumptions, and six months from now nobody will remember which run was which.
    synthetic     BOOLEAN NOT NULL DEFAULT false,
    samples       INTEGER NOT NULL,
    events        INTEGER NOT NULL,
    pairs         INTEGER NOT NULL,
    -- Every metric and baseline from the run, keyed by name. JSONB because the metric
    -- set will grow through Stage 3 and a column per metric would mean a migration each
    -- time.
    metrics       JSONB   NOT NULL,
    -- The bootstrap seed, so a run is reproducible rather than merely repeatable.
    seed          INTEGER NOT NULL
);

CREATE INDEX eval_runs_recent_idx ON eval_runs (ran_at DESC);

-- Per-label detail behind a run, so a surprising number can be opened up without
-- recomputing it — and so two runs can be diffed row by row to find which labels moved.
CREATE TABLE eval_predictions (
    run_id            BIGINT  NOT NULL REFERENCES eval_runs (id) ON DELETE CASCADE,
    observation_id    BIGINT  NOT NULL REFERENCES observations (id) ON DELETE CASCADE,
    spot_id           TEXT    NOT NULL REFERENCES spots (id),
    day               DATE    NOT NULL,
    event_id          INTEGER NOT NULL,
    predicted_score   SMALLINT,
    predicted_verdict TEXT,
    rating            SMALLINT,
    PRIMARY KEY (run_id, observation_id)
);

CREATE INDEX eval_predictions_observation_idx ON eval_predictions (observation_id);
