BEGIN;

CREATE TABLE IF NOT EXISTS lh_tools (
  id UUID PRIMARY KEY,
  tool_name TEXT NOT NULL,
  version TEXT NOT NULL DEFAULT 'v1',
  title TEXT NOT NULL,
  description TEXT NOT NULL,
  category TEXT NOT NULL,
  execution_type TEXT NOT NULL DEFAULT 'capability',
  kernel TEXT,
  risk TEXT,
  confirmation_mode TEXT,
  writes BOOLEAN NOT NULL DEFAULT FALSE,
  arguments JSONB NOT NULL DEFAULT '{}'::jsonb,
  capabilities JSONB NOT NULL DEFAULT '[]'::jsonb,
  requirements JSONB NOT NULL DEFAULT '{}'::jsonb,
  examples JSONB NOT NULL DEFAULT '[]'::jsonb,
  metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
  active BOOLEAN NOT NULL DEFAULT TRUE,
  search_vector TSVECTOR GENERATED ALWAYS AS (
    to_tsvector('simple',
      coalesce(tool_name,'') || ' ' || coalesce(title,'') || ' ' ||
      coalesce(description,'') || ' ' || coalesce(category,'')
    )
  ) STORED,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE(tool_name,version)
);
CREATE INDEX IF NOT EXISTS idx_lh_tools_search ON lh_tools USING GIN(search_vector);
CREATE INDEX IF NOT EXISTS idx_lh_tools_category ON lh_tools(category,active,updated_at DESC);

CREATE TABLE IF NOT EXISTS lh_tool_relations (
  from_tool_id UUID NOT NULL REFERENCES lh_tools(id) ON DELETE CASCADE,
  relation TEXT NOT NULL,
  to_tool_id UUID NOT NULL REFERENCES lh_tools(id) ON DELETE CASCADE,
  confidence DOUBLE PRECISION NOT NULL DEFAULT 1 CHECK (confidence BETWEEN 0 AND 1),
  metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY(from_tool_id,relation,to_tool_id),
  CHECK (from_tool_id <> to_tool_id)
);

CREATE TABLE IF NOT EXISTS lh_tool_usage (
  id BIGSERIAL PRIMARY KEY,
  tool_id UUID NOT NULL REFERENCES lh_tools(id) ON DELETE CASCADE,
  workspace_id UUID REFERENCES lh_workspaces(id) ON DELETE CASCADE,
  run_id UUID REFERENCES lh_agent_runs(id) ON DELETE SET NULL,
  project_id UUID,
  operation_id UUID REFERENCES lh_operations(id) ON DELETE SET NULL,
  outcome TEXT,
  duration_ms BIGINT,
  evidence JSONB NOT NULL DEFAULT '[]'::jsonb,
  metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_lh_tool_usage_recent ON lh_tool_usage(tool_id,created_at DESC);

CREATE TABLE IF NOT EXISTS lh_mega_projects (
  id UUID PRIMARY KEY,
  workspace_id UUID NOT NULL REFERENCES lh_workspaces(id) ON DELETE CASCADE,
  conversation_id UUID REFERENCES lh_conversations(id) ON DELETE SET NULL,
  director_run_id UUID REFERENCES lh_agent_runs(id) ON DELETE SET NULL,
  title TEXT NOT NULL,
  goal TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'active',
  current_phase TEXT NOT NULL DEFAULT 'adaptive',
  project_version BIGINT NOT NULL DEFAULT 1 CHECK (project_version >= 1),
  metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  completed_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_lh_mega_projects_context
  ON lh_mega_projects(workspace_id,conversation_id,status,updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_lh_mega_projects_run
  ON lh_mega_projects(director_run_id,updated_at DESC);

ALTER TABLE lh_tool_usage
  DROP CONSTRAINT IF EXISTS lh_tool_usage_project_id_fkey;
ALTER TABLE lh_tool_usage
  ADD CONSTRAINT lh_tool_usage_project_id_fkey
  FOREIGN KEY (project_id) REFERENCES lh_mega_projects(id) ON DELETE SET NULL;

CREATE TABLE IF NOT EXISTS lh_project_findings (
  id UUID PRIMARY KEY,
  project_id UUID NOT NULL REFERENCES lh_mega_projects(id) ON DELETE CASCADE,
  domain TEXT NOT NULL DEFAULT 'general',
  finding_type TEXT NOT NULL,
  claim TEXT NOT NULL,
  confidence DOUBLE PRECISION NOT NULL DEFAULT 0.5 CHECK (confidence BETWEEN 0 AND 1),
  evidence JSONB NOT NULL DEFAULT '[]'::jsonb,
  source_work_order_id UUID REFERENCES lh_work_orders(id) ON DELETE SET NULL,
  status TEXT NOT NULL DEFAULT 'active',
  supersedes_id UUID REFERENCES lh_project_findings(id) ON DELETE SET NULL,
  metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
  search_vector TSVECTOR GENERATED ALWAYS AS (
    to_tsvector('simple',coalesce(domain,'') || ' ' || coalesce(finding_type,'') || ' ' || coalesce(claim,''))
  ) STORED,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_lh_project_findings_search ON lh_project_findings USING GIN(search_vector);
CREATE INDEX IF NOT EXISTS idx_lh_project_findings_project
  ON lh_project_findings(project_id,status,updated_at DESC);

CREATE TABLE IF NOT EXISTS lh_project_decisions (
  id UUID PRIMARY KEY,
  project_id UUID NOT NULL REFERENCES lh_mega_projects(id) ON DELETE CASCADE,
  decision TEXT NOT NULL,
  reason TEXT NOT NULL DEFAULT '',
  evidence JSONB NOT NULL DEFAULT '[]'::jsonb,
  made_by TEXT NOT NULL DEFAULT 'main-ai',
  project_version BIGINT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_lh_project_decisions_project
  ON lh_project_decisions(project_id,created_at DESC);

CREATE TABLE IF NOT EXISTS lh_project_steps (
  id UUID PRIMARY KEY,
  project_id UUID NOT NULL REFERENCES lh_mega_projects(id) ON DELETE CASCADE,
  parent_step_id UUID REFERENCES lh_project_steps(id) ON DELETE SET NULL,
  phase TEXT NOT NULL DEFAULT 'adaptive',
  title TEXT NOT NULL,
  goal TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'proposed',
  sequence BIGINT NOT NULL DEFAULT 0,
  dependencies JSONB NOT NULL DEFAULT '[]'::jsonb,
  assigned_work_order_id UUID REFERENCES lh_work_orders(id) ON DELETE SET NULL,
  implementation_receipts JSONB NOT NULL DEFAULT '[]'::jsonb,
  verification JSONB NOT NULL DEFAULT '{}'::jsonb,
  metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  completed_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_lh_project_steps_project
  ON lh_project_steps(project_id,status,sequence,updated_at DESC);

CREATE TABLE IF NOT EXISTS lh_project_checkpoints (
  id UUID PRIMARY KEY,
  project_id UUID NOT NULL REFERENCES lh_mega_projects(id) ON DELETE CASCADE,
  project_version BIGINT NOT NULL,
  phase TEXT NOT NULL DEFAULT 'adaptive',
  summary TEXT NOT NULL DEFAULT '',
  payload JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_by TEXT NOT NULL DEFAULT 'main-ai',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_lh_project_checkpoints_project
  ON lh_project_checkpoints(project_id,project_version DESC,created_at DESC);

COMMIT;
