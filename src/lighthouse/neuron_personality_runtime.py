from __future__ import annotations

import secrets
from typing import Any, Mapping, Sequence
from uuid import uuid4

from .neuron_model import (
    NeuronField,
    NeuronState,
    StimulusVector,
    VECTOR_SIZE,
    _clip,
    cosine_similarity,
)
from .neuron_personality import (
    ATTRACTOR_SIMILARITY,
    CHECKPOINT_INTERVAL,
    IDENTITY_SCHEMA_VERSION,
    NEURON_COUNT,
    STATE_VECTOR_SIZE,
    _clip01,
    PersistentNeuronField,
    PersonalityLearningResult,
    apply_identity_seed,
    apply_persistent_learning,
    derive_cognitive_control,
    detect_circuits,
    identity_signature,
)
from .neuron_runtime import PostgresNeuronRuntime, _as_float_list, _json


class PersistentPersonalityMixin:
    """PostgreSQL-backed learning lifecycle for cross-session personality evolution."""

    def _ensure_identity(self, workspace_id: str, *, connection) -> dict[str, Any]:
        row = connection.execute(
            "SELECT * FROM lh_neuron_identities WHERE workspace_id=%s",
            (workspace_id,),
        ).fetchone()
        if row:
            return dict(row)
        seed = secrets.randbits(62)
        row = connection.execute(
            """INSERT INTO lh_neuron_identities(
                   workspace_id,identity_seed,generation,schema_version,birth_snapshot
               ) VALUES (%s,%s,1,%s,%s::jsonb)
               ON CONFLICT (workspace_id) DO NOTHING
               RETURNING *""",
            (
                workspace_id,
                seed,
                IDENTITY_SCHEMA_VERSION,
                _json(
                    {
                        "neuron_count": NEURON_COUNT,
                        "stimulus_dimensions": VECTOR_SIZE,
                        "state_dimensions": 8,
                        "formation": "experience_driven",
                        "prompt_persona": False,
                    }
                ),
            ),
        ).fetchone()
        if row:
            return dict(row)
        row = connection.execute(
            "SELECT * FROM lh_neuron_identities WHERE workspace_id=%s",
            (workspace_id,),
        ).fetchone()
        return dict(row)

    def _load_field(self, workspace_id: str, *, connection) -> PersistentNeuronField:
        identity = self._ensure_identity(workspace_id, connection=connection)
        relation_seed = int(identity["identity_seed"]) % (2**31 - 1)
        field = PersistentNeuronField(relation_seed=relation_seed)
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
        if not weights and not edges:
            apply_identity_seed(field, int(identity["identity_seed"]))

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

    def _edge_metadata(
        self, connection, *, workspace_id: str
    ) -> dict[tuple[int, int], dict[str, Any]]:
        rows = connection.execute(
            """SELECT source_neuron_id,target_neuron_id,plasticity,permanence,
                      usage_count,success_count,failure_count,dormant
               FROM lh_neuron_edges WHERE workspace_id=%s""",
            (workspace_id,),
        ).fetchall()
        return {
            (int(row["source_neuron_id"]), int(row["target_neuron_id"])): dict(row)
            for row in rows
        }

    def _process_claimed(self, event: dict[str, Any]) -> dict[str, Any]:
        result = PostgresNeuronRuntime._process_claimed(self, event)
        stimulus = StimulusVector(
            tuple(float(value) for value in result["stimulus_vector"])
        )
        reward = _clip(stimulus.get("reward_delta") - stimulus.get("loss_delta"))
        try:
            learning = self._learn_and_persist(
                event_id=int(event["id"]),
                reward=reward,
                outcome={"automatic": True, "source": event.get("event_type")},
                run_id=str(result["abm"]["run_id"]),
                create_outcome_event=abs(reward) >= 0.05,
            )
            result["learning"] = learning
            result["cognitive_control"] = learning.get("cognitive_control")
            result["identity"] = learning.get("identity")
        except Exception as exc:
            result["learning"] = {
                "applied": False,
                "reward": reward,
                "persistent": False,
                "error": str(exc),
            }
        return result

    def record_outcome(
        self,
        *,
        event_id: int,
        reward: float,
        outcome: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        with self._connect() as connection:
            source = connection.execute(
                "SELECT workspace_id FROM lh_stimulus_events WHERE id=%s",
                (int(event_id),),
            ).fetchone()
            if not source:
                raise KeyError("stimulus event not found")
            run = connection.execute(
                """SELECT id FROM lh_abm_runs
                   WHERE stimulus_event_id=%s ORDER BY created_at DESC LIMIT 1""",
                (int(event_id),),
            ).fetchone()
        learned = self._learn_and_persist(
            event_id=int(event_id),
            reward=_clip(reward),
            outcome=outcome or {},
            run_id=str(run["id"]) if run else None,
            create_outcome_event=True,
        )
        outcome_event = learned.get("outcome_event")
        if not outcome_event:
            raise RuntimeError("outcome event was not persisted")
        return outcome_event

    def _learn_and_persist(
        self,
        *,
        event_id: int,
        reward: float,
        outcome: Mapping[str, Any],
        run_id: str | None,
        create_outcome_event: bool,
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
            stimulus = self._stimulus_for_event(dict(event))
            metadata = self._edge_metadata(connection, workspace_id=workspace_id)
            learned = apply_persistent_learning(
                field,
                stimulus,
                global_reward=reward,
                outcome=outcome,
                edge_metadata=metadata,
            )
            PostgresNeuronRuntime._persist_weights_and_edges(
                self,
                connection,
                workspace_id=workspace_id,
                field=field,
            )
            self._persist_edge_learning(
                connection,
                workspace_id=workspace_id,
                event_id=event_id,
                learned=learned,
            )
            self._persist_neuron_learning(
                connection,
                workspace_id=workspace_id,
                event_id=event_id,
                field=field,
                learned=learned,
                outcome=outcome,
            )
            outcome_event = None
            if create_outcome_event:
                outcome_event = self._create_outcome_event_and_memories(
                    connection,
                    workspace_id=workspace_id,
                    event_id=event_id,
                    stimulus=stimulus,
                    field=field,
                    reward=reward,
                    outcome=outcome,
                )
            attractor = self._persist_attractor(
                connection,
                workspace_id=workspace_id,
                run_id=run_id,
                field=field,
                reward=reward,
            )
            circuits = detect_circuits(field)
            self._persist_circuits(
                connection,
                workspace_id=workspace_id,
                circuits=circuits,
                reward=reward,
            )
            dominant_circuit = circuits[0]["signature"] if circuits else None
            control = derive_cognitive_control(
                field, dominant_circuit=dominant_circuit
            ).as_dict()
            self._persist_control(
                connection,
                workspace_id=workspace_id,
                run_id=run_id,
                control=control,
            )
            identity = self._advance_identity(
                connection,
                workspace_id=workspace_id,
                event_id=event_id,
                signature=learned.identity_signature,
            )
            if int(identity["event_count"]) % CHECKPOINT_INTERVAL == 0:
                self._persist_checkpoint(
                    connection,
                    workspace_id=workspace_id,
                    identity=identity,
                    field=field,
                )

        return {
            "applied": True,
            "persistent": True,
            "cross_session": True,
            "reward": learned.global_reward,
            "local_rewards": [credit.as_dict() for credit in learned.credits],
            "identity": {
                "generation": int(identity["generation"]),
                "event_count": int(identity["event_count"]),
                "schema_version": int(identity["schema_version"]),
            },
            "attractor": attractor,
            "circuits": circuits,
            "cognitive_control": control,
            "outcome_event": (
                self._event_dict(outcome_event) if outcome_event is not None else None
            ),
            "outcome_event_id": (
                int(outcome_event["id"]) if outcome_event is not None else None
            ),
        }

    def _persist_edge_learning(
        self,
        connection,
        *,
        workspace_id: str,
        event_id: int,
        learned: PersonalityLearningResult,
    ) -> None:
        for edge in learned.edges:
            changed = abs(edge.new_weight - edge.old_weight)
            sign_changed = (edge.old_weight < 0 <= edge.new_weight) or (
                edge.old_weight > 0 >= edge.new_weight
            )
            connection.execute(
                """UPDATE lh_neuron_edges SET
                     weight=%s,edge_type=%s,plasticity=%s,permanence=%s,
                     usage_count=%s,success_count=%s,failure_count=%s,dormant=%s,
                     last_activated_at=CASE WHEN %s THEN now() ELSE last_activated_at END,
                     last_modified_at=now()
                   WHERE workspace_id=%s AND source_neuron_id=%s AND target_neuron_id=%s""",
                (
                    edge.new_weight,
                    edge.edge_type,
                    edge.plasticity,
                    edge.permanence,
                    edge.usage_count,
                    edge.success_count,
                    edge.failure_count,
                    edge.dormant,
                    abs(edge.coactivation) > 0.05,
                    workspace_id,
                    edge.source_neuron_id,
                    edge.target_neuron_id,
                ),
            )
            if changed >= 0.01 or sign_changed or edge.dormant:
                connection.execute(
                    """INSERT INTO lh_neuron_edge_history(
                           workspace_id,source_neuron_id,target_neuron_id,source_event_id,
                           old_weight,new_weight,local_reward,prediction_error,reason
                       ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (
                        workspace_id,
                        edge.source_neuron_id,
                        edge.target_neuron_id,
                        event_id,
                        edge.old_weight,
                        edge.new_weight,
                        learned.credits[edge.target_neuron_id - 1].local_reward,
                        edge.target_prediction_error,
                        "dormant" if edge.dormant else "plasticity_update",
                    ),
                )

    def _persist_neuron_learning(
        self,
        connection,
        *,
        workspace_id: str,
        event_id: int,
        field: NeuronField,
        learned: PersonalityLearningResult,
        outcome: Mapping[str, Any],
    ) -> None:
        incoming_inhibition = [0.0] * NEURON_COUNT
        for source in range(NEURON_COUNT):
            for target in range(NEURON_COUNT):
                weight = field.relations[source][target]
                if weight < 0:
                    incoming_inhibition[target] += abs(weight) * max(
                        0.0, field.neurons[source].state.activation
                    )
        for neuron, credit in zip(field.neurons, learned.credits, strict=True):
            stability = _clip01(1.0 - abs(credit.prediction_error))
            plasticity = _clip01(neuron.archetype.learning_rate * 20.0)
            connection.execute(
                """UPDATE lh_neuron_states SET
                     inhibition=%s,plasticity=%s,stability=%s,intrinsic_reward=%s,
                     updated_at=now()
                   WHERE workspace_id=%s AND neuron_id=%s""",
                (
                    _clip01(incoming_inhibition[neuron.archetype.neuron_id - 1]),
                    plasticity,
                    stability,
                    credit.intrinsic_reward,
                    workspace_id,
                    neuron.archetype.neuron_id,
                ),
            )
            connection.execute(
                """INSERT INTO lh_neuron_learning_events(
                       workspace_id,source_event_id,neuron_id,global_reward,local_reward,
                       contribution,responsibility,intrinsic_reward,prediction_error,metadata
                   ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)""",
                (
                    workspace_id,
                    event_id,
                    neuron.archetype.neuron_id,
                    learned.global_reward,
                    credit.local_reward,
                    credit.contribution,
                    credit.responsibility,
                    credit.intrinsic_reward,
                    credit.prediction_error,
                    _json({"outcome": dict(outcome)}),
                ),
            )

    def _create_outcome_event_and_memories(
        self,
        connection,
        *,
        workspace_id: str,
        event_id: int,
        stimulus: StimulusVector,
        field: NeuronField,
        reward: float,
        outcome: Mapping[str, Any],
    ):
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
                _json({"reward": _clip(reward), "outcome": dict(outcome)}),
                list(stimulus.values),
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
                    [_clip(reward), neuron.state.prediction],
                    1.0 + max(0.0, float(reward)) * 0.5,
                    _clip01(abs(neuron.state.activation)),
                    _json({"source_event_id": event_id, "outcome": dict(outcome)}),
                ),
            )
        return outcome_event

    def _persist_attractor(
        self,
        connection,
        *,
        workspace_id: str,
        run_id: str | None,
        field: NeuronField,
        reward: float,
    ) -> dict[str, Any]:
        vector = [value for neuron in field.neurons for value in neuron.state.vector()]
        rows = connection.execute(
            """SELECT * FROM lh_neuron_attractors
               WHERE workspace_id=%s ORDER BY occurrence_count DESC LIMIT 64""",
            (workspace_id,),
        ).fetchall()
        best = None
        best_similarity = -1.0
        for row in rows:
            centroid = _as_float_list(row["centroid_vector"], size=STATE_VECTOR_SIZE)
            similarity = cosine_similarity(vector, centroid)
            if similarity > best_similarity:
                best = row
                best_similarity = similarity
        dominant = [
            neuron.archetype.neuron_id
            for neuron in sorted(
                field.neurons,
                key=lambda item: abs(item.state.activation),
                reverse=True,
            )[:6]
        ]
        if best is not None and best_similarity >= ATTRACTOR_SIMILARITY:
            count = int(best["occurrence_count"]) + 1
            centroid = _as_float_list(
                best["centroid_vector"], size=STATE_VECTOR_SIZE
            )
            updated = [
                old + (new - old) / count
                for old, new in zip(centroid, vector, strict=True)
            ]
            success = 0.9 * float(best["success_score"]) + 0.1 * _clip(reward)
            stability = _clip01(max(float(best["stability"]), count / 50.0))
            row = connection.execute(
                """UPDATE lh_neuron_attractors SET
                     centroid_vector=%s,dominant_neurons=%s,occurrence_count=%s,
                     success_score=%s,stability=%s,last_run_id=%s,last_seen_at=now()
                   WHERE id=%s RETURNING *""",
                (updated, dominant, count, success, stability, run_id, best["id"]),
            ).fetchone()
            similarity = best_similarity
        else:
            row = connection.execute(
                """INSERT INTO lh_neuron_attractors(
                       id,workspace_id,centroid_vector,dominant_neurons,occurrence_count,
                       success_score,stability,last_run_id
                   ) VALUES (%s,%s,%s,%s,1,%s,0.02,%s) RETURNING *""",
                (str(uuid4()), workspace_id, vector, dominant, _clip(reward), run_id),
            ).fetchone()
            similarity = 1.0
        return {
            "id": str(row["id"]),
            "similarity": similarity,
            "occurrence_count": int(row["occurrence_count"]),
            "success_score": float(row["success_score"]),
            "stability": float(row["stability"]),
            "dominant_neurons": list(row["dominant_neurons"] or []),
        }

    def _persist_circuits(
        self,
        connection,
        *,
        workspace_id: str,
        circuits: Sequence[Mapping[str, Any]],
        reward: float,
    ) -> None:
        for circuit in circuits:
            connection.execute(
                """INSERT INTO lh_neuron_circuits(
                       id,workspace_id,signature,neuron_ids,edge_weights,circuit_strength,
                       stability,activation_count,success_rate,kind,last_activated_at
                   ) VALUES (%s,%s,%s,%s,%s,%s,%s,1,%s,%s,now())
                   ON CONFLICT (workspace_id,signature) DO UPDATE SET
                     neuron_ids=EXCLUDED.neuron_ids,
                     edge_weights=EXCLUDED.edge_weights,
                     circuit_strength=EXCLUDED.circuit_strength,
                     stability=LEAST(1.0,GREATEST(
                       lh_neuron_circuits.stability,
                       lh_neuron_circuits.activation_count / 100.0
                     )),
                     activation_count=lh_neuron_circuits.activation_count+1,
                     success_rate=0.9*lh_neuron_circuits.success_rate+0.1*EXCLUDED.success_rate,
                     kind=EXCLUDED.kind,last_activated_at=now(),updated_at=now()""",
                (
                    str(uuid4()),
                    workspace_id,
                    circuit["signature"],
                    list(circuit["neuron_ids"]),
                    list(circuit["edge_weights"]),
                    float(circuit["circuit_strength"]),
                    min(1.0, float(circuit["circuit_strength"]) * 0.1),
                    _clip(reward),
                    circuit["kind"],
                ),
            )

    def _persist_control(
        self,
        connection,
        *,
        workspace_id: str,
        run_id: str | None,
        control: Mapping[str, Any],
    ) -> None:
        connection.execute(
            """INSERT INTO lh_neuron_controls(workspace_id,control,source_run_id,version)
               VALUES (%s,%s::jsonb,%s,1)
               ON CONFLICT (workspace_id) DO UPDATE SET
                 control=EXCLUDED.control,source_run_id=EXCLUDED.source_run_id,
                 version=lh_neuron_controls.version+1,updated_at=now()""",
            (workspace_id, _json(dict(control)), run_id),
        )

    def _advance_identity(
        self,
        connection,
        *,
        workspace_id: str,
        event_id: int,
        signature: Sequence[float],
    ) -> dict[str, Any]:
        row = connection.execute(
            """UPDATE lh_neuron_identities SET
                 event_count=event_count+1,current_signature=%s,last_event_id=%s,
                 updated_at=now()
               WHERE workspace_id=%s RETURNING *""",
            (list(signature), event_id, workspace_id),
        ).fetchone()
        return dict(row)

    def _persist_checkpoint(
        self,
        connection,
        *,
        workspace_id: str,
        identity: Mapping[str, Any],
        field: NeuronField,
    ) -> None:
        states = [
            {
                "neuron_id": neuron.archetype.neuron_id,
                "state": list(neuron.state.vector()),
                "threshold": neuron.threshold,
                "experience_count": neuron.experience_count,
            }
            for neuron in field.neurons
        ]
        weights = [
            {
                "neuron_id": neuron.archetype.neuron_id,
                "long": list(neuron.long_weights),
                "short": list(neuron.short_weights),
                "eligibility": list(neuron.eligibility_trace),
            }
            for neuron in field.neurons
        ]
        edges = [
            [field.relations[source][target] for target in range(NEURON_COUNT)]
            for source in range(NEURON_COUNT)
        ]
        attractors = connection.execute(
            """SELECT id,occurrence_count,success_score,stability,dominant_neurons
               FROM lh_neuron_attractors WHERE workspace_id=%s
               ORDER BY occurrence_count DESC LIMIT 16""",
            (workspace_id,),
        ).fetchall()
        connection.execute(
            """INSERT INTO lh_neuron_checkpoints(
                   id,workspace_id,event_count,checkpoint_type,states,weights,edges,
                   attractors,identity_signature
               ) VALUES (%s,%s,%s,'periodic',%s::jsonb,%s::jsonb,%s::jsonb,%s::jsonb,%s)
               ON CONFLICT (workspace_id,event_count) DO NOTHING""",
            (
                str(uuid4()),
                workspace_id,
                int(identity["event_count"]),
                _json(states),
                _json(weights),
                _json(edges),
                _json([dict(row) for row in attractors]),
                list(identity.get("current_signature") or []),
            ),
        )

    def current_summary(self, *, workspace_id: str) -> dict[str, Any]:
        base = PostgresNeuronRuntime.current_summary(self, workspace_id=workspace_id)
        with self._connect() as connection:
            identity = connection.execute(
                "SELECT * FROM lh_neuron_identities WHERE workspace_id=%s",
                (workspace_id,),
            ).fetchone()
            control = connection.execute(
                "SELECT * FROM lh_neuron_controls WHERE workspace_id=%s",
                (workspace_id,),
            ).fetchone()
            attractors = connection.execute(
                """SELECT id,dominant_neurons,occurrence_count,success_score,stability,
                          first_seen_at,last_seen_at
                   FROM lh_neuron_attractors WHERE workspace_id=%s
                   ORDER BY stability DESC,occurrence_count DESC LIMIT 5""",
                (workspace_id,),
            ).fetchall()
            circuits = connection.execute(
                """SELECT signature,neuron_ids,edge_weights,circuit_strength,stability,
                          activation_count,success_rate,kind,last_activated_at
                   FROM lh_neuron_circuits WHERE workspace_id=%s
                   ORDER BY stability DESC,circuit_strength DESC LIMIT 8""",
                (workspace_id,),
            ).fetchall()
            learning = connection.execute(
                """SELECT count(*) AS updates,avg(abs(local_reward)) AS mean_local_reward,
                          avg(abs(prediction_error)) AS mean_prediction_error
                   FROM lh_neuron_learning_events WHERE workspace_id=%s""",
                (workspace_id,),
            ).fetchone()
            if control:
                cognitive_control = dict(control["control"] or {})
            else:
                field = self._load_field(workspace_id, connection=connection)
                cognitive_control = derive_cognitive_control(field).as_dict()
                if identity is None:
                    identity = connection.execute(
                        "SELECT * FROM lh_neuron_identities WHERE workspace_id=%s",
                        (workspace_id,),
                    ).fetchone()
        base.update(
            {
                "persistent": True,
                "cross_session_learning": True,
                "persistence_scope": "workspace",
                "prompt_persona": False,
                "identity": (
                    {
                        "generation": int(identity["generation"]),
                        "event_count": int(identity["event_count"]),
                        "schema_version": int(identity["schema_version"]),
                        "created_at": identity["created_at"].isoformat(),
                        "updated_at": identity["updated_at"].isoformat(),
                    }
                    if identity
                    else None
                ),
                "cognitive_control": cognitive_control,
                "dominant_attractors": [
                    {
                        **dict(row),
                        "id": str(row["id"]),
                        "first_seen_at": row["first_seen_at"].isoformat(),
                        "last_seen_at": row["last_seen_at"].isoformat(),
                    }
                    for row in attractors
                ],
                "stable_circuits": [
                    {
                        **dict(row),
                        "last_activated_at": (
                            row["last_activated_at"].isoformat()
                            if row["last_activated_at"]
                            else None
                        ),
                    }
                    for row in circuits
                ],
                "learning": {
                    "updates": int(learning["updates"] or 0),
                    "mean_local_reward": float(
                        learning["mean_local_reward"] or 0.0
                    ),
                    "mean_prediction_error": float(
                        learning["mean_prediction_error"] or 0.0
                    ),
                },
            }
        )
        return base
