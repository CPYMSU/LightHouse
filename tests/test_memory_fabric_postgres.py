from __future__ import annotations

import hashlib
import os
from uuid import uuid4

import pytest

from lighthouse.agent_store import PostgresAgentStore
from lighthouse.bootstrap import migration_sql
from lighthouse.memory import PostgresMemoryFabric
from lighthouse.models import KernelMode, TargetKind
from lighthouse.repository import PostgresRepository


DSN = os.environ.get("TEST_DATABASE_URL", "")
pytestmark = pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL is not configured")


def test_messages_tasks_locators_and_files_survive_across_runs(tmp_path):
    suffix = uuid4().hex[:10]
    repository = PostgresRepository(DSN)
    repository.migrate(migration_sql())
    target = repository.create_target(name=f"memory-system-{suffix}", kind=TargetKind.SYSTEM, config={"transport": "local", "default_cwd": str(tmp_path), "allowed_roots": [str(tmp_path)], "shell": "/bin/bash"})
    workspace = repository.create_workspace(name=f"memory-workspace-{suffix}", data_target_id=None, system_target_id=target.id)
    store = PostgresAgentStore(DSN)
    memory = PostgresMemoryFabric(DSN)
    desktop = tmp_path / "Desktop"
    desktop.mkdir()
    html = desktop / "index.html"
    content = "<html><body>Swiss dashboard</body></html>"
    html.write_text(content, encoding="utf-8")

    run1 = str(uuid4())
    store.create_run(run_id=run1, task="把桌面的 index.html 做得酷炫一些", workspace_id=workspace.id, actor="adsin", mode=KernelMode.AUTO, max_steps=12, auto_confirm=False)
    conversation = memory.ensure_conversation(workspace_id=workspace.id, actor="adsin", new=True, title="index.html")
    memory.link_run(run1, conversation["id"])
    memory.record_message(conversation_id=conversation["id"], role="user", content="把桌面的 index.html 做得酷炫一些", run_id=run1)
    memory.start_task(run_id=run1, conversation_id=conversation["id"], goal="把桌面的 index.html 做得酷炫一些")
    memory.project_operation(run1, {"operation": {"capability": "system.file.write.v1", "envelope": {"arguments": {"path": "Desktop/index.html"}}}, "receipt": {"ok": True, "result": {"path": str(html), "relative_path": "Desktop/index.html", "sha256": hashlib.sha256(content.encode()).hexdigest()}}})
    memory.complete_task(run1, status="succeeded", summary="已修改並打開桌面 index.html")

    run2 = str(uuid4())
    store.create_run(run_id=run2, task="再做得豐富一些", workspace_id=workspace.id, actor="adsin", mode=KernelMode.AUTO, max_steps=12, auto_confirm=False)
    same = memory.ensure_conversation(workspace_id=workspace.id, actor="adsin", conversation_id=conversation["id"])
    memory.link_run(run2, same["id"])
    memory.record_message(conversation_id=same["id"], role="user", content="再做得豐富一些", run_id=run2)
    memory.start_task(run_id=run2, conversation_id=same["id"], goal="再做得豐富一些")

    context = memory.context(workspace_id=workspace.id, actor="adsin", conversation_id=same["id"], query="剛才的 index.html")
    assert context["active_task"]["subject"] == str(html.resolve())
    assert context["conversation"]["active_subject_value"] == str(html.resolve())
    assert any(item["canonical_path"] == str(html.resolve()) for item in context["relevant_files"])
    assert [item["content"] for item in context["recent_messages"]][-2:] == ["把桌面的 index.html 做得酷炫一些", "再做得豐富一些"]


def test_workspace_scan_builds_searchable_file_and_directory_index(tmp_path):
    suffix = uuid4().hex[:10]
    repository = PostgresRepository(DSN)
    repository.migrate(migration_sql())
    target = repository.create_target(name=f"scan-system-{suffix}", kind=TargetKind.SYSTEM, config={"transport": "local", "default_cwd": str(tmp_path), "allowed_roots": [str(tmp_path)]})
    workspace = repository.create_workspace(name=f"scan-workspace-{suffix}", data_target_id=None, system_target_id=target.id)
    nested = tmp_path / "real-project"
    nested.mkdir()
    file = nested / "warehouse-dashboard.html"
    file.write_text("<title>算電協同系統</title>", encoding="utf-8")
    memory = PostgresMemoryFabric(DSN)
    result = memory.scan_workspace(workspace_id=workspace.id, roots=[str(tmp_path)], max_files=100)
    assert result["indexed"] == 1
    assert result["directories_indexed"] >= 2
    found = memory.search_files(workspace_id=workspace.id, query="算電協同", limit=10)
    assert found[0]["canonical_path"] == str(file.resolve())
    context = memory.context(workspace_id=workspace.id, actor="adsin", conversation_id=None, query="real-project")
    assert any(item["kind"] == "directory" and item["canonical_value"] == str(nested.resolve()) for item in context["recent_locators"])
