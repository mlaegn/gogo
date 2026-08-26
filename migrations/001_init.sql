-- System of record. Worker writes snapshots; API reads current.
-- Unique (spot_id, valid_at) on current so a retry cannot duplicate an hour.

CREATE TABLE spots (
    id            TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    lat           DOUBLE PRECISION NOT NULL,
    lon           DOUBLE PRECISION NOT NULL,
    region        TEXT NOT NULL,
    spec          JSONB NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE forecast_snapshots (
    id            BIGSERIAL PRIMARY KEY,
    grid_lat      DOUBLE PRECISION NOT NULL,
    grid_lon      DOUBLE PRECISION NOT NULL,
    valid_at      TIMESTAMPTZ NOT NULL,
    fetched_at    TIMESTAMPTZ NOT NULL,
    source        TEXT NOT NULL,
    payload       JSONB NOT NULL
);

CREATE INDEX forecast_snapshots_grid_valid_idx
    ON forecast_snapshots (grid_lat, grid_lon, valid_at);

CREATE TABLE forecast_current (
    grid_lat      DOUBLE PRECISION NOT NULL,
    grid_lon      DOUBLE PRECISION NOT NULL,
    valid_at      TIMESTAMPTZ NOT NULL,
    fetched_at    TIMESTAMPTZ NOT NULL,
    source        TEXT NOT NULL,
    payload       JSONB NOT NULL,
    PRIMARY KEY (grid_lat, grid_lon, valid_at)
);

CREATE TABLE sessions (
    id            BIGSERIAL PRIMARY KEY,
    spot_id       TEXT NOT NULL REFERENCES spots (id),
    surfed_at     TIMESTAMPTZ NOT NULL,
    rating        SMALLINT NOT NULL CHECK (rating BETWEEN 1 AND 5),
    note          TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
