-- Reanalysis, kept distinguishable from forecast.
--
-- ERA5 knows what happened. A forecast guessed. Both describe the same hour, so without
-- a flag they are indistinguishable once stored — and Stage 2's second question ("would
-- we have called it right with what we had?") would quietly be answered with data that
-- did not exist at the time. That is the one mistake the harness must not make.
ALTER TABLE forecast_snapshots
    ADD COLUMN is_analysis BOOLEAN NOT NULL DEFAULT false;

-- A forecast has many rows per hour, one per fetch: that is why snapshots are
-- append-only. An analysis has one per hour, because there is only one account of what
-- happened -- though the most recent days are revised later, so that one row is
-- updated in place rather than frozen. The partial index says both things, and makes
-- `gogo backfill` re-runnable over a range that only partly landed.
CREATE UNIQUE INDEX forecast_snapshots_analysis_uniq
    ON forecast_snapshots (grid_lat, grid_lon, valid_at)
    WHERE is_analysis;

-- `forecast_current` deliberately gets no such column. It is the serving table, so an
-- analysis row must never reach it; there is no state there for a flag to describe.
