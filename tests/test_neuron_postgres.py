from __future__ import annotations

import os
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row
import pytest

from lighthouse.bootstrap import migration_sql
from lighthouse.neuron_adaptation import AdaptivePostgresNeuronRuntime
from lighthouse.repository import PostgresRepository


DSN = os.environ.get("TEST_DATABASE_URL", "")
pytestmark = pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL is not configured")


def test_postgres_neuron_runtime_captures_processes_and_learns_from_change():
    repository = PostgresRepository(DSN)
    repository.migrate(migration_sql())
    workspace = repository.create_workspace(
        name=f"neuron-test-{uuid4().hex[:10]}",
        data_target_id=None,
        system_target_id=None,
    )
    runtime = AdaptivePostgresNeuronRuntime(DSN)

    try:
        event = runtime.emit_event(
            workspace_id=workspace.id,
            event_type="memory.failed",
            source_table="integration_test",
            source_id="memory-1",
            operation="insert",
            payload={"risk": 0.9, "uncertainty": 0.7},
        )
        processed = runtime.process_event(event["id"])

        assert processed["status"] == "processed"
        assert processed["learning"]["applied"] is True
        assert processed["learning"]["reward"] < 0
        assert len(processed["stimulus_vector"]) == 64
        assert len(processed["abm"]["state_vector"]) == 24 * 8

        with psycopg.connect(DSN) as connection:
            neuron_count = connection.execute(
                "SELECT count(*) FROM lh_neurons WHERE active=TRUE"
            ).fetchone()[0]
            vector_space_count = connection.execute(
                "SELECT count(*) FROM lh_neuron_vector_spaces"
            ).fetchone()[0]
            state_count = connection.execute(
                "SELECT count(*) FROM lh_neuron_states WHERE workspace_id=%s",
                (workspace.id,),
            ).fetchone()[0]
            memory_count = connection.execute(
                "SELECT count(*) FROM lh_neuron_memories WHERE workspace_id=%s",
                (workspace.id,),
            ).fetchone()[0]
            weight_count = connection.execute(
                "SELECT count(*) FROM lh_neuron_weights WHERE workspace_id=%s",
                (workspace.id,),
            ).fetchone()[0]
            edge_count = connection.execute(
                "SELECT count(*) FROM lh_neuron_edges WHERE workspace_id=%s",
                (workspace.id,),
            ).fetchone()[0]

        assert neuron_count == 24
        assert vector_space_count == 24
        assert state_count == 24
        assert memory_count == 48
        assert weight_count == 24
        assert edge_count == 24 * 23
        assert runtime.current_summary(workspace_id=workspace.id)["dominant_neurons"]
    finally:
        with psycopg.connect(DSN) as connection:
            connection.execute(
                "DELETE FROM lh_workspaces WHERE id=%s",
                (workspace.id,),
            )
            connection.commit()


def test_memory_message_insert_creates_a_nonblocking_stimulus_event():
    repository = PostgresRepository(DSN)
    repository.migrate(migration_sql())
    workspace = repository.create_workspace(
        name=f"neuron-trigger-{uuid4().hex[:10]}",
        data_target_id=None,
        system_target_id=None,
    )
    conversation_id = str(uuid4())

    try:
        with psycopg.connect(DSN, row_factory=dict_row) as connection:
            connection.execute(
                """INSERT INTO lh_conversations(id,workspace_id,actor,title)
                   VALUES (%s,%s,'integration','trigger test')""",
                (conversation_id, workspace.id),
            )
            connection.execute(
                """INSERT INTO lh_messages(conversation_id,role,content)
                   VALUES (%s,'user','A new memory stimulus')""",
                (conversation_id,),
            )
            event = connection.execute(
                """SELECT * FROM lh_stimulus_events
                   WHERE workspace_id=%s AND source_table='lh_messages'
                   ORDER BY id DESC LIMIT 1""",
                (workspace.id,),
            ).fetchone()
            connection.commit()

        assert event is not None
        assert event["status"] == "pending"
        assert event["operation"] == "insert"
        assert event["event_type"] == "lh_messages.insert"
    finally:
        with psycopg.connect(DSN) as connection:
            connection.execute(
                "DELETE FROM lh_workspaces WHERE id=%s",
                (workspace.id,),
            )
            connection.commit()
