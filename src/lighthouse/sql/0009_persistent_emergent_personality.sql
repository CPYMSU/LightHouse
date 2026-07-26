BEGIN;

ALTER TABLE lh_neuron_states
  ADD COLUMN IF NOT EXISTS inhibition DOUBLE PRECISION NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS plasticity DOUBLE PRECISION NOT NULL DEFAULT 0.5,
  ADD COLUMN IF NOT EXISTS stability DOUBLE PRECISION NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS intrinsic_reward DOUBLE PRECISION NOT NULL DEFAULT 0;

ALTER TABLE lh_neuron_edges
  ADD COLUMN IF NOT EXISTS edge_type TEXT NOT NULL DEFAULT 'adaptive',
  ADD COLUMN IF NOT EXISTS plasticity DOUBLE PRECISION NOT NULL DEFAULT 0.01,
  ADD COLUMN IF NOT EXISTS permanence DOUBLE PRECISION NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS usage_count BIGINT NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS success_count BIGINT NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS failure_count BIGINT NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS dormant BOOLEAN NOT NULL DEFAULT FALSE,
  ADD COLUMN IF NOT EXISTS last_activated_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS last_modified_at TIMESTAMPTZ NOT NULL DEFAULT now();

UPDATE lh_neuron_edges
SET edge_type = CASE
  WHEN weight > 0.005 THEN 'excitatory'
  WHEN weight < -0.005 THEN 'inhibitory'
  ELSE 'adaptive'
END
WHERE edge_type = 'adaptive';

