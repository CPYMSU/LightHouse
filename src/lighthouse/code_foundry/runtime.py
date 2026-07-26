from __future__ import annotations

import asyncio
import hashlib
import inspect
import re
from dataclasses import dataclass
from typing import Any, Protocol

from ..kernel import OperationKernel
from ..models import KernelMode, OperationRequest, OperationStatus
from .models import CodeAction, CodeActionKind, CodeObservation
from .patch import changed_paths_from_unified_patch
from .tools import CodeActionRegistry


class CodeActionExecutor(Protocol):
    def execute(self, action: CodeAction) -> CodeObservation | Any: ...


@dataclass(frozen=True)
class CodeBatchResult:
    actions: tuple[CodeAction, ...]
    observations: tuple[CodeObservation, ...]


class CodeRuntime:
    """Runs independent reads concurrently and workspace mutations in order."""

    def __init__(self, executor: CodeActionExecutor):
        self.executor = executor

    async def execute_batch(self, actions: tuple[CodeAction, ...] | list[CodeAction]) -> CodeBatchResult:
        batch = tuple(actions)
        if len({action.id for action in batch}) != len(batch):
            raise ValueError("code action batch contains duplicate ids")

        parallel = [action for action in batch if not action.mutates_workspace]
        mutations = [action for action in batch if action.mutates_workspace]
        results: dict[str, CodeObservation] = {}

        if parallel:
            async with asyncio.TaskGroup() as group:
                tasks = {
                    action.id: group.create_task(self._execute_safely(action))
                    for action in parallel
                }
            results.update({action_id: task.result() for action_id, task in tasks.items()})

        for action in mutations:
            results[action.id] = await self._execute_safely(action)

        return CodeBatchResult(
            actions=batch,
            observations=tuple(results[action.id] for action in batch),
        )

    async def _execute_safely(self, action: CodeAction) -> CodeObservation:
        try:
            value = self.executor.execute(action)
            observation = await value if inspect.isawaitable(value) else value
            if not isinstance(observation, CodeObservation):
                raise TypeError("code action executor must return CodeObservation")
            if observation.action_id != action.id or observation.kind is not action.kind:
                raise ValueError("code action executor returned an observation for a different action")
            return observation
        except Exception as exc:
            return CodeObservation(
                id=f"runtime-error:{action.id}",
                action_id=action.id,
                kind=action.kind,
                ok=False,
                payload={"error": str(exc), "error_type": type(exc).__name__},
            )


class KernelCodeActionExecutor:
    """Adapts native CodeActions to normal LightHouse Operations and Receipts."""

    def __init__(
        self,
        kernel: OperationKernel,
        *,
        workspace_id: str,
        actor: str,
        registry: CodeActionRegistry | None = None,
        mode: KernelMode = KernelMode.AUTO,
        auto_confirm: bool = False,
    ):
        self.kernel = kernel
        self.workspace_id = workspace_id
        self.actor = actor
        self.registry = registry or CodeActionRegistry()
        self.mode = mode
        self.auto_confirm = auto_confirm

    async def execute(self, action: CodeAction) -> CodeObservation:
        return await asyncio.to_thread(self._execute_sync, action)

    def _execute_sync(self, action: CodeAction) -> CodeObservation:
        spec = self.registry.get(action.kind)
        if action.kind is CodeActionKind.REVIEW:
            return self._review_sync(action)
        if spec.capability is None:
            return CodeObservation(
                id=f"kernel:{action.id}",
                action_id=action.id,
                kind=action.kind,
                ok=False,
                payload={"error": "action requires the native CodeFoundry review engine"},
            )
        if spec.mutates_workspace != action.mutates_workspace:
            return CodeObservation(
                id=f"kernel:{action.id}",
                action_id=action.id,
                kind=action.kind,
                ok=False,
                payload={"error": "action mutation declaration conflicts with the tool registry"},
            )

        snapshot = self.kernel.submit(
            OperationRequest(
                capability=spec.capability,
                arguments=action.arguments,
                workspace_id=self.workspace_id,
                actor=self.actor,
                mode=self.mode,
                idempotency_key=f"code-foundry:{action.id}",
            )
        )
        operation = snapshot["operation"]
        if (
            self.auto_confirm
            and operation["status"] == OperationStatus.AWAITING_CONFIRMATION.value
        ):
            snapshot = self.kernel.confirm(operation["id"], actor=self.actor)
            operation = snapshot["operation"]
        receipt = snapshot.get("receipt") if isinstance(snapshot.get("receipt"), dict) else {}
        payload = {
            "operation_id": operation["id"],
            "operation_status": operation["status"],
            "capability": operation["capability"],
            "receipt": receipt,
        }
        if isinstance(receipt.get("result"), dict):
            payload["result"] = receipt["result"]
        if action.kind is CodeActionKind.PATCH:
            payload["changed_paths"] = list(
                changed_paths_from_unified_patch(str(action.arguments.get("patch") or ""))
            )
        return CodeObservation(
            id=f"kernel:{operation['id']}",
            action_id=action.id,
            kind=action.kind,
            ok=receipt.get("ok") is True,
            payload=payload,
        )

    def _review_sync(self, action: CodeAction) -> CodeObservation:
        """Produce a fresh, deterministic review receipt from the current diff.

        This is intentionally a narrow native review stage: it proves that the
        post-patch diff was inspected and fails closed on merge-conflict
        markers. A later model-based reviewer can add findings beside this
        receipt without weakening the required deterministic evidence.
        """

        snapshot = self.kernel.submit(
            OperationRequest(
                capability="system.git.diff.v1",
                arguments={"cwd": action.arguments.get("cwd")} if action.arguments.get("cwd") else {},
                workspace_id=self.workspace_id,
                actor=self.actor,
                mode=self.mode,
                idempotency_key=f"code-foundry:{action.id}:review-diff",
            )
        )
        operation = snapshot["operation"]
        receipt = snapshot.get("receipt") if isinstance(snapshot.get("receipt"), dict) else {}
        result = receipt.get("result") if isinstance(receipt.get("result"), dict) else {}
        diff = str(result.get("diff") or "")
        findings = _review_findings(diff) if receipt.get("ok") is True else ["unable to obtain current diff"]
        payload = {
            "operation_id": operation["id"],
            "operation_status": operation["status"],
            "capability": "lighthouse.code_review.v1",
            "receipt": receipt,
            "review": {
                "engine": "deterministic-diff-v1",
                "diff_sha256": hashlib.sha256(diff.encode("utf-8")).hexdigest(),
                "changed_paths": _changed_paths(diff),
                "findings": findings,
            },
        }
        return CodeObservation(
            id=f"review:{operation['id']}",
            action_id=action.id,
            kind=action.kind,
            ok=receipt.get("ok") is True and not findings,
            payload=payload,
        )


_CONFLICT_MARKER = re.compile(r"^\+(?!\+\+\+)(?:<{7}|={7}|>{7})", re.MULTILINE)


def _review_findings(diff: str) -> list[str]:
    if not diff.strip():
        return ["current diff is empty"]
    if _CONFLICT_MARKER.search(diff):
        return ["unresolved merge-conflict marker in added code"]
    return []


def _changed_paths(diff: str) -> list[str]:
    paths: list[str] = []
    for match in re.finditer(r"^\+\+\+ b/(.+)$", diff, re.MULTILINE):
        path = match.group(1).strip()
        if path and path not in paths:
            paths.append(path)
    return paths
