from __future__ import annotations

import asyncio

from lighthouse.capabilities import CapabilityRegistry, DEFAULT_CAPABILITIES
from lighthouse.code_foundry import (
    CodeAction,
    CodeActionKind,
    CodeActionRegistry,
    CodeObservation,
    CodeRuntime,
    KernelCodeActionExecutor,
)
from lighthouse.kernel import OperationKernel
from lighthouse.models import ExecutionResult, TargetKind
from lighthouse.repository import InMemoryRepository


class TrackingExecutor:
    def __init__(self):
        self.running = 0
        self.maximum_running = 0
        self.calls: list[str] = []

    async def execute(self, action: CodeAction) -> CodeObservation:
        self.running += 1
        self.maximum_running = max(self.maximum_running, self.running)
        self.calls.append(action.id)
        await asyncio.sleep(0.01)
        self.running -= 1
        return CodeObservation(
            id=f"observation:{action.id}",
            action_id=action.id,
            kind=action.kind,
            ok=True,
        )


def action(identifier: str, kind: CodeActionKind) -> CodeAction:
    return CodeAction(
        id=identifier,
        kind=kind,
        mutates_workspace=kind is CodeActionKind.PATCH,
    )


def test_runtime_executes_independent_reads_concurrently_and_preserves_result_order():
    executor = TrackingExecutor()
    batch = [
        action("read", CodeActionKind.READ),
        action("patch", CodeActionKind.PATCH),
        action("status", CodeActionKind.STATUS),
    ]

    result = asyncio.run(CodeRuntime(executor).execute_batch(batch))

    assert executor.maximum_running == 2
    assert executor.calls[-1] == "patch"
    assert [item.action_id for item in result.observations] == ["read", "patch", "status"]


def test_runtime_serializes_workspace_mutations():
    executor = TrackingExecutor()
    batch = [action("patch-1", CodeActionKind.PATCH), action("patch-2", CodeActionKind.PATCH)]

    asyncio.run(CodeRuntime(executor).execute_batch(batch))

    assert executor.maximum_running == 1
    assert executor.calls == ["patch-1", "patch-2"]


def test_registry_has_a_small_native_coding_surface():
    registry = CodeActionRegistry()

    assert [item.kind.value for item in registry.visible_specs()] == [
        "search", "read", "list", "status", "patch", "diff", "test", "review"
    ]
    assert registry.get(CodeActionKind.PATCH).capability == "system.file.patch.v1"
    assert registry.get(CodeActionKind.REVIEW).capability == "lighthouse.code_review.v1"


class FakeSystemExecutor:
    def __init__(self):
        self.calls: list[str] = []

    def execute(self, capability, target, arguments):
        self.calls.append(capability.tool_name)
        if capability.tool_name == "system.git.diff.v1":
            return ExecutionResult(
                ok=True,
                result={
                    "diff": "diff --git a/src/app.py b/src/app.py\n"
                    "--- a/src/app.py\n"
                    "+++ b/src/app.py\n"
                    "@@ -1 +1 @@\n"
                    "-old\n"
                    "+new\n"
                },
            )
        return ExecutionResult(ok=True, result={"path": arguments.get("path"), "content": "source"})


def test_kernel_adapter_creates_a_normal_operation_receipt_for_a_code_action():
    repository = InMemoryRepository()
    target = repository.create_target(
        name="project",
        kind=TargetKind.SYSTEM,
        config={"transport": "local", "default_cwd": "/project", "allowed_roots": ["/project"]},
    )
    workspace = repository.create_workspace(
        name="project",
        data_target_id=None,
        system_target_id=target.id,
    )
    executor = FakeSystemExecutor()
    kernel = OperationKernel(
        repository,
        CapabilityRegistry(DEFAULT_CAPABILITIES),
        {"system": executor, "postgres": executor},
    )
    adapter = KernelCodeActionExecutor(
        kernel,
        workspace_id=workspace.id,
        actor="test",
        auto_confirm=True,
    )

    observation = asyncio.run(
        adapter.execute(
            CodeAction(id="read-app", kind=CodeActionKind.READ, arguments={"path": "app.py"})
        )
    )

    assert observation.ok is True
    assert observation.payload["capability"] == "system.file.read.v1"
    assert observation.payload["receipt"]["ok"] is True
    assert "changed_paths" not in observation.payload
    assert executor.calls == ["system.file.read.v1"]


def test_kernel_adapter_extracts_patch_paths_for_evidence_invalidation():
    repository = InMemoryRepository()
    target = repository.create_target(
        name="project",
        kind=TargetKind.SYSTEM,
        config={"transport": "local", "default_cwd": "/project", "allowed_roots": ["/project"]},
    )
    workspace = repository.create_workspace(
        name="project",
        data_target_id=None,
        system_target_id=target.id,
    )
    executor = FakeSystemExecutor()
    kernel = OperationKernel(
        repository,
        CapabilityRegistry(DEFAULT_CAPABILITIES),
        {"system": executor, "postgres": executor},
    )
    patch = """diff --git a/src/old.py b/src/new.py
--- a/src/old.py
+++ b/src/new.py
@@ -1 +1 @@
-old
+new
diff --git a/src/deleted.py b/src/deleted.py
--- a/src/deleted.py
+++ /dev/null
@@ -1 +0,0 @@
-deleted
"""
    adapter = KernelCodeActionExecutor(
        kernel,
        workspace_id=workspace.id,
        actor="test",
        auto_confirm=True,
    )

    observation = asyncio.run(
        adapter.execute(
            CodeAction(
                id="patch-app",
                kind=CodeActionKind.PATCH,
                arguments={"patch": patch},
                mutates_workspace=True,
            )
        )
    )

    assert observation.ok is True
    assert observation.payload["changed_paths"] == ["src/old.py", "src/new.py", "src/deleted.py"]


def test_kernel_adapter_runs_native_review_against_a_fresh_diff_receipt():
    repository = InMemoryRepository()
    target = repository.create_target(
        name="project",
        kind=TargetKind.SYSTEM,
        config={"transport": "local", "default_cwd": "/project", "allowed_roots": ["/project"]},
    )
    workspace = repository.create_workspace(
        name="project",
        data_target_id=None,
        system_target_id=target.id,
    )
    executor = FakeSystemExecutor()
    kernel = OperationKernel(
        repository,
        CapabilityRegistry(DEFAULT_CAPABILITIES),
        {"system": executor, "postgres": executor},
    )
    adapter = KernelCodeActionExecutor(kernel, workspace_id=workspace.id, actor="test")

    observation = asyncio.run(
        adapter.execute(CodeAction(id="review-app", kind=CodeActionKind.REVIEW))
    )

    assert observation.ok is True
    assert observation.payload["capability"] == "lighthouse.code_review.v1"
    assert observation.payload["review"]["changed_paths"] == ["src/app.py"]
    assert observation.payload["review"]["findings"] == []
    assert executor.calls == ["system.git.diff.v1"]
