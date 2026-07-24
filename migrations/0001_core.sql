BEGIN;

CREATE TABLE IF NOT EXISTS lh_targets (
  id UUID PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,
  kind TEXT NOT NULL,
  config JSONB NOT NULL DEFAULT '{}'::jsonb,
  active BOOLEAN NOT NULL DEFAULT TRUE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
ALTER TABLE lh_targets DROP CONSTRAINT IF EXISTS lh_targets_kind_check;
ALTER TABLE lh_targets ADD CONSTRAINT lh_targets_kind_check CHECK (kind IN ('data','system','desktop'));

CREATE TABLE IF NOT EXISTS lh_workspaces (
  id UUID PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,
  data_target_id UUID REFERENCES lh_targets(id),
  system_target_id UUID REFERENCES lh_targets(id),
  desktop_target_id UUID REFERENCES lh_targets(id),
  config JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
ALTER TABLE lh_workspaces ADD COLUMN IF NOT EXISTS config JSONB NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE lh_workspaces ADD COLUMN IF NOT EXISTS desktop_target_id UUID REFERENCES lh_targets(id);

CREATE TABLE IF NOT EXISTS lh_operations (
  id UUID PRIMARY KEY,
  workspace_id UUID NOT NULL REFERENCES lh_workspaces(id),
  target_id UUID NOT NULL REFERENCES lh_targets(id),
  capability TEXT NOT NULL,
  kernel TEXT NOT NULL,
  actor TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('created','awaiting_confirmation','running','succeeded','failed','cancelled')),
  envelope JSONB NOT NULL,
  envelope_hash CHAR(64) NOT NULL,
  request_hash CHAR(64) NOT NULL,
  idempotency_key TEXT UNIQUE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
ALTER TABLE lh_operations DROP CONSTRAINT IF EXISTS lh_operations_kernel_check;
ALTER TABLE lh_operations ADD CONSTRAINT lh_operations_kernel_check CHECK (kernel IN ('data','system','desktop'));
CREATE INDEX IF NOT EXISTS idx_lh_operations_workspace_created ON lh_operations(workspace_id,created_at DESC);
CREATE INDEX IF NOT EXISTS idx_lh_operations_status ON lh_operations(status,updated_at);

CREATE TABLE IF NOT EXISTS lh_operation_events (
  id BIGSERIAL PRIMARY KEY,
  operation_id UUID NOT NULL REFERENCES lh_operations(id) ON DELETE CASCADE,
  sequence INTEGER NOT NULL,
  event_type TEXT NOT NULL,
  payload JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE(operation_id,sequence)
);

CREATE TABLE IF NOT EXISTS lh_operation_receipts (
  operation_id UUID PRIMARY KEY REFERENCES lh_operations(id) ON DELETE CASCADE,
  ok BOOLEAN NOT NULL,
  result JSONB NOT NULL,
  result_hash CHAR(64) NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS lh_agent_runs (
  id UUID PRIMARY KEY,
  task TEXT NOT NULL,
  workspace_id UUID NOT NULL REFERENCES lh_workspaces(id),
  actor TEXT NOT NULL,
  mode TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('created','running','awaiting_confirmation','waiting_input','succeeded','failed','cancelled')),
  max_steps INTEGER NOT NULL CHECK (max_steps BETWEEN 1 AND 64),
  current_step INTEGER NOT NULL DEFAULT 0 CHECK (current_step >= 0),
  auto_confirm BOOLEAN NOT NULL DEFAULT FALSE,
  pending_operation_id UUID REFERENCES lh_operations(id),
  final_message TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
ALTER TABLE lh_agent_runs DROP CONSTRAINT IF EXISTS lh_agent_runs_mode_check;
ALTER TABLE lh_agent_runs ADD CONSTRAINT lh_agent_runs_mode_check CHECK (mode IN ('data','system','desktop','auto'));
CREATE INDEX IF NOT EXISTS idx_lh_agent_runs_workspace_created ON lh_agent_runs(workspace_id,created_at DESC);
CREATE INDEX IF NOT EXISTS idx_lh_agent_runs_status ON lh_agent_runs(status,updated_at);

CREATE TABLE IF NOT EXISTS lh_agent_steps (
  id BIGSERIAL PRIMARY KEY,
  run_id UUID NOT NULL REFERENCES lh_agent_runs(id) ON DELETE CASCADE,
  sequence INTEGER NOT NULL,
  kind TEXT NOT NULL,
  payload JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE(run_id,sequence)
);

CREATE TABLE IF NOT EXISTS lh_workspace_data_targets (
  workspace_id UUID NOT NULL REFERENCES lh_workspaces(id) ON DELETE CASCADE,
  target_id UUID NOT NULL REFERENCES lh_targets(id),
  alias TEXT NOT NULL CHECK (alias ~ '^[a-z][a-z0-9_-]{0,63}$'),
  is_default BOOLEAN NOT NULL DEFAULT FALSE,
  active BOOLEAN NOT NULL DEFAULT TRUE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY(workspace_id,alias),
  UNIQUE(workspace_id,target_id)
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_lh_workspace_data_targets_default ON lh_workspace_data_targets(workspace_id) WHERE is_default AND active;
INSERT INTO lh_workspace_data_targets(workspace_id,target_id,alias,is_default)
SELECT id,data_target_id,'default',TRUE FROM lh_workspaces WHERE data_target_id IS NOT NULL
ON CONFLICT (workspace_id,alias) DO NOTHING;

CREATE TABLE IF NOT EXISTS lh_schema_nodes (
  id CHAR(64) PRIMARY KEY,
  target_id UUID NOT NULL REFERENCES lh_targets(id) ON DELETE CASCADE,
  kind TEXT NOT NULL CHECK (kind IN ('database','schema','table','column')),
  qualified_name TEXT NOT NULL,
  parent_id CHAR(64),
  title TEXT NOT NULL,
  payload JSONB NOT NULL DEFAULT '{}'::jsonb,
  content_hash CHAR(64) NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE(target_id,kind,qualified_name)
);
CREATE INDEX IF NOT EXISTS idx_lh_schema_nodes_target_kind ON lh_schema_nodes(target_id,kind);

CREATE TABLE IF NOT EXISTS lh_schema_edges (
  target_id UUID NOT NULL REFERENCES lh_targets(id) ON DELETE CASCADE,
  from_node_id CHAR(64) NOT NULL REFERENCES lh_schema_nodes(id) ON DELETE CASCADE,
  to_node_id CHAR(64) NOT NULL REFERENCES lh_schema_nodes(id) ON DELETE CASCADE,
  relation TEXT NOT NULL,
  metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
  PRIMARY KEY(target_id,from_node_id,to_node_id,relation)
);

CREATE TABLE IF NOT EXISTS lh_data_resources (
  id UUID PRIMARY KEY,
  target_id UUID NOT NULL REFERENCES lh_targets(id) ON DELETE CASCADE,
  resource_name TEXT NOT NULL,
  schema_name TEXT NOT NULL,
  table_name TEXT NOT NULL,
  primary_key JSONB NOT NULL DEFAULT '[]'::jsonb,
  readable_columns JSONB NOT NULL DEFAULT '[]'::jsonb,
  writable_columns JSONB NOT NULL DEFAULT '[]'::jsonb,
  display_column TEXT,
  default_order JSONB NOT NULL DEFAULT '[]'::jsonb,
  policy JSONB NOT NULL DEFAULT '{}'::jsonb,
  active BOOLEAN NOT NULL DEFAULT TRUE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE(target_id,resource_name)
);
CREATE INDEX IF NOT EXISTS idx_lh_data_resources_target ON lh_data_resources(target_id,active,resource_name);

CREATE TABLE IF NOT EXISTS lh_semantic_commands (
  id UUID PRIMARY KEY,
  target_id UUID NOT NULL REFERENCES lh_targets(id) ON DELETE CASCADE,
  command_name TEXT NOT NULL,
  resource_name TEXT NOT NULL,
  action TEXT NOT NULL CHECK (action IN ('search','show')),
  definition JSONB NOT NULL DEFAULT '{}'::jsonb,
  active BOOLEAN NOT NULL DEFAULT TRUE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE(target_id,command_name)
);
CREATE INDEX IF NOT EXISTS idx_lh_semantic_commands_target ON lh_semantic_commands(target_id,active,command_name);

CREATE TABLE IF NOT EXISTS lh_index_nodes (
  id UUID PRIMARY KEY,
  workspace_id UUID REFERENCES lh_workspaces(id) ON DELETE CASCADE,
  kind TEXT NOT NULL,
  source_type TEXT NOT NULL,
  source_id TEXT NOT NULL,
  revision TEXT,
  title TEXT NOT NULL,
  search_text TEXT NOT NULL DEFAULT '',
  search_vector TSVECTOR GENERATED ALWAYS AS (to_tsvector('simple',coalesce(title,'') || ' ' || coalesce(search_text,''))) STORED,
  payload JSONB NOT NULL DEFAULT '{}'::jsonb,
  acl_scope TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
  content_hash CHAR(64) NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE(workspace_id,source_type,source_id,revision)
);
CREATE INDEX IF NOT EXISTS idx_lh_index_nodes_search ON lh_index_nodes USING GIN(search_vector);

CREATE TABLE IF NOT EXISTS lh_index_edges (
  from_node_id UUID NOT NULL REFERENCES lh_index_nodes(id) ON DELETE CASCADE,
  to_node_id UUID NOT NULL REFERENCES lh_index_nodes(id) ON DELETE CASCADE,
  relation TEXT NOT NULL,
  weight DOUBLE PRECISION NOT NULL DEFAULT 1,
  metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
  PRIMARY KEY(from_node_id,to_node_id,relation)
);

COMMIT;
