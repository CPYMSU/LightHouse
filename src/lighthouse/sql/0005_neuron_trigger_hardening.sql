BEGIN;

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
  -- Referential-action cascades are database housekeeping, not environmental
  -- stimuli. More importantly, they must never create a new child row while its
  -- Workspace is being deleted.
  IF pg_trigger_depth() > 1 THEN
    IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
    RETURN NEW;
  END IF;

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

COMMIT;
