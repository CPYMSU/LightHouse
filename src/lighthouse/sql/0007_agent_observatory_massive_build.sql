BEGIN;

ALTER TABLE lh_agent_runs
  ADD COLUMN IF NOT EXISTS execution_status TEXT NOT NULL DEFAULT 'not_started',
  ADD COLUMN IF NOT EXISTS response_status TEXT NOT NULL DEFAULT 'pending',
  ADD COLUMN IF NOT EXISTS goal_status TEXT NOT NULL DEFAULT 'unknown',
  ADD COLUMN IF NOT EXISTS warning TEXT,
  ADD COLUMN IF NOT EXISTS auto_scope JSONB NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE lh_agent_runs DROP CONSTRAINT IF EXISTS lh_agent_runs_status_check;
ALTER TABLE lh_agent_runs ADD CONSTRAINT lh_agent_runs_status_check CHECK (
  status IN (
    'created','running','awaiting_confirmation','waiting_input','succeeded',
    'completed_with_warning','partially_completed','failed','cancelled'
  )
);

ALTER TABLE lh_agents
  ADD COLUMN IF NOT EXISTS display_name TEXT,
  ADD COLUMN IF NOT EXISTS specialty TEXT,
  ADD COLUMN IF NOT EXISTS quality_profile JSONB NOT NULL DEFAULT '{}'::jsonb;

ALTER TABLE lh_work_orders
  ADD COLUMN IF NOT EXISTS display_summary TEXT,
  ADD COLUMN IF NOT EXISTS progress DOUBLE PRECISION NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS token_usage JSONB NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE lh_work_orders DROP CONSTRAINT IF EXISTS lh_work_orders_progress_check;
ALTER TABLE lh_work_orders ADD CONSTRAINT lh_work_orders_progress_check
  CHECK (progress >= 0 AND progress <= 1);

