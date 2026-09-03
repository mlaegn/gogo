-- The labelled loop. `forecast_snapshots` says what was predicted; these tables say
-- what a human saw. Joined on (spot, hour) they are a supervised dataset.

-- `sessions` was a placeholder: a single timestamp, one 1-5 rating, no user, no way to
-- say which part of the forecast was wrong. Nothing has been written to it.
DROP TABLE IF EXISTS sessions;

CREATE TABLE users (
    id          BIGSERIAL PRIMARY KEY,
    handle      TEXT UNIQUE NOT NULL,
    skill       TEXT NOT NULL,
    home_lat    DOUBLE PRECISION,
    home_lon    DOUBLE PRECISION,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- What a person saw, over an interval, at one spot.
--
-- residual is the load-bearing field: how it compared to what we predicted, -2..+2.
-- People are unreliable on absolute scales and good at comparison, so `rating` is the
-- optional secondary signal, not the primary one.
--
-- kind = 'checked' means looked at it and did not paddle out. Those rows are the only
-- trace a wrongly-vetoed spot ever leaves, without which the dataset is censored by our
-- own recommendations.
--
-- anchored records whether the score was visible when the question was answered.
-- Unanchored rows are the control group that proves agreement is not self-fulfilling.
--
-- scope is 'global' for every row today. Group-scoped knowledge is Stage 4; the column
-- is free now and a migration through live data later.
CREATE TABLE observations (
    id            BIGSERIAL PRIMARY KEY,
    user_id       BIGINT NOT NULL REFERENCES users (id),
    spot_id       TEXT   NOT NULL REFERENCES spots (id),
    kind          TEXT   NOT NULL CHECK (kind IN ('surfed', 'checked', 'cam')),
    scope         TEXT   NOT NULL DEFAULT 'global',
    started_at    TIMESTAMPTZ NOT NULL,
    ended_at      TIMESTAMPTZ NOT NULL,
    reported_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    residual      SMALLINT CHECK (residual BETWEEN -2 AND 2),
    anchored      BOOLEAN NOT NULL,
    would_return  BOOLEAN,
    rating        SMALLINT CHECK (rating BETWEEN 1 AND 5),
    crowd         TEXT CHECK (crowd IN ('empty', 'ok', 'busy', 'zoo')),
    note          TEXT,
    CHECK (ended_at > started_at)
);

CREATE INDEX observations_spot_time_idx ON observations (spot_id, started_at);

-- Which gate was wrong. The codes are the score's own reason codes, so an error is
-- attributable ("the size gate is off here") instead of merely registered ("it was
-- worse"). This is also what makes drift detection possible: three weeks of
-- tide-direction faults at one beach is a sandbar that moved.
CREATE TABLE observation_faults (
    observation_id BIGINT   NOT NULL REFERENCES observations (id) ON DELETE CASCADE,
    code           TEXT     NOT NULL
        CHECK (code IN ('swell_dir', 'size', 'period', 'wind', 'tide')),
    direction      SMALLINT NOT NULL CHECK (direction IN (-1, 1)),
    PRIMARY KEY (observation_id, code)
);

-- What a human was actually shown, append-only, never recomputed. This is the audit
-- log: without it a residual has nothing to be a residual *of*, and no past
-- recommendation can be reproduced.
--
-- Evaluation output does not belong here. Backtest runs get their own tables in Stage 2
-- precisely so experiments cannot pollute the record of what was served.
CREATE TABLE window_impressions (
    id            BIGSERIAL PRIMARY KEY,
    user_id       BIGINT REFERENCES users (id),
    spot_id       TEXT   NOT NULL REFERENCES spots (id),
    window_start  TIMESTAMPTZ NOT NULL,
    window_end    TIMESTAMPTZ NOT NULL,
    shown_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    as_of         TIMESTAMPTZ NOT NULL,   -- fetched_at of the forecast behind it
    surface       TEXT     NOT NULL CHECK (surface IN ('api', 'cli')),
    rank          SMALLINT NOT NULL,
    score         SMALLINT NOT NULL,
    verdict       TEXT     NOT NULL,
    reasons       JSONB    NOT NULL,
    score_version TEXT     NOT NULL,
    spec_version  TEXT     NOT NULL
);

CREATE INDEX window_impressions_spot_window_idx
    ON window_impressions (spot_id, window_start);
