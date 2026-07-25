BEGIN;

CREATE TABLE IF NOT EXISTS lh_agents (
  id UUID PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,
  role TEXT NOT NULL,
  execution_mode TEXT NOT NULL CHECK (execution_mode IN ('deterministic','model','background','external')),
  visibility TEXT NOT NULL DEFAULT 'foreground' CHECK (visibility IN ('foreground','hidden')),
  capabilities JSONB NOT NULL DEFAULT '[]'::jsonb,
  metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
  active BOOLEAN NOT NULL DEFAULT TRUE,
  health TEXT NOT NULL DEFAULT 'ready' CHECK (health IN ('ready','busy','degraded','offline')),
  max_concurrency INTEGER NOT NULL DEFAULT 1 CHECK (max_concurrency BETWEEN 1 AND 64),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_lh_agents_role ON lh_agents(role,active,health);

CREATE TABLE IF NOT EXISTS lh_work_orders (
  id UUID PRIMARY KEY,
  workspace_id UUID NOT NULL REFERENCES lh_workspaces(id) ON DELETE CASCADE,
  parent_run_id UUID REFERENCES lh_agent_runs(id) ON DELETE SET NULL,
  requested_by TEXT NOT NULL,
  role TEXT NOT NULL,
  agent_id UUID REFERENCES lh_agents(id) ON DELETE SET NULL,
  goal TEXT NOT NULL,
  payload JSONB NOT NULL DEFAULT '{}'::jsonb,
  priority INTEGER NOT NULL DEFAULT 50 CHECK (priority BETWEEN 0 AND 100),
  visibility TEXT NOT NULL DEFAULT 'foreground' CHECK (visibility IN ('foreground','hidden')),
  status TEXT NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','leased','running','waiting_dependency','waiting_confirmation','succeeded','failed','cancelled','superseded')),
  result JSONB,
  error TEXT,
  lease_owner TEXT,
  leased_at TIMESTAMPTZ,
  lease_expires_at TIMESTAMPTZ,
  heartbeat_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  completed_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_lh_work_orders_queue ON lh_work_orders(status,priority DESC,created_at);
CREATE INDEX IF NOT EXISTS idx_lh_work_orders_run ON lh_work_orders(parent_run_id,updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_lh_work_orders_workspace ON lh_work_orders(workspace_id,updated_at DESC);

CREATE TABLE IF NOT EXISTS lh_work_dependencies (
  work_order_id UUID NOT NULL REFERENCES lh_work_orders(id) ON DELETE CASCADE,
  depends_on_id UUID NOT NULL REFERENCES lh_work_orders(id) ON DELETE CASCADE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY(work_order_id,depends_on_id),
  CHECK (work_order_id <> depends_on_id)
);

CREATE TABLE IF NOT EXISTS lh_work_events (
  id BIGSERIAL PRIMARY KEY,
  work_order_id UUID NOT NULL REFERENCES lh_work_orders(id) ON DELETE CASCADE,
  event_type TEXT NOT NULL,
  payload JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_lh_work_events_order ON lh_work_events(work_order_id,id);

CREATE TABLE IF NOT EXISTS lh_background_jobs (
  id UUID PRIMARY KEY,
  workspace_id UUID NOT NULL REFERENCES lh_workspaces(id) ON DELETE CASCADE,
  conversation_id UUID REFERENCES lh_conversations(id) ON DELETE CASCADE,
  run_id UUID REFERENCES lh_agent_runs(id) ON DELETE SET NULL,
  work_order_id UUID REFERENCES lh_work_orders(id) ON DELETE SET NULL,
  job_type TEXT NOT NULL,
  coalesce_key TEXT,
  payload JSONB NOT NULL DEFAULT '{}'::jsonb,
  priority INTEGER NOT NULL DEFAULT 20 CHECK (priority BETWEEN 0 AND 100),
  status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','running','succeeded','failed','dead','cancelled')),
  attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
  max_attempts INTEGER NOT NULL DEFAULT 5 CHECK (max_attempts BETWEEN 1 AND 20),
  run_after TIMESTAMPTZ NOT NULL DEFAULT now(),
  lease_owner TEXT,
  locked_at TIMESTAMPTZ,
  lease_expires_at TIMESTAMPTZ,
  result JSONB,
  error TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  completed_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_lh_background_jobs_queue ON lh_background_jobs(status,run_after,priority DESC,created_at);
CREATE UNIQUE INDEX IF NOT EXISTS idx_lh_background_jobs_coalesce
  ON lh_background_jobs(workspace_id,coalesce_key)
  WHERE coalesce_key IS NOT NULL AND status IN ('pending','running');

CREATE TABLE IF NOT EXISTS lh_conversation_summaries (
  conversation_id UUID PRIMARY KEY REFERENCES lh_conversations(id) ON DELETE CASCADE,
  summary TEXT NOT NULL DEFAULT '',
  entities JSONB NOT NULL DEFAULT '[]'::jsonb,
  relations JSONB NOT NULL DEFAULT '[]'::jsonb,
  uncertainties JSONB NOT NULL DEFAULT '[]'::jsonb,
  distillation_level INTEGER NOT NULL DEFAULT 1 CHECK (distillation_level BETWEEN 0 AND 9),
  source_message_id BIGINT,
  source_hash CHAR(64),
  model TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS lh_context_snapshots (
  id UUID PRIMARY KEY,
  workspace_id UUID NOT NULL REFERENCES lh_workspaces(id) ON DELETE CASCADE,
  conversation_id UUID REFERENCES lh_conversations(id) ON DELETE CASCADE,
  run_id UUID REFERENCES lh_agent_runs(id) ON DELETE CASCADE,
  query_hash CHAR(64) NOT NULL,
  source_cursor CHAR(64) NOT NULL,
  distillation_level INTEGER NOT NULL DEFAULT 1 CHECK (distillation_level BETWEEN 0 AND 9),
  payload JSONB NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  expires_at TIMESTAMPTZ,
  UNIQUE(workspace_id,conversation_id,run_id,query_hash,source_cursor)
);
CREATE INDEX IF NOT EXISTS idx_lh_context_snapshots_lookup
  ON lh_context_snapshots(workspace_id,conversation_id,run_id,created_at DESC);

CREATE TABLE IF NOT EXISTS lh_world_entities (
  id UUID PRIMARY KEY,
  workspace_id UUID NOT NULL REFERENCES lh_workspaces(id) ON DELETE CASCADE,
  entity_type TEXT NOT NULL,
  canonical_key TEXT NOT NULL,
  label TEXT,
  attributes JSONB NOT NULL DEFAULT '{}'::jsonb,
  active BOOLEAN NOT NULL DEFAULT TRUE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE(workspace_id,entity_type,canonical_key)
);
CREATE INDEX IF NOT EXISTS idx_lh_world_entities_type ON lh_world_entities(workspace_id,entity_type,updated_at DESC);

CREATE TABLE IF NOT EXISTS lh_world_facts (
  id UUID PRIMARY KEY,
  workspace_id UUID NOT NULL REFERENCES lh_workspaces(id) ON DELETE CASCADE,
  entity_id UUID REFERENCES lh_world_entities(id) ON DELETE CASCADE,
  fact_key TEXT NOT NULL,
  value JSONB NOT NULL,
  source_type TEXT NOT NULL,
  source_id TEXT,
  evidence JSONB NOT NULL DEFAULT '[]'::jsonb,
  observed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  valid_until TIMESTAMPTZ,
  volatility TEXT NOT NULL DEFAULT 'medium' CHECK (volatility IN ('immutable','low','medium','high')),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_lh_world_facts_entity ON lh_world_facts(entity_id,fact_key,observed_at DESC);

CREATE TABLE IF NOT EXISTS lh_world_relations (
  id UUID PRIMARY KEY,
  workspace_id UUID NOT NULL REFERENCES lh_workspaces(id) ON DELETE CASCADE,
  from_entity_id UUID NOT NULL REFERENCES lh_world_entities(id) ON DELETE CASCADE,
  relation TEXT NOT NULL,
  to_entity_id UUID NOT NULL REFERENCES lh_world_entities(id) ON DELETE CASCADE,
  evidence JSONB NOT NULL DEFAULT '[]'::jsonb,
  confidence DOUBLE PRECISION NOT NULL DEFAULT 1 CHECK (confidence >= 0 AND confidence <= 1),
  observed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  valid_until TIMESTAMPTZ,
  UNIQUE(from_entity_id,relation,to_entity_id)
);
CREATE INDEX IF NOT EXISTS idx_lh_world_relations_from ON lh_world_relations(from_entity_id,relation);

CREATE TABLE IF NOT EXISTS lh_world_inferences (
  id UUID PRIMARY KEY,
  workspace_id UUID NOT NULL REFERENCES lh_workspaces(id) ON DELETE CASCADE,
  conversation_id UUID REFERENCES lh_conversations(id) ON DELETE CASCADE,
  claim TEXT NOT NULL,
  confidence DOUBLE PRECISION NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
  based_on JSONB NOT NULL DEFAULT '[]'::jsonb,
  distillation_level INTEGER NOT NULL DEFAULT 2 CHECK (distillation_level BETWEEN 0 AND 9),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  expires_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_lh_world_inferences_recent ON lh_world_inferences(workspace_id,conversation_id,created_at DESC);

CREATE TABLE IF NOT EXISTS lh_world_uncertainties (
  id UUID PRIMARY KEY,
  workspace_id UUID NOT NULL REFERENCES lh_workspaces(id) ON DELETE CASCADE,
  conversation_id UUID REFERENCES lh_conversations(id) ON DELETE CASCADE,
  question TEXT NOT NULL,
  severity TEXT NOT NULL DEFAULT 'low' CHECK (severity IN ('low','medium','high')),
  status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','resolved','dismissed')),
  evidence JSONB NOT NULL DEFAULT '[]'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_lh_world_uncertainties_open ON lh_world_uncertainties(workspace_id,conversation_id,status,updated_at DESC);

COMMIT;
