BEGIN;

CREATE TABLE IF NOT EXISTS lh_neurons (
  id SMALLINT PRIMARY KEY CHECK (id BETWEEN 1 AND 24),
  name TEXT NOT NULL UNIQUE,
  role TEXT NOT NULL,
  archetype TEXT NOT NULL,
  random_seed INTEGER NOT NULL,
  persona JSONB NOT NULL DEFAULT '{}'::jsonb,
  active BOOLEAN NOT NULL DEFAULT TRUE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS lh_neuron_vector_spaces (
  id TEXT PRIMARY KEY,
  neuron_id SMALLINT NOT NULL UNIQUE REFERENCES lh_neurons(id) ON DELETE CASCADE,
  namespace TEXT NOT NULL UNIQUE,
  stimulus_dimensions INTEGER NOT NULL DEFAULT 64 CHECK (stimulus_dimensions = 64),
  state_dimensions INTEGER NOT NULL DEFAULT 8 CHECK (state_dimensions = 8),
  metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS lh_stimulus_events (
  id BIGSERIAL PRIMARY KEY,
  workspace_id UUID NOT NULL REFERENCES lh_workspaces(id) ON DELETE CASCADE,
  event_type TEXT NOT NULL,
  source_table TEXT NOT NULL,
  source_id TEXT,
  operation TEXT NOT NULL CHECK (operation IN ('insert','update','delete','outcome','manual')),
  payload JSONB NOT NULL DEFAULT '{}'::jsonb,
  stimulus_vector DOUBLE PRECISION[],
  status TEXT NOT NULL DEFAULT 'pending'
    CHECK (status IN ('pending','processing','processed','failed','ignored')),
  attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
  error TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  processed_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_lh_stimulus_events_queue
  ON lh_stimulus_events(status,created_at,id);
CREATE INDEX IF NOT EXISTS idx_lh_stimulus_events_workspace
  ON lh_stimulus_events(workspace_id,created_at DESC);

CREATE TABLE IF NOT EXISTS lh_neuron_states (
  workspace_id UUID NOT NULL REFERENCES lh_workspaces(id) ON DELETE CASCADE,
  neuron_id SMALLINT NOT NULL REFERENCES lh_neurons(id) ON DELETE CASCADE,
  state_vector DOUBLE PRECISION[] NOT NULL,
  activation DOUBLE PRECISION NOT NULL DEFAULT 0,
  valence DOUBLE PRECISION NOT NULL DEFAULT 0,
  arousal DOUBLE PRECISION NOT NULL DEFAULT 0,
  confidence DOUBLE PRECISION NOT NULL DEFAULT 0,
  fatigue DOUBLE PRECISION NOT NULL DEFAULT 0,
  curiosity DOUBLE PRECISION NOT NULL DEFAULT 0,
  taste DOUBLE PRECISION NOT NULL DEFAULT 0,
  prediction DOUBLE PRECISION NOT NULL DEFAULT 0,
  version BIGINT NOT NULL DEFAULT 0,
  last_event_id BIGINT REFERENCES lh_stimulus_events(id) ON DELETE SET NULL,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY(workspace_id,neuron_id)
);
CREATE INDEX IF NOT EXISTS idx_lh_neuron_states_activation
  ON lh_neuron_states(workspace_id,activation DESC);

CREATE TABLE IF NOT EXISTS lh_neuron_weights (
  workspace_id UUID NOT NULL REFERENCES lh_workspaces(id) ON DELETE CASCADE,
  neuron_id SMALLINT NOT NULL REFERENCES lh_neurons(id) ON DELETE CASCADE,
  long_weights DOUBLE PRECISION[] NOT NULL,
  short_weights DOUBLE PRECISION[] NOT NULL,
  eligibility_trace DOUBLE PRECISION[] NOT NULL,
  threshold DOUBLE PRECISION NOT NULL DEFAULT 0.15,
  experience_count BIGINT NOT NULL DEFAULT 0,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY(workspace_id,neuron_id)
);

CREATE TABLE IF NOT EXISTS lh_neuron_edges (
  workspace_id UUID NOT NULL REFERENCES lh_workspaces(id) ON DELETE CASCADE,
  source_neuron_id SMALLINT NOT NULL REFERENCES lh_neurons(id) ON DELETE CASCADE,
  target_neuron_id SMALLINT NOT NULL REFERENCES lh_neurons(id) ON DELETE CASCADE,
  weight DOUBLE PRECISION NOT NULL DEFAULT 0,
  relation TEXT NOT NULL DEFAULT 'adaptive',
  version BIGINT NOT NULL DEFAULT 0,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY(workspace_id,source_neuron_id,target_neuron_id),
  CHECK (source_neuron_id <> target_neuron_id),
  CHECK (weight BETWEEN -1 AND 1)
);

CREATE TABLE IF NOT EXISTS lh_neuron_memories (
  id UUID PRIMARY KEY,
  vector_space_id TEXT NOT NULL REFERENCES lh_neuron_vector_spaces(id) ON DELETE CASCADE,
  workspace_id UUID NOT NULL REFERENCES lh_workspaces(id) ON DELETE CASCADE,
  source_event_id BIGINT REFERENCES lh_stimulus_events(id) ON DELETE SET NULL,
  memory_type TEXT NOT NULL DEFAULT 'episodic'
    CHECK (memory_type IN ('episodic','semantic','reflex','preference','social','outcome')),
  content TEXT NOT NULL DEFAULT '',
  semantic_vector DOUBLE PRECISION[],
  stimulus_vector DOUBLE PRECISION[],
  state_vector DOUBLE PRECISION[],
  affective_vector DOUBLE PRECISION[],
  outcome_vector DOUBLE PRECISION[],
  strength DOUBLE PRECISION NOT NULL DEFAULT 1 CHECK (strength >= 0),
  confidence DOUBLE PRECISION NOT NULL DEFAULT 0.5 CHECK (confidence BETWEEN 0 AND 1),
  metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_recalled_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_lh_neuron_memories_recent
  ON lh_neuron_memories(vector_space_id,workspace_id,created_at DESC);
CREATE INDEX IF NOT EXISTS idx_lh_neuron_memories_event
  ON lh_neuron_memories(source_event_id,vector_space_id);

CREATE TABLE IF NOT EXISTS lh_abm_runs (
  id UUID PRIMARY KEY,
  workspace_id UUID NOT NULL REFERENCES lh_workspaces(id) ON DELETE CASCADE,
  stimulus_event_id BIGINT NOT NULL REFERENCES lh_stimulus_events(id) ON DELETE CASCADE,
  status TEXT NOT NULL DEFAULT 'running'
    CHECK (status IN ('running','converged','bounded','failed')),
  rounds INTEGER NOT NULL DEFAULT 0 CHECK (rounds >= 0),
  converged BOOLEAN NOT NULL DEFAULT FALSE,
  max_delta DOUBLE PRECISION,
  dominant_neurons JSONB NOT NULL DEFAULT '[]'::jsonb,
  global_emotion JSONB NOT NULL DEFAULT '{}'::jsonb,
  state_vector DOUBLE PRECISION[],
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  completed_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_lh_abm_runs_workspace
  ON lh_abm_runs(workspace_id,created_at DESC);

CREATE TABLE IF NOT EXISTS lh_abm_steps (
  run_id UUID NOT NULL REFERENCES lh_abm_runs(id) ON DELETE CASCADE,
  round INTEGER NOT NULL,
  neuron_id SMALLINT NOT NULL REFERENCES lh_neurons(id) ON DELETE CASCADE,
  activation DOUBLE PRECISION NOT NULL,
  state_vector DOUBLE PRECISION[] NOT NULL,
  prediction DOUBLE PRECISION NOT NULL DEFAULT 0,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY(run_id,round,neuron_id)
);

INSERT INTO lh_neurons(id,name,role,archetype,random_seed,persona)
VALUES
  (1,'Sentinel','vigilance','警戒者',1101,'{"threat":0.8,"anomaly":0.65,"failure_probability":0.55}'::jsonb),
  (2,'Explorer','exploration','探索者',1102,'{"novelty":0.8,"information_gain":0.7,"opportunity":0.45}'::jsonb),
  (3,'Archivist','memory','記憶者',1103,'{"memory_strength_delta":0.75,"recall_success":0.65,"association_growth":0.6}'::jsonb),
  (4,'Skeptic','skepticism','懷疑者',1104,'{"contradiction":0.8,"uncertainty":0.55,"ambiguity":0.5}'::jsonb),
  (5,'Empath','empathy','共情者',1105,'{"emotional_intensity":0.7,"social_warmth":0.65,"relationship_relevance":0.6}'::jsonb),
  (6,'Planner','planning','計畫者',1106,'{"dependency_change":0.7,"priority":0.55,"long_horizon":0.6}'::jsonb),
  (7,'Actor','action','行動者',1107,'{"actionability":0.8,"urgency":0.55,"short_horizon":0.45}'::jsonb),
  (8,'Creator','creation','創造者',1108,'{"novelty":0.6,"opportunity":0.7,"complexity":0.35}'::jsonb),
  (9,'Conservator','stability','守成者',1109,'{"predictability":0.7,"reversibility":0.6,"anomaly":-0.35}'::jsonb),
  (10,'Optimizer','optimization','優化者',1110,'{"resource_cost":0.65,"progress_delta":0.55,"coherence":0.45}'::jsonb),
  (11,'Mediator','mediation','調和者',1111,'{"cooperation":0.7,"conflict":0.55,"coherence":0.55}'::jsonb),
  (12,'Challenger','challenge','挑戰者',1112,'{"contradiction":0.65,"conflict":0.55,"novelty":0.35}'::jsonb),
  (13,'Strategist','strategy','戰略者',1113,'{"long_horizon":0.8,"goal_relevance":0.65,"opportunity":0.5}'::jsonb),
  (14,'Craftsperson','implementation','工匠',1114,'{"actionability":0.7,"controllability":0.6,"procedural_match":0.6}'::jsonb),
  (15,'Auditor','audit','審計者',1115,'{"causal_strength":0.7,"coherence":0.65,"contradiction":0.55}'::jsonb),
  (16,'Guardian','protection','守護者',1116,'{"reversibility":0.75,"threat":0.6,"loss_delta":0.55}'::jsonb),
  (17,'Interpreter','explanation','解釋者',1117,'{"semantic_density":0.7,"coherence":0.65,"complexity":0.4}'::jsonb),
  (18,'Forecaster','prediction','預測者',1118,'{"trend":0.7,"predictability":0.6,"acceleration":0.55}'::jsonb),
  (19,'Goalkeeper','goal-alignment','目標守護者',1119,'{"goal_relevance":0.8,"completion_signal":0.65,"blocked_signal":0.55}'::jsonb),
  (20,'Social Observer','social','社會觀察者',1120,'{"relationship_relevance":0.7,"authority":0.55,"trust_delta":0.5}'::jsonb),
  (21,'Pattern Hunter','pattern','模式獵手',1121,'{"repetition":0.7,"recurrence":0.65,"procedural_match":0.55}'::jsonb),
  (22,'Restorer','recovery','復原者',1122,'{"recoverability":0.8,"failure_probability":0.55,"controllability":0.45}'::jsonb),
  (23,'Integrator','integration','整合者',1123,'{"coherence":0.7,"association_growth":0.6,"causal_strength":0.5}'::jsonb),
  (24,'Metacognitive Observer','metacognition','元觀察者',1124,'{"uncertainty":0.55,"conflict_level":0.6,"predictability":0.5}'::jsonb)
ON CONFLICT (id) DO UPDATE SET
  name=EXCLUDED.name,
  role=EXCLUDED.role,
  archetype=EXCLUDED.archetype,
  random_seed=EXCLUDED.random_seed,
  persona=EXCLUDED.persona,
  active=TRUE,
  updated_at=now();

INSERT INTO lh_neuron_vector_spaces(id,neuron_id,namespace,metadata)
SELECT
  'neuron-' || lpad(id::text,2,'0'),
  id,
  'lighthouse.neuron.' || lpad(id::text,2,'0'),
  jsonb_build_object('logical_database',TRUE,'role',role,'archetype',archetype)
FROM lh_neurons
ON CONFLICT (neuron_id) DO UPDATE SET
  namespace=EXCLUDED.namespace,
  metadata=lh_neuron_vector_spaces.metadata || EXCLUDED.metadata,
  updated_at=now();

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
BEGIN
  IF TG_OP <> 'INSERT' THEN
    old_row := to_jsonb(OLD);
  END IF;
  IF TG_OP <> 'DELETE' THEN
    new_row := to_jsonb(NEW);
  END IF;
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

  IF resolved_workspace IS NULL THEN
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

  safe_old := old_row - ARRAY[
    'content','search_text','search_vector','payload','result','envelope',
    'value','evidence','config'
  ]::text[];
  safe_new := new_row - ARRAY[
    'content','search_text','search_vector','payload','result','envelope',
    'value','evidence','config'
  ]::text[];

  resolved_source_id := COALESCE(
    row_data->>'id',
    row_data->>'operation_id',
    row_data->>'run_id',
    row_data->>'conversation_id',
    row_data->>'entity_id',
    row_data->>'file_id'
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
      'after',safe_new
    )
  );

  IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_lh_messages_neuron_stimulus ON lh_messages;
CREATE TRIGGER trg_lh_messages_neuron_stimulus
AFTER INSERT OR UPDATE OR DELETE ON lh_messages
FOR EACH ROW EXECUTE FUNCTION lh_capture_neuron_stimulus();

DROP TRIGGER IF EXISTS trg_lh_memory_tasks_neuron_stimulus ON lh_memory_tasks;
CREATE TRIGGER trg_lh_memory_tasks_neuron_stimulus
AFTER INSERT OR UPDATE OR DELETE ON lh_memory_tasks
FOR EACH ROW EXECUTE FUNCTION lh_capture_neuron_stimulus();

DROP TRIGGER IF EXISTS trg_lh_files_neuron_stimulus ON lh_files;
CREATE TRIGGER trg_lh_files_neuron_stimulus
AFTER INSERT OR UPDATE OR DELETE ON lh_files
FOR EACH ROW EXECUTE FUNCTION lh_capture_neuron_stimulus();

DROP TRIGGER IF EXISTS trg_lh_operation_receipts_neuron_stimulus ON lh_operation_receipts;
CREATE TRIGGER trg_lh_operation_receipts_neuron_stimulus
AFTER INSERT OR UPDATE OR DELETE ON lh_operation_receipts
FOR EACH ROW EXECUTE FUNCTION lh_capture_neuron_stimulus();

DROP TRIGGER IF EXISTS trg_lh_conversation_summaries_neuron_stimulus ON lh_conversation_summaries;
CREATE TRIGGER trg_lh_conversation_summaries_neuron_stimulus
AFTER INSERT OR UPDATE OR DELETE ON lh_conversation_summaries
FOR EACH ROW EXECUTE FUNCTION lh_capture_neuron_stimulus();

DROP TRIGGER IF EXISTS trg_lh_world_entities_neuron_stimulus ON lh_world_entities;
CREATE TRIGGER trg_lh_world_entities_neuron_stimulus
AFTER INSERT OR UPDATE OR DELETE ON lh_world_entities
FOR EACH ROW EXECUTE FUNCTION lh_capture_neuron_stimulus();

DROP TRIGGER IF EXISTS trg_lh_world_facts_neuron_stimulus ON lh_world_facts;
CREATE TRIGGER trg_lh_world_facts_neuron_stimulus
AFTER INSERT OR UPDATE OR DELETE ON lh_world_facts
FOR EACH ROW EXECUTE FUNCTION lh_capture_neuron_stimulus();

DROP TRIGGER IF EXISTS trg_lh_world_relations_neuron_stimulus ON lh_world_relations;
CREATE TRIGGER trg_lh_world_relations_neuron_stimulus
AFTER INSERT OR UPDATE OR DELETE ON lh_world_relations
FOR EACH ROW EXECUTE FUNCTION lh_capture_neuron_stimulus();

DROP TRIGGER IF EXISTS trg_lh_world_inferences_neuron_stimulus ON lh_world_inferences;
CREATE TRIGGER trg_lh_world_inferences_neuron_stimulus
AFTER INSERT OR UPDATE OR DELETE ON lh_world_inferences
FOR EACH ROW EXECUTE FUNCTION lh_capture_neuron_stimulus();

DROP TRIGGER IF EXISTS trg_lh_world_uncertainties_neuron_stimulus ON lh_world_uncertainties;
CREATE TRIGGER trg_lh_world_uncertainties_neuron_stimulus
AFTER INSERT OR UPDATE OR DELETE ON lh_world_uncertainties
FOR EACH ROW EXECUTE FUNCTION lh_capture_neuron_stimulus();

COMMIT;
