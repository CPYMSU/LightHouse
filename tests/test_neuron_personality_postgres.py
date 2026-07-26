from __future__ import annotations

import os
from uuid import uuid4

import psycopg
import pytest

from lighthouse.bootstrap import migration_sql
from lighthouse.neuron_adaptation import AdaptivePostgresNeuronRuntime
from lighthouse.repository import PostgresRepository


DSN = os.environ.get("TEST_DATABASE_URL", "")
pytestmark = pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL is not configured")


def test_personality_learning_survives_runtime_and_conversation_restart():
    repository = PostgresRepository(DSN)
    repository.migrate(migration_sql())
    workspace = repository.create_workspace(
        name=f"persistent-neuron-{uuid4().hex[:10]}",
        data_target_id=None,
        system_target_id=None,
    )
    first_runtime = AdaptivePostgresNeuronRuntime(DSN)

    try:
        first = first_runtime.emit_event(
            workspace_id=workspace.id,
            event_type="conversation.user_approval",
            source_table="integration_test",
            source_id="message-1",
            operation="insert",
            payload={
                "interaction_features": {
                    "approval": 0.9,
                    "continuation": 0.8,
                    "directness": 0.7,
                }
            },
        )
        first_result = first_runtime.process_event(first["id"])
        assert first_result["learning"]["persistent"] is True
        assert first_result["learning"]["cross_session"] is True
        assert len(first_result["learning"]["local_rewards"]) == 24

        resumed_runtime = AdaptivePostgresNeuronRuntime(DSN)
        resumed = resumed_runtime.current_summary(workspace_id=workspace.id)
        assert resumed["persistent"] is True
        assert resumed["cross_session_learning"] is True
        assert resumed["identity"]["event_count"] == 1
        assert resumed["cognitive_control"]["prompt_persona"] is False

        second = resumed_runtime.emit_event(
            workspace_id=workspace.id,
            event_type="conversation.user_correction",
            source_table="integration_test",
            source_id="message-2",
            operation="insert",
            payload={
                "interaction_features": {
                    "correction": 0.9,
                    "rejection": 0.6,
                    "frustration": 0.4,
                }
            },
        )
        resumed_runtime.process_event(second["id"])
        final = AdaptivePostgresNeuronRuntime(DSN).current_summary(
            workspace_id=workspace.id
        )
        assert final["identity"]["event_count"] == 2

        with psycopg.connect(DSN) as connection:
            identity_count = connection.execute(
                "SELECT count(*) FROM lh_neuron_identities WHERE workspace_id=%s",
                (workspace.id,),
            ).fetchone()[0]
            learning_count = connection.execute(
                "SELECT count(*) FROM lh_neuron_learning_events WHERE workspace_id=%s",
                (workspace.id,),
            ).fetchone()[0]
            attractor_count = connection.execute(
                "SELECT count(*) FROM lh_neuron_attractors WHERE workspace_id=%s",
                (workspace.id,),
            ).fetchone()[0]
            control_count = connection.execute(
                "SELECT count(*) FROM lh_neuron_controls WHERE workspace_id=%s",
                (workspace.id,),
            ).fetchone()[0]

        assert identity_count == 1
        assert learning_count == 48
        assert attractor_count >= 1
        assert control_count == 1
    finally:
        with psycopg.connect(DSN) as connection:
            connection.execute(
                "DELETE FROM lh_workspaces WHERE id=%s",
                (workspace.id,),
            )
            connection.commit()
