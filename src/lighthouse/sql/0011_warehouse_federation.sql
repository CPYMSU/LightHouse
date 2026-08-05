-- Warehouse × LightHouse Federation v1 local durability and policy binding.

CREATE TABLE IF NOT EXISTS lh_warehouse_federation_runs (
  remote_run_id UUID PRIMARY KEY,
  local_run_id UUID NOT NULL UNIQUE,
  warehouse_origin TEXT NOT NULL,
  workspace_id UUID NOT NULL REFERENCES lh_workspaces(id),
  actor TEXT NOT NULL,
  conversation_ref TEXT,
  policy JSONB NOT NULL,
  status TEXT NOT NULL DEFAULT 'offered',
  last_sent_sequence BIGINT NOT NULL DEFAULT 0 CHECK (last_sent_sequence >= 0),
  result_digest CHAR(64),
  last_error TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_lh_warehouse_federation_runs_active
  ON lh_warehouse_federation_runs(status,updated_at DESC);

CREATE TABLE IF NOT EXISTS lh_warehouse_federation_messages (
  direction TEXT NOT NULL CHECK (direction IN ('inbound','outbound')),
  message_id UUID NOT NULL,
  message_type TEXT NOT NULL,
  payload_hash CHAR(64) NOT NULL,
  status TEXT NOT NULL,
  error TEXT,
  handled_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (direction,message_id)
);

CREATE TABLE IF NOT EXISTS lh_warehouse_federation_outbox (
  message_id UUID PRIMARY KEY,
  remote_run_id UUID REFERENCES lh_warehouse_federation_runs(remote_run_id) ON DELETE SET NULL,
  message_type TEXT NOT NULL,
  envelope JSONB NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','acknowledged')),
  attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
  last_attempt_at TIMESTAMPTZ,
  last_error TEXT,
  acknowledged_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_lh_warehouse_federation_outbox_pending
  ON lh_warehouse_federation_outbox(status,created_at);
