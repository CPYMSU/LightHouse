from __future__ import annotations

import hashlib
import os
from uuid import uuid4

import pytest

from lighthouse.agent_bus import PostgresAgentBus
from lighthouse.agent_store import PostgresAgentStore
from lighthouse.bootstrap import migration_sql
from lighthouse.context_intelligence import ContextCompiler
from lighthouse.memory_search import PostgresMemoryFabric
from lighthouse.models import KernelMode, TargetKind
from lighthouse.repository import PostgresRepository


DSN = os.environ.get("TEST_DATABASE_URL", "")
pytestmark = pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL is not configured")


def test_context_compiler_uses_focused_memory_capsule_and_caches(tmp_path):
    suffix = uuid4().hex[:10]
    repository = PostgresRepository(DSN)
    repository.migrate(migration_sql())
    target = repository.create_target(
        name=f"context-system-{suffix}",
        kind=TargetKind.SYSTEM,
        config={
            "transport": "local",
            "default_cwd": str(tmp_path),
            "allowed_roots": [str(tmp_path)],
            "shell": "/bin/bash",
        },
    )
    workspace = repository.create_workspace(
        name=f"context-workspace-{suffix}",
        data_target_id=None,
        system_target_id=target.id,
    )
    memory = PostgresMemoryFabric(DSN)
    bus = PostgresAgentBus(DSN)
    bus.register_builtin_agents()
    memory.bind_agent_bus(bus)
    compiler = ContextCompiler(memory, bus)

    run_id = str(uuid4())
    PostgresAgentStore(DSN).create_run(
        run_id=run_id,
        task="繼續完善頁面",
        workspace_id=workspace.id,
        actor="adsin",
        mode=KernelMode.AUTO,
        max_steps=12,
        auto_confirm=False,
    )
    conversation = memory.ensure_conversation(
        workspace_id=workspace.id,
        actor="adsin",
        new=True,
        title="context",
    )
    memory.link_run(run_id, conversation["id"])
    for index in range(10):
        memory.record_message(
            conversation_id=conversation["id"],
            role="user",
            content=f"user-{index}",
            run_id=run_id,
        )
        memory.record_message(
            conversation_id=conversation["id"],
            role="assistant",
            content=f"assistant-{index}",
            run_id=run_id,
        )
    memory.start_task(
        run_id=run_id,
        conversation_id=conversation["id"],
        goal="繼續完善頁面",
    )

    first = compiler.compile(
        workspace_id=workspace.id,
        actor="adsin",
        conversation_id=conversation["id"],
        run_id=run_id,
        query="繼續完善頁面",
        turn_limit=8,
    )
    assert first["snapshot"]["cache"] == "miss"
    assert first["memory_index"]["tier"] == "focused"
    assert len(first["recent_turns"]) == 4
    assert first["recent_turns"][0]["user"]["content"] == "user-6"
    assert first["recent_turns"][-1]["assistant"]["content"] == "assistant-9"
    assert first["active_task"]["subject"] is None
    assert any(item["role"] == "memory-steward" for item in first["available_agents"])

    second = compiler.compile(
        workspace_id=workspace.id,
        actor="adsin",
        conversation_id=conversation["id"],
        run_id=run_id,
        query="繼續完善頁面",
        turn_limit=8,
    )
    assert second["snapshot"]["cache"] == "hit"
    assert second["snapshot"]["source_cursor"] == first["snapshot"]["source_cursor"]

    source_hash = hashlib.sha256(b"older-context").hexdigest()
    memory.store_conversation_distillation(
        conversation_id=conversation["id"],
        workspace_id=workspace.id,
        result={
            "summary": "較早對話已蒸餾",
            "entities": [],
            "relations": [],
            "inferences": [
                {
                    "claim": "使用者正在延續同一個頁面任務",
                    "confidence": 0.9,
                    "based_on": ["recent turns"],
                }
            ],
            "uncertainties": [],
        },
        source_message_id=4,
        source_hash=source_hash,
        distillation_level=2,
        model="test-model",
    )
    index_layer = memory.conversation_summary(conversation["id"], memory_depth="index")
    focused_layer = memory.conversation_summary(conversation["id"], memory_depth="focused")
    assert index_layer["memory_layer"] == "index"
    assert focused_layer["memory_layer"] == "focused"
    assert index_layer["summary"] == focused_layer["summary"]
    compiler.invalidate(
        workspace_id=workspace.id,
        conversation_id=conversation["id"],
    )
    upgraded = compiler.compile(
        workspace_id=workspace.id,
        actor="adsin",
        conversation_id=conversation["id"],
        run_id=run_id,
        query="繼續完善頁面",
        force=True,
    )
    assert upgraded["distillation"]["level"] == 2
    assert upgraded["conversation_summary"]["summary"] == "較早對話已蒸餾"
    assert upgraded["inferences"][0]["confidence"] == pytest.approx(0.9)