CREATE TABLE IF NOT EXISTS lh_neuron_identities (
  workspace_id UUID PRIMARY KEY REFERENCES lh_workspaces(id) ON DELETE CASCADE,
  identity_seed BIGINT NOT NULL,
  generation INTEGER NOT NULL DEFAULT 1 CHECK (generation >= 1),
  schema_version INTEGER NOT NULL DEFAULT 1 CHECK (schema_version >= 1),
  event_count BIGINT NOT NULL DEFAULT 0 CHECK (event_count >= 0),
  birth_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb,
  current_signature DOUBLE PRECISION[],
  last_event_id BIGINT REFERENCES lh_stimulus_events(id) ON DELETE SET NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS lh_neuron_learning_events (
  id BIGSERIAL PRIMARY KEY,
  workspace_id UUID NOT NULL REFERENCES lh_workspaces(id) ON DELETE CASCADE,
  source_event_id BIGINT NOT NULL REFERENCES lh_stimulus_events(id) ON DELETE CASCADE,
  neuron_id SMALLINT NOT NULL REFERENCES lh_neurons(id) ON DELETE CASCADE,
  global_reward DOUBLE PRECISION NOT NULL DEFAULT 0,
  local_reward DOUBLE PRECISION NOT NULL DEFAULT 0,
  contribution DOUBLE PRECISION NOT NULL DEFAULT 0,
  responsibility DOUBLE PRECISION NOT NULL DEFAULT 0,
  intrinsic_reward DOUBLE PRECISION NOT NULL DEFAULT 0,
  prediction_error DOUBLE PRECISION NOT NULL DEFAULT 0,
  metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_lh_neuron_learning_workspace
  ON lh_neuron_learning_events(workspace_id,created_at DESC);
CREATE INDEX IF NOT EXISTS idx_lh_neuron_learning_event
  ON lh_neuron_learning_events(source_event_id,neuron_id);

CREATE TABLE IF NOT EXISTS lh_neuron_edge_history (
  id BIGSERIAL PRIMARY KEY,
  workspace_id UUID NOT NULL REFERENCES lh_workspaces(id) ON DELETE CASCADE,
  source_neuron_id SMALLINT NOT NULL REFERENCES lh_neurons(id) ON DELETE CASCADE,
  target_neuron_id SMALLINT NOT NULL REFERENCES lh_neurons(id) ON DELETE CASCADE,
  source_event_id BIGINT REFERENCES lh_stimulus_events(id) ON DELETE SET NULL,
  old_weight DOUBLE PRECISION NOT NULL,
  new_weight DOUBLE PRECISION NOT NULL,
  local_reward DOUBLE PRECISION,
  prediction_error DOUBLE PRECISION,
  reason TEXT NOT NULL DEFAULT 'plasticity_update',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CHECK (source_neuron_id <> target_neuron_id)
);
CREATE INDEX IF NOT EXISTS idx_lh_neuron_edge_history_workspace
  ON lh_neuron_edge_history(workspace_id,created_at DESC);

CREATE TABLE IF NOT EXISTS lh_neuron_attractors (
  id UUID PRIMARY KEY,
  workspace_id UUID NOT NULL REFERENCES lh_workspaces(id) ON DELETE CASCADE,
  centroid_vector DOUBLE PRECISION[] NOT NULL,
  dominant_neurons SMALLINT[] NOT NULL DEFAULT '{}'::smallint[],
  occurrence_count BIGINT NOT NULL DEFAULT 1 CHECK (occurrence_count >= 1),
  success_score DOUBLE PRECISION NOT NULL DEFAULT 0,
  stability DOUBLE PRECISION NOT NULL DEFAULT 0,
  last_run_id UUID REFERENCES lh_abm_runs(id) ON DELETE SET NULL,
  first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_lh_neuron_attractors_workspace
  ON lh_neuron_attractors(workspace_id,stability DESC,occurrence_count DESC);

CREATE TABLE IF NOT EXISTS lh_neuron_circuits (
  id UUID PRIMARY KEY,
  workspace_id UUID NOT NULL REFERENCES lh_workspaces(id) ON DELETE CASCADE,
  signature TEXT NOT NULL,
  neuron_ids SMALLINT[] NOT NULL,
  edge_weights DOUBLE PRECISION[] NOT NULL,
  circuit_strength DOUBLE PRECISION NOT NULL DEFAULT 0,
  stability DOUBLE PRECISION NOT NULL DEFAULT 0,
  activation_count BIGINT NOT NULL DEFAULT 0 CHECK (activation_count >= 0),
  success_rate DOUBLE PRECISION NOT NULL DEFAULT 0,
  kind TEXT NOT NULL DEFAULT 'recurrent',
  last_activated_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE(workspace_id,signature)
);
CREATE INDEX IF NOT EXISTS idx_lh_neuron_circuits_workspace
  ON lh_neuron_circuits(workspace_id,stability DESC,circuit_strength DESC);

CREATE TABLE IF NOT EXISTS lh_neuron_controls (
  workspace_id UUID PRIMARY KEY REFERENCES lh_workspaces(id) ON DELETE CASCADE,
  control JSONB NOT NULL DEFAULT '{}'::jsonb,
  source_run_id UUID REFERENCES lh_abm_runs(id) ON DELETE SET NULL,
  version BIGINT NOT NULL DEFAULT 1 CHECK (version >= 1),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS lh_neuron_checkpoints (
  id UUID PRIMARY KEY,
  workspace_id UUID NOT NULL REFERENCES lh_workspaces(id) ON DELETE CASCADE,
  event_count BIGINT NOT NULL CHECK (event_count >= 0),
  checkpoint_type TEXT NOT NULL DEFAULT 'periodic',
  states JSONB NOT NULL,
  weights JSONB NOT NULL,
  edges JSONB NOT NULL,
  attractors JSONB NOT NULL DEFAULT '[]'::jsonb,
  identity_signature DOUBLE PRECISION[],
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE(workspace_id,event_count)
);
CREATE INDEX IF NOT EXISTS idx_lh_neuron_checkpoints_workspace
  ON lh_neuron_checkpoints(workspace_id,event_count DESC);

CREATE OR REPLACE FUNCTION lh_capture_neuron_stimulus()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
DECLARE
  old_row JSONB := '{}'::jsonb;
  new_row JSONB := '{}'::jsonb;
  row_data JSONB;
  safe_old JSONB;
  safe_new JSONB;
  changed_keys JSONB := '[]'::jsonb;
  resolved_workspace UUID;
  resolved_source_id TEXT;
  conversation_key TEXT;
  operation_key TEXT;
  content_text TEXT := '';
  interaction_features JSONB := '{}'::jsonb;
BEGIN
  IF pg_trigger_depth() > 1 THEN
    IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
    RETURN NEW;
  END IF;

  IF TG_OP <> 'INSERT' THEN old_row := to_jsonb(OLD); END IF;
  IF TG_OP <> 'DELETE' THEN new_row := to_jsonb(NEW); END IF;
  row_data := CASE WHEN TG_OP = 'DELETE' THEN old_row ELSE new_row END;

  IF row_data ? 'workspace_id' AND NULLIF(row_data->>'workspace_id','') IS NOT NULL THEN
    resolved_workspace := (row_data->>'workspace_id')::uuid;
  ELSIF TG_TABLE_NAME IN ('lh_messages','lh_conversation_summaries') THEN
    conversation_key := COALESCE(row_data->>'conversation_id',row_data->>'id');
    IF NULLIF(conversation_key,'') IS NOT NULL THEN
      SELECT workspace_id INTO resolved_workspace
      FROM lh_conversations WHERE id=conversation_key::uuid;
    END IF;
  ELSIF TG_TABLE_NAME IN ('lh_operation_events','lh_operation_receipts') THEN
    operation_key := row_data->>'operation_id';
    IF NULLIF(operation_key,'') IS NOT NULL THEN
      SELECT workspace_id INTO resolved_workspace
      FROM lh_operations WHERE id=operation_key::uuid;
    END IF;
  END IF;

  IF resolved_workspace IS NULL OR NOT EXISTS (
    SELECT 1 FROM lh_workspaces WHERE id=resolved_workspace
  ) THEN
    IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
    RETURN NEW;
  END IF;

  SELECT COALESCE(jsonb_agg(key ORDER BY key),'[]'::jsonb)
  INTO changed_keys
  FROM (
    SELECT key
    FROM jsonb_object_keys(old_row || new_row) AS keys(key)
    WHERE old_row->key IS DISTINCT FROM new_row->key
  ) changes;

  content_text := COALESCE(new_row->>'content',old_row->>'content','');
  IF TG_TABLE_NAME = 'lh_messages' AND length(content_text) > 0 THEN
    interaction_features := jsonb_build_object(
      'content_length', LEAST(1.0,length(content_text)::double precision / 2000.0),
      'question_density', LEAST(
        1.0,
        (length(content_text)-length(replace(content_text,'?','')))::double precision
        / GREATEST(1.0,length(content_text)::double precision / 120.0)
      ),
      'approval', CASE WHEN content_text ~* '(好的|好吧|可以|同意|通过|正確|正确|很好|不错|great|good|approved|yes)' THEN 0.8 ELSE 0 END,
      'rejection', CASE WHEN content_text ~* '(不对|不是这个|错误|不行|拒绝|不可以|wrong|incorrect|not this|no[ ,.!])' THEN 0.85 ELSE 0 END,
      'correction', CASE WHEN content_text ~* '(我的意思|应该是|改成|修复|纠正|重新|i mean|should be|change to|fix)' THEN 0.8 ELSE 0 END,
      'frustration', CASE WHEN content_text ~* '(太复杂|卡住|无法|又失败|流氓锁|废|烦|frustrat|stuck|annoy|cannot)' THEN 0.85 ELSE 0 END,
      'continuation', CASE WHEN content_text ~* '(继续|开始吧|执行吧|做吧|往下|go ahead|continue|proceed|start)' THEN 0.8 ELSE 0 END,
      'directness', CASE WHEN content_text ~* '(直接|一次性|完整做完|不要问|无需确认|全部实作|全部實作|directly|without asking|complete it)' THEN 0.85 ELSE 0 END
    );
  END IF;

  safe_old := old_row - ARRAY[
    'content','search_text','search_vector','payload','result','envelope',
    'value','evidence','config'
  ]::text[];
  safe_new := new_row - ARRAY[
    'content','search_text','search_vector','payload','result','envelope',
    'value','evidence','config'
  ]::text[];

  resolved_source_id := COALESCE(
    row_data->>'id',row_data->>'operation_id',row_data->>'run_id',
    row_data->>'conversation_id',row_data->>'entity_id',row_data->>'file_id'
  );

  INSERT INTO lh_stimulus_events(
    workspace_id,event_type,source_table,source_id,operation,payload
  ) VALUES (
    resolved_workspace,
    TG_TABLE_NAME || '.' || lower(TG_OP),
    TG_TABLE_NAME,
    resolved_source_id,
    lower(TG_OP),
    jsonb_build_object(
      'changed_keys',changed_keys,
      'before',safe_old,
      'after',safe_new,
      'interaction_features',interaction_features
    )
  );

  IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
  RETURN NEW;
END;
$$;

COMMIT;
