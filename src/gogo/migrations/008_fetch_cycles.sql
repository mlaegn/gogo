-- One row per completed fetch, so coverage is a fact rather than an inference.
--
-- This exists because 007 took the ability away. Before the payload dedup, every cycle
-- appended snapshot rows, so `select distinct fetched_at from forecast_snapshots` was an
-- exact record of when the worker ran. Now a cycle that finds nothing changed writes no
-- snapshot at all — 60 of 96 cycles in the first four days on the box — and those
-- quiet cycles are indistinguishable from an outage.
--
-- That distinction is not cosmetic. An as-of query during a six-hour outage happily
-- returns the last row before it and reports a fresh forecast, so lead-time metrics come
-- back flattering exactly where the system was performing worst. "We believed X at time
-- T" and "we were still awake at time T" are two different questions, and after 007 the
-- snapshots table could only answer the first.
--
-- Deliberately not backfilled. The 36 changed cycles could be recovered from snapshots,
-- but that would imply the other 60 never happened, which is a worse lie than an honest
-- hole. Coverage starts the day this lands.
--
-- Cheap: 24 rows a day, about 9k a year, no payload.
CREATE TABLE fetch_cycles (
    fetched_at  TIMESTAMPTZ PRIMARY KEY,
    source      TEXT    NOT NULL,
    -- Servable hours upserted into `forecast_current` — the cycle did its job.
    grid_hours  INTEGER NOT NULL,
    -- How many of those were news. Zero is healthy and common; it means the model had
    -- not run since we last asked.
    appended    INTEGER NOT NULL
);

COMMENT ON TABLE fetch_cycles IS
    'One row per completed fetch. Answers "was the worker awake", which snapshots '
    'stopped being able to answer once unchanged payloads were skipped (007).';
