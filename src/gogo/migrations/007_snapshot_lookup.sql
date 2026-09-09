-- The newest forecast we hold for one cell-hour, found in one index seek.
--
-- Two things want exactly this, which is why it is one index and not two.
--
-- `persist_hours` now appends a snapshot only when the payload differs from the last
-- one for that (cell, hour). Two fetches 23 minutes apart on 8 September wrote 1176
-- rows and 1176 identical payloads: hourly polling of a model that updates a few times
-- a day is mostly recording the same belief over and over. Skipping a repeat costs one
-- lookup per hour, and this is that lookup.
--
-- It is also the index S8 needs. As-of features ask "what did we believe about hour H
-- at time T", which is this same ordering with a `fetched_at <= T` bound. Building it
-- now means the harness does not start by adding an index to a table with millions of
-- rows in it.
--
-- Partial on `NOT is_analysis` because reanalysis has its own uniqueness (003) and one
-- row per hour, so it has nothing to search through.
CREATE INDEX forecast_snapshots_latest_idx
    ON forecast_snapshots (grid_lat, grid_lon, valid_at, fetched_at DESC)
    WHERE NOT is_analysis;
