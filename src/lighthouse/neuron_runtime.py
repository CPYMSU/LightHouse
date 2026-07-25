from __future__ import annotations

import json
import threading
from typing import Any
from uuid import uuid4

from .neuron_model import (
    MemoryTrace,
    NeuronField,
    NeuronState,
    StimulusVector,
    VECTOR_SIZE,
    encode_database_change,
)


def _json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _as_float_list(value: Any, *, size: int | None = None) -> list[float]:
    result = [] if value is None else [float(item) for item in value]
    if size is not None and len(result) != size:
        raise ValueError(f"expected vector of length {size}, received {len(result)}")
    return result


class PostgresNeuronRuntime:
    """Durable 24-neuron reflex runtime backed by PostgreSQL vector spaces."""

    def __init__(self, dsn: str):
        if not dsn:
            raise ValueError("PostgreSQL DSN is required")
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("Neuron runtime requires psycopg") from exc
        self._psycopg = psycopg
        self._dict_row = dict_row
        self.dsn = dsn

    def _connect(self):
        return self._psycopg.connect(self.dsn, row_factory=self._dict_row)

    def emit_event(
        self,
        *,
        workspace_id: str,
        event_type: str,
        source_table: str,
        operation: str,
        source_id: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        stimulus = encode_database_change(
            event_type=event_type,
            operation=operation,
            payload=payload,
        )
        with self._connect() as connection:
            row = connection.execute(
                """INSERT INTO lh_stimulus_events(
                       workspace_id,event_type,source_table,source_id,operation,
                       payload,stimulus_vector,status
                   ) VALUES (%s,%s,%s,%s,%s,%s::jsonb,%s,'pending')
                   RETURNING *""",
                (
                    workspace_id,
                    event_type,
                    source_table,
                    source_id,
                    operation,
                    _json(payload or {}),
                    list(stimulus.values),
                ),
            ).fetchone()
        return self._event_dict(row)

    def process_pending(self, *, limit: int = 4) -> list[dict[str, Any]]:
        processed: list[dict[str, Any]] = []
        for _ in range(max(1, min(int(limit), 32))):
            event = self._claim_event()
            if not event:
                break
            try:
                processed.append(self._process_claimed(event))
            except Exception as exc:
                self._mark_failed(int(event["id"]), str(exc))
        return processed

    def process_event(self, event_id: int) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute(
                """UPDATE lh_stimulus_events
                   SET status='processing',attempts=attempts+1,error=NULL
                   WHERE id=%s AND status IN ('pending','failed')
                   RETURNING *""",
                (int(event_id),),
            ).fetchone()
        if not row:
            with self._connect() as connection:
                row = connection.execute(
                    "SELECT * FROM lh_stimulus_events WHERE id=%s",
                    (int(event_id),),
                ).fetchone()
            if not row:
                raise KeyError("stimulus event not found")
            if row["status"] == "processed":
                return self._event_dict(row)
            raise ValueError(f"stimulus event cannot be processed from {row['status']}")
        try:
            return self._process_claimed(row)
        except Exception as exc:
            self._mark_failed(int(event_id), str(exc))
            raise

    def record_outcome(
        self,
        *,
        event_id: int,
        reward: float,
        outcome: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        with self._connect() as connection:
            event = connection.execute(
                "SELECT * FROM lh_stimulus_events WHERE id=%s",
                (int(event_id),),
            ).fetchone()
            if not event:
                raise KeyError("stimulus event not found")
            workspace_id = str(event["workspace_id"])
            field = self._load_field(workspace_id, connection=connection)
            stimulus = self._stimulus_for_event(event)
            field.last_stimulus = stimulus
            field.apply_outcome(reward=reward)
            self._persist_weights_and_edges(
                connection,
                workspace_id=workspace_id,
                field=field,
            )
            outcome_event = connection.execute(
                """INSERT INTO lh_stimulus_events(
                       workspace_id,event_type,source_table,source_id,operation,
                       payload,stimulus_vector,status,processed_at
                   ) VALUES (%s,'neuron.outcome','lh_stimulus_events',%s,'outcome',
                             %s::jsonb,%s,'processed',now())
                   RETURNING *""",
                (
                    workspace_id,
                    str(event_id),
                    _json(
                        {
                            "reward": max(-1.0, min(float(reward), 1.0)),
                            "outcome": outcome or {},
                        }
                    ),
                    list(
                        encode_database_change(
                            event_type="neuron.outcome",
                            operation="outcome",
                            payload={"reward": reward, **(outcome or {})},
                        ).values
                    ),
                ),
            ).fetchone()
            for neuron in field.neurons:
                connection.execute(
                    """INSERT INTO lh_neuron_memories(
                           id,vector_space_id,workspace_id,source_event_id,memory_type,
                           content,stimulus_vector,state_vector,affective_vector,
                           outcome_vector,strength,confidence,metadata
                       ) VALUES (%s,%s,%s,%s,'outcome',%s,%s,%s,%s,%s,%s,%s,%s::jsonb)""",
                    (
                        str(uuid4()),
                        f"neuron-{neuron.archetype.neuron_id:02d}",
                        workspace_id,
                        int(outcome_event["id"]),
                        f"Outcome for stimulus event {event_id}",
                        list(stimulus.values),
                        list(neuron.state.vector()),
                        [
                            neuron.state.valence,
                            neuron.state.arousal,
                            neuron.state.curiosity,
                            neuron.state.taste,
                        ],
                        [max(-1.0, min(float(reward), 1.0)), neuron.state.prediction],
                        1.0 + max(0.0, float(reward)) * 0.5,
                        max(0.0, min(1.0, abs(neuron.state.activation))),
                        _json({"source_event_id": event_id, "outcome": outcome or {}}),
                    ),
                )
        return self._event_dict(outcome_event)

    def current_summary(self, *, workspace_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT s.*,n.name,n.role
                   FROM lh_neuron_states s
                   JOIN lh_neurons n ON n.id=s.neuron_id
                   WHERE s.workspace_id=%s
                   ORDER BY abs(s.activation) DESC,n.id""",
                (workspace_id,),
            ).fetchall()
            latest_run = connection.execute(
                """SELECT * FROM lh_abm_runs
                   WHERE workspace_id=%s
                   ORDER BY created_at DESC LIMIT 1""",
                (workspace_id,),
            ).fetchone()
        return {
            "workspace_id": workspace_id,
            "dominant_neurons": [
                {
                    "neuron_id": int(row["neuron_id"]),
                    "name": row["name"],
                    "role": row["role"],
                    "activation": float(row["activation"]),
                    "valence": float(row["valence"]),
                    "confidence": float(row["confidence"]),
                    "curiosity": float(row["curiosity"]),
                    "taste": float(row["taste"]),
                }
                for row in rows[:8]
            ],
            "latest_abm_run": self._run_dict(latest_run) if latest_run else None,
        }

    def _claim_event(self) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                """WITH candidate AS (
                     SELECT id FROM lh_stimulus_events
                     WHERE status='pending'
                     ORDER BY created_at,id
                     FOR UPDATE SKIP LOCKED LIMIT 1
                   )
                   UPDATE lh_stimulus_events event
                   SET status='processing',attempts=attempts+1,error=NULL
                   FROM candidate
                   WHERE event.id=candidate.id
                   RETURNING event.*"""
            ).fetchone()
        return dict(row) if row else None

    def _process_claimed(self, event: dict[str, Any]) -> dict[str, Any]:
        workspace_id = str(event["workspace_id"])
        stimulus = self._stimulus_for_event(event)
        run_id = str(uuid4())

        with self._connect() as connection:
            field = self._load_field(workspace_id, connection=connection)
            memories = self._load_memories(
                connection,
                workspace_id=workspace_id,
                limit_per_neuron=32,
            )
            result = field.run(stimulus, memories=memories)
            connection.execute(
                """INSERT INTO lh_abm_runs(
                       id,workspace_id,stimulus_event_id,status,rounds,converged,
                       max_delta,dominant_neurons,global_emotion,state_vector,
                       completed_at
                   ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s,now())""",
                (
                    run_id,
                    workspace_id,
                    int(event["id"]),
                    "converged" if result.converged else "bounded",
                    result.rounds,
                    result.converged,
                    result.max_delta,
                    _json(list(result.dominant_neurons)),
                    _json(result.emotions),
                    list(result.state_vector),
                ),
            )
            self._persist_field(
                connection,
                workspace_id=workspace_id,
                event=event,
                stimulus=stimulus,
                field=field,
                run_id=run_id,
                final_round=result.rounds,
            )
            row = connection.execute(
                """UPDATE lh_stimulus_events
                   SET status='processed',stimulus_vector=%s,error=NULL,processed_at=now()
                   WHERE id=%s
                   RETURNING *""",
                (list(stimulus.values), int(event["id"])),
            ).fetchone()
        value = self._event_dict(row)
        value["abm"] = result.as_dict()
        value["abm"]["run_id"] = run_id
        return value

    def _stimulus_for_event(self, event: dict[str, Any]) -> StimulusVector:
        vector = event.get("stimulus_vector")
        if vector:
            return StimulusVector(tuple(float(item) for item in vector))
        return encode_database_change(
            event_type=str(event.get("event_type") or "database.change"),
            operation=str(event.get("operation") or "update"),
            payload=event.get("payload") or {},
        )

    def _load_field(self, workspace_id: str, *, connection) -> NeuronField:
        field = NeuronField()
        weights = connection.execute(
            "SELECT * FROM lh_neuron_weights WHERE workspace_id=%s ORDER BY neuron_id",
            (workspace_id,),
        ).fetchall()
        states = connection.execute(
            "SELECT * FROM lh_neuron_states WHERE workspace_id=%s ORDER BY neuron_id",
            (workspace_id,),
        ).fetchall()
        edges = connection.execute(
            """SELECT * FROM lh_neuron_edges
               WHERE workspace_id=%s ORDER BY source_neuron_id,target_neuron_id""",
            (workspace_id,),
        ).fetchall()

        by_id = {neuron.archetype.neuron_id: neuron for neuron in field.neurons}
        for row in weights:
            neuron = by_id[int(row["neuron_id"])]
            neuron.long_weights = _as_float_list(row["long_weights"], size=VECTOR_SIZE)
            neuron.short_weights = _as_float_list(row["short_weights"], size=VECTOR_SIZE)
            neuron.eligibility_trace = _as_float_list(
                row["eligibility_trace"], size=VECTOR_SIZE
            )
            neuron.threshold = float(row["threshold"])
            neuron.experience_count = int(row["experience_count"])

        for row in states:
            neuron = by_id[int(row["neuron_id"])]
            neuron.state = NeuronState(
                activation=float(row["activation"]),
                valence=float(row["valence"]),
                arousal=float(row["arousal"]),
                confidence=float(row["confidence"]),
                fatigue=float(row["fatigue"]),
                curiosity=float(row["curiosity"]),
                taste=float(row["taste"]),
                prediction=float(row["prediction"]),
                version=int(row["version"]),
            )

        for row in edges:
            source = int(row["source_neuron_id"]) - 1
            target = int(row["target_neuron_id"]) - 1
            field.relations[source][target] = float(row["weight"])
        return field

    def _load_memories(
        self,
        connection,
        *,
        workspace_id: str,
        limit_per_neuron: int,
    ) -> dict[int, list[MemoryTrace]]:
        rows = connection.execute(
            """SELECT space.neuron_id,m.stimulus_vector,m.strength,
                      m.affective_vector,m.outcome_vector
               FROM lh_neuron_vector_spaces space
               JOIN LATERAL (
                 SELECT stimulus_vector,strength,affective_vector,outcome_vector
                 FROM lh_neuron_memories memory
                 WHERE memory.vector_space_id=space.id
                   AND memory.workspace_id=%s
                   AND memory.stimulus_vector IS NOT NULL
                 ORDER BY memory.created_at DESC
                 LIMIT %s
               ) m ON TRUE
               ORDER BY space.neuron_id""",
            (workspace_id, max(1, min(int(limit_per_neuron), 128))),
        ).fetchall()
        grouped: dict[int, list[MemoryTrace]] = {}
        for row in rows:
            affect = _as_float_list(row["affective_vector"])
            outcome = _as_float_list(row["outcome_vector"])
            grouped.setdefault(int(row["neuron_id"]), []).append(
                MemoryTrace(
                    vector=StimulusVector(
                        tuple(_as_float_list(row["stimulus_vector"], size=VECTOR_SIZE))
                    ),
                    strength=float(row["strength"]),
                    valence=affect[0] if affect else 0.0,
                    reward=outcome[0] if outcome else 0.0,
                )
            )
        return grouped

    def _persist_field(
        self,
        connection,
        *,
        workspace_id: str,
        event: dict[str, Any],
        stimulus: StimulusVector,
        field: NeuronField,
        run_id: str,
        final_round: int,
    ) -> None:
        for neuron in field.neurons:
            state_vector = list(neuron.state.vector())
            connection.execute(
                """INSERT INTO lh_neuron_states(
                       workspace_id,neuron_id,state_vector,activation,valence,
                       arousal,confidence,fatigue,curiosity,taste,prediction,
                       version,last_event_id
                   ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (workspace_id,neuron_id) DO UPDATE SET
                     state_vector=EXCLUDED.state_vector,
                     activation=EXCLUDED.activation,
                     valence=EXCLUDED.valence,
                     arousal=EXCLUDED.arousal,
                     confidence=EXCLUDED.confidence,
                     fatigue=EXCLUDED.fatigue,
                     curiosity=EXCLUDED.curiosity,
                     taste=EXCLUDED.taste,
                     prediction=EXCLUDED.prediction,
                     version=EXCLUDED.version,
                     last_event_id=EXCLUDED.last_event_id,
                     updated_at=now()""",
                (
                    workspace_id,
                    neuron.archetype.neuron_id,
                    state_vector,
                    neuron.state.activation,
                    neuron.state.valence,
                    neuron.state.arousal,
                    neuron.state.confidence,
                    neuron.state.fatigue,
                    neuron.state.curiosity,
                    neuron.state.taste,
                    neuron.state.prediction,
                    neuron.state.version,
                    int(event["id"]),
                ),
            )
            connection.execute(
                """INSERT INTO lh_abm_steps(
                       run_id,round,neuron_id,activation,state_vector,prediction
                   ) VALUES (%s,%s,%s,%s,%s,%s)""",
                (
                    run_id,
                    final_round,
                    neuron.archetype.neuron_id,
                    neuron.state.activation,
                    state_vector,
                    neuron.state.prediction,
                ),
            )
            connection.execute(
                """INSERT INTO lh_neuron_memories(
                       id,vector_space_id,workspace_id,source_event_id,memory_type,
                       content,stimulus_vector,state_vector,affective_vector,
                       outcome_vector,strength,confidence,metadata
                   ) VALUES (%s,%s,%s,%s,'episodic',%s,%s,%s,%s,%s,%s,%s,%s::jsonb)""",
                (
                    str(uuid4()),
                    f"neuron-{neuron.archetype.neuron_id:02d}",
                    workspace_id,
                    int(event["id"]),
                    (
                        f"{event.get('event_type')} "
                        f"{event.get('source_table')}:{event.get('source_id') or ''}"
                    ).strip(),
                    list(stimulus.values),
                    state_vector,
                    [
                        neuron.state.valence,
                        neuron.state.arousal,
                        neuron.state.curiosity,
                        neuron.state.taste,
                    ],
                    [0.0, neuron.state.prediction],
                    1.0,
                    max(0.0, min(1.0, abs(neuron.state.activation))),
                    _json(
                        {
                            "operation": event.get("operation"),
                            "source_table": event.get("source_table"),
                            "source_id": event.get("source_id"),
                            "payload": event.get("payload") or {},
                        }
                    ),
                ),
            )

    def _persist_weights_and_edges(
        self,
        connection,
        *,
        workspace_id: str,
        field: NeuronField,
    ) -> None:
        for neuron in field.neurons:
            connection.execute(
                """INSERT INTO lh_neuron_weights(
                       workspace_id,neuron_id,long_weights,short_weights,
                       eligibility_trace,threshold,experience_count
                   ) VALUES (%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (workspace_id,neuron_id) DO UPDATE SET
                     long_weights=EXCLUDED.long_weights,
                     short_weights=EXCLUDED.short_weights,
                     eligibility_trace=EXCLUDED.eligibility_trace,
                     threshold=EXCLUDED.threshold,
                     experience_count=EXCLUDED.experience_count,
                     updated_at=now()""",
                (
                    workspace_id,
                    neuron.archetype.neuron_id,
                    list(neuron.long_weights),
                    list(neuron.short_weights),
                    list(neuron.eligibility_trace),
                    neuron.threshold,
                    neuron.experience_count,
                ),
            )
        for source in range(24):
            for target in range(24):
                if source == target:
                    continue
                connection.execute(
                    """INSERT INTO lh_neuron_edges(
                           workspace_id,source_neuron_id,target_neuron_id,weight,
                           relation,version
                       ) VALUES (%s,%s,%s,%s,'adaptive',1)
                       ON CONFLICT (workspace_id,source_neuron_id,target_neuron_id)
                       DO UPDATE SET
                         weight=EXCLUDED.weight,
                         version=lh_neuron_edges.version+1,
                         updated_at=now()""",
                    (
                        workspace_id,
                        source + 1,
                        target + 1,
                        field.relations[source][target],
                    ),
                )

    def _mark_failed(self, event_id: int, error: str) -> None:
        with self._connect() as connection:
            connection.execute(
                """UPDATE lh_stimulus_events
                   SET status='failed',error=%s,processed_at=now()
                   WHERE id=%s""",
                (str(error)[:4000], int(event_id)),
            )

    @staticmethod
    def _event_dict(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": int(row["id"]),
            "workspace_id": str(row["workspace_id"]),
            "event_type": row["event_type"],
            "source_table": row["source_table"],
            "source_id": row.get("source_id"),
            "operation": row["operation"],
            "payload": row.get("payload") or {},
            "stimulus_vector": list(row.get("stimulus_vector") or []),
            "status": row["status"],
            "attempts": int(row["attempts"]),
            "error": row.get("error"),
            "created_at": row["created_at"].isoformat(),
            "processed_at": (
                row["processed_at"].isoformat() if row.get("processed_at") else None
            ),
        }

    @staticmethod
    def _run_dict(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": str(row["id"]),
            "stimulus_event_id": int(row["stimulus_event_id"]),
            "status": row["status"],
            "rounds": int(row["rounds"]),
            "converged": bool(row["converged"]),
            "max_delta": float(row["max_delta"] or 0.0),
            "dominant_neurons": row.get("dominant_neurons") or [],
            "global_emotion": row.get("global_emotion") or {},
            "created_at": row["created_at"].isoformat(),
            "completed_at": (
                row["completed_at"].isoformat() if row.get("completed_at") else None
            ),
        }


class NeuronReflexWorker:
    """Non-model worker that continuously turns data changes into reflex states."""

    def __init__(
        self,
        runtime: PostgresNeuronRuntime,
        *,
        poll_interval: float = 0.35,
        batch_size: int = 4,
    ):
        self.runtime = runtime
        self.poll_interval = max(0.1, min(float(poll_interval), 5.0))
        self.batch_size = max(1, min(int(batch_size), 16))
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop,
            name="lighthouse-neuron-reflex",
            daemon=True,
        )
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(max(0.0, min(float(timeout), 10.0)))

    def _loop(self) -> None:
        while not self._stop.is_set():
            did_work = False
            try:
                did_work = bool(self.runtime.process_pending(limit=self.batch_size))
            except Exception:
                did_work = False
            if not did_work:
                self._stop.wait(self.poll_interval)
