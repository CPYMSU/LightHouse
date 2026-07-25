BEGIN;

CREATE OR REPLACE FUNCTION lh_assign_operation_event_sequence()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
  IF NEW.sequence IS NULL THEN
    PERFORM 1 FROM lh_operations WHERE id=NEW.operation_id FOR UPDATE;
    SELECT COALESCE(MAX(sequence),0)+1
      INTO NEW.sequence
      FROM lh_operation_events
      WHERE operation_id=NEW.operation_id;
  END IF;
  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_lh_operation_events_sequence ON lh_operation_events;
CREATE TRIGGER trg_lh_operation_events_sequence
BEFORE INSERT ON lh_operation_events
FOR EACH ROW
EXECUTE FUNCTION lh_assign_operation_event_sequence();

COMMIT;
