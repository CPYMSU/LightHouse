BEGIN;

CREATE TABLE IF NOT EXISTS lh_memory_projections (
  run_id UUID NOT NULL REFERENCES lh_agent_runs(id) ON DELETE CASCADE,
  sequence INTEGER NOT NULL,
  kind TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY(run_id,sequence,kind)
);

COMMIT;
