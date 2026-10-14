-- One row per ETL step per pipeline run: what ran, how long it took, how many rows it loaded.
-- The scheduled refresh reads this to compare a run with the one before it.

CREATE TABLE etl_runs (
  id          BIGSERIAL PRIMARY KEY,
  run_id      UUID NOT NULL,
  step        TEXT NOT NULL,
  status      TEXT NOT NULL CHECK (status IN ('ok', 'empty', 'failed', 'blocked', 'skipped')),
  records     INTEGER,
  table_rows  JSONB,
  error       TEXT,
  started_at  TIMESTAMPTZ NOT NULL,
  finished_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE etl_runs IS 'ETL step history: status, records loaded and table row counts per run';
COMMENT ON COLUMN etl_runs.run_id IS 'Shared by every step of one pipeline invocation';
COMMENT ON COLUMN etl_runs.records IS 'Records the step reported loading';
COMMENT ON COLUMN etl_runs.table_rows IS 'Row counts of the tables the step writes, after it ran';

CREATE INDEX idx_etl_runs_step_finished ON etl_runs (step, finished_at DESC);