CREATE TABLE IF NOT EXISTS lh_model_usage (
  id BIGSERIAL PRIMARY KEY,
  workspace_id UUID REFERENCES lh_workspaces(id) ON DELETE CASCADE,
  conversation_id UUID REFERENCES lh_conversations(id) ON DELETE SET NULL,
  run_id UUID REFERENCES lh_agent_runs(id) ON DELETE SET NULL,
  work_order_id UUID REFERENCES lh_work_orders(id) ON DELETE SET NULL,
  agent_id UUID REFERENCES lh_agents(id) ON DELETE SET NULL,
  project_id UUID REFERENCES lh_mega_projects(id) ON DELETE SET NULL,
  provider TEXT,
  model TEXT,
  call_kind TEXT NOT NULL DEFAULT 'model',
  input_tokens BIGINT NOT NULL DEFAULT 0,
  output_tokens BIGINT NOT NULL DEFAULT 0,
  cached_input_tokens BIGINT NOT NULL DEFAULT 0,
  reasoning_tokens BIGINT NOT NULL DEFAULT 0,
  total_tokens BIGINT NOT NULL DEFAULT 0,
  estimated BOOLEAN NOT NULL DEFAULT FALSE,
  metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_lh_model_usage_run ON lh_model_usage(run_id,created_at);
CREATE INDEX IF NOT EXISTS idx_lh_model_usage_conversation ON lh_model_usage(conversation_id,created_at);
CREATE INDEX IF NOT EXISTS idx_lh_model_usage_project ON lh_model_usage(project_id,created_at);
CREATE INDEX IF NOT EXISTS idx_lh_model_usage_work_order ON lh_model_usage(work_order_id,created_at);

CREATE TABLE IF NOT EXISTS lh_agent_recommendations (
  id UUID PRIMARY KEY,
  workspace_id UUID NOT NULL REFERENCES lh_workspaces(id) ON DELETE CASCADE,
  conversation_id UUID REFERENCES lh_conversations(id) ON DELETE SET NULL,
  run_id UUID REFERENCES lh_agent_runs(id) ON DELETE CASCADE,
  project_id UUID REFERENCES lh_mega_projects(id) ON DELETE CASCADE,
  role TEXT NOT NULL,
  reason TEXT NOT NULL,
  expected_information_gain TEXT,
  estimated_cost JSONB NOT NULL DEFAULT '{}'::jsonb,
  confidence DOUBLE PRECISION NOT NULL DEFAULT 0.5 CHECK (confidence BETWEEN 0 AND 1),
  status TEXT NOT NULL DEFAULT 'suggested' CHECK (status IN ('suggested','accepted','ignored','expired')),
  advisory_only BOOLEAN NOT NULL DEFAULT TRUE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_lh_agent_recommendations_run
  ON lh_agent_recommendations(run_id,status,created_at DESC);

CREATE TABLE IF NOT EXISTS lh_feature_wiring (
  id UUID PRIMARY KEY,
  project_id UUID NOT NULL REFERENCES lh_mega_projects(id) ON DELETE CASCADE,
  feature_key TEXT NOT NULL,
  title TEXT NOT NULL,
  frontend_state TEXT NOT NULL DEFAULT 'unknown',
  event_state TEXT NOT NULL DEFAULT 'unknown',
  api_state TEXT NOT NULL DEFAULT 'unknown',
  service_state TEXT NOT NULL DEFAULT 'unknown',
  repository_state TEXT NOT NULL DEFAULT 'unknown',
  database_state TEXT NOT NULL DEFAULT 'unknown',
  receipt_state TEXT NOT NULL DEFAULT 'unknown',
  e2e_state TEXT NOT NULL DEFAULT 'unknown',
  overall_state TEXT NOT NULL DEFAULT 'unverified',
  evidence JSONB NOT NULL DEFAULT '[]'::jsonb,
  metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
  verified_by_work_order_id UUID REFERENCES lh_work_orders(id) ON DELETE SET NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE(project_id,feature_key)
);
CREATE INDEX IF NOT EXISTS idx_lh_feature_wiring_project
  ON lh_feature_wiring(project_id,overall_state,updated_at DESC);

CREATE TABLE IF NOT EXISTS lh_project_cells (
  id UUID PRIMARY KEY,
  project_id UUID NOT NULL REFERENCES lh_mega_projects(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  domain TEXT NOT NULL DEFAULT 'general',
  goal TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'proposed' CHECK (
    status IN ('proposed','ready','running','waiting','integrating','verified','completed','failed','cancelled')
  ),
  strategy TEXT NOT NULL DEFAULT 'adaptive',
  base_commit TEXT,
  worktree_id UUID,
  contract_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
  assigned_work_orders JSONB NOT NULL DEFAULT '[]'::jsonb,
  dependencies JSONB NOT NULL DEFAULT '[]'::jsonb,
  progress DOUBLE PRECISION NOT NULL DEFAULT 0 CHECK (progress BETWEEN 0 AND 1),
  metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  completed_at TIMESTAMPTZ,
  UNIQUE(project_id,name)
);
CREATE INDEX IF NOT EXISTS idx_lh_project_cells_project
  ON lh_project_cells(project_id,status,updated_at DESC);

CREATE TABLE IF NOT EXISTS lh_project_contracts (
  id UUID PRIMARY KEY,
  project_id UUID NOT NULL REFERENCES lh_mega_projects(id) ON DELETE CASCADE,
  contract_type TEXT NOT NULL,
  name TEXT NOT NULL,
  version BIGINT NOT NULL DEFAULT 1 CHECK (version >= 1),
  status TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','provisional','stable','deprecated','superseded')),
  schema JSONB NOT NULL DEFAULT '{}'::jsonb,
  owner TEXT NOT NULL DEFAULT 'main-ai',
  consumers JSONB NOT NULL DEFAULT '[]'::jsonb,
  evidence JSONB NOT NULL DEFAULT '[]'::jsonb,
  supersedes_id UUID REFERENCES lh_project_contracts(id) ON DELETE SET NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE(project_id,contract_type,name,version)
);
CREATE INDEX IF NOT EXISTS idx_lh_project_contracts_project
  ON lh_project_contracts(project_id,status,contract_type,name,version DESC);

CREATE TABLE IF NOT EXISTS lh_project_worktrees (
  id UUID PRIMARY KEY,
  project_id UUID NOT NULL REFERENCES lh_mega_projects(id) ON DELETE CASCADE,
  cell_id UUID REFERENCES lh_project_cells(id) ON DELETE SET NULL,
  path TEXT NOT NULL,
  branch TEXT NOT NULL,
  base_ref TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','integrated','removed','failed')),
  head_commit TEXT,
  metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE(project_id,path),
  UNIQUE(project_id,branch)
);
CREATE INDEX IF NOT EXISTS idx_lh_project_worktrees_project
  ON lh_project_worktrees(project_id,status,updated_at DESC);

DO $$ BEGIN
  ALTER TABLE lh_project_cells ADD CONSTRAINT lh_project_cells_worktree_fk
    FOREIGN KEY (worktree_id) REFERENCES lh_project_worktrees(id) ON DELETE SET NULL;
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

CREATE TABLE IF NOT EXISTS lh_project_write_leases (
  id UUID PRIMARY KEY,
  project_id UUID NOT NULL REFERENCES lh_mega_projects(id) ON DELETE CASCADE,
  cell_id UUID REFERENCES lh_project_cells(id) ON DELETE CASCADE,
  owner_work_order_id UUID REFERENCES lh_work_orders(id) ON DELETE CASCADE,
  scope_type TEXT NOT NULL,
  scope TEXT NOT NULL,
  mode TEXT NOT NULL DEFAULT 'write' CHECK (mode IN ('write','exclusive')),
  status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','released','expired','cancelled')),
  base_commit TEXT,
  metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
  acquired_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  expires_at TIMESTAMPTZ NOT NULL,
  released_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_lh_project_write_leases_active
  ON lh_project_write_leases(project_id,status,expires_at,scope_type,scope);

CREATE TABLE IF NOT EXISTS lh_project_batches (
  id UUID PRIMARY KEY,
  project_id UUID NOT NULL REFERENCES lh_mega_projects(id) ON DELETE CASCADE,
  cell_id UUID REFERENCES lh_project_cells(id) ON DELETE SET NULL,
  title TEXT NOT NULL,
  goal TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'proposed' CHECK (
    status IN ('proposed','running','implemented','verifying','accepted','rolled_back','failed','cancelled')
  ),
  base_commit TEXT,
  head_commit TEXT,
  changed_files JSONB NOT NULL DEFAULT '[]'::jsonb,
  added_lines BIGINT NOT NULL DEFAULT 0,
  deleted_lines BIGINT NOT NULL DEFAULT 0,
  diff_summary JSONB NOT NULL DEFAULT '{}'::jsonb,
  receipts JSONB NOT NULL DEFAULT '[]'::jsonb,
  verification JSONB NOT NULL DEFAULT '{}'::jsonb,
  metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  completed_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_lh_project_batches_project
  ON lh_project_batches(project_id,status,updated_at DESC);

CREATE TABLE IF NOT EXISTS lh_project_integrations (
  id UUID PRIMARY KEY,
  project_id UUID NOT NULL REFERENCES lh_mega_projects(id) ON DELETE CASCADE,
  title TEXT NOT NULL,
  scope TEXT NOT NULL DEFAULT 'project',
  status TEXT NOT NULL DEFAULT 'proposed' CHECK (
    status IN ('proposed','running','conflicted','verifying','succeeded','rolled_back','failed','cancelled')
  ),
  source_cells JSONB NOT NULL DEFAULT '[]'::jsonb,
  source_batches JSONB NOT NULL DEFAULT '[]'::jsonb,
  base_commit TEXT,
  result_commit TEXT,
  conflicts JSONB NOT NULL DEFAULT '[]'::jsonb,
  receipts JSONB NOT NULL DEFAULT '[]'::jsonb,
  verification JSONB NOT NULL DEFAULT '{}'::jsonb,
  metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  completed_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_lh_project_integrations_project
  ON lh_project_integrations(project_id,status,updated_at DESC);

COMMIT;
