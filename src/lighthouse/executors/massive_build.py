from __future__ import annotations

from pathlib import Path
import re
import subprocess
from typing import Any

from ..models import Capability, ExecutionResult, Target


_REF_RE = re.compile(r"^[A-Za-z0-9._/-]{1,240}$")


class MassiveBuildExecutor:
    def __init__(self, *, store) -> None:
        self.store = store

    def execute(
        self,
        capability: Capability,
        target: Target,
        arguments: dict[str, Any],
    ) -> ExecutionResult:
        operation = capability.operation
        if operation == "cell_create":
            value = self.store.create_cell(
                project_id=self._required(arguments, "project_id"),
                name=self._required(arguments, "name"),
                goal=self._required(arguments, "goal"),
                domain=str(arguments.get("domain") or "general"),
                strategy=str(arguments.get("strategy") or "adaptive"),
                base_commit=str(arguments.get("base_commit") or "") or None,
                contract_ids=self._list(arguments, "contract_ids"),
                dependencies=self._list(arguments, "dependencies"),
                metadata=self._dict(arguments, "metadata"),
            )
            return ExecutionResult(ok=True, result={"cell": value})
        if operation == "cell_update":
            value = self.store.update_cell(
                self._required(arguments, "cell_id"),
                status=self._optional(arguments, "status"),
                progress=arguments.get("progress"),
                worktree_id=self._optional(arguments, "worktree_id"),
                assigned_work_orders=(
                    self._list(arguments, "assigned_work_orders")
                    if "assigned_work_orders" in arguments
                    else None
                ),
                metadata=self._dict(arguments, "metadata") if "metadata" in arguments else None,
            )
            return ExecutionResult(ok=True, result={"cell": value})
        if operation == "project_inspect":
            return ExecutionResult(
                ok=True,
                result={"massive_build": self.store.project_brief(self._required(arguments, "project_id"))},
            )
        if operation == "contract_create":
            value = self.store.create_contract(
                project_id=self._required(arguments, "project_id"),
                contract_type=self._required(arguments, "contract_type"),
                name=self._required(arguments, "name"),
                schema=self._dict(arguments, "schema"),
                status=str(arguments.get("status") or "draft"),
                owner=str(arguments.get("owner") or arguments.get("__actor") or "main-ai"),
                consumers=self._list(arguments, "consumers"),
                evidence=self._list(arguments, "evidence"),
                supersedes_id=self._optional(arguments, "supersedes_id"),
            )
            return ExecutionResult(ok=True, result={"contract": value})
        if operation == "contract_inspect":
            values = self.store.list_contracts(
                self._required(arguments, "project_id"),
                include_deprecated=bool(arguments.get("include_deprecated")),
            )
            return ExecutionResult(ok=True, result={"contracts": values, "count": len(values)})
        if operation == "lease_acquire":
            value = self.store.acquire_lease(
                project_id=self._required(arguments, "project_id"),
                scope_type=self._required(arguments, "scope_type"),
                scope=self._required(arguments, "scope"),
                cell_id=self._optional(arguments, "cell_id"),
                owner_work_order_id=self._optional(arguments, "owner_work_order_id"),
                base_commit=self._optional(arguments, "base_commit"),
                lease_seconds=int(arguments.get("lease_seconds") or 1800),
                metadata=self._dict(arguments, "metadata"),
            )
            return ExecutionResult(ok=True, result={"write_lease": value})
        if operation == "lease_release":
            value = self.store.release_lease(self._required(arguments, "lease_id"))
            return ExecutionResult(ok=True, result={"write_lease": value})
        if operation == "worktree_create":
            return self._worktree_create(target, arguments)
        if operation == "worktree_remove":
            return self._worktree_remove(target, arguments)
        if operation == "batch_create":
            value = self.store.create_batch(
                project_id=self._required(arguments, "project_id"),
                title=self._required(arguments, "title"),
                goal=self._required(arguments, "goal"),
                cell_id=self._optional(arguments, "cell_id"),
                base_commit=self._optional(arguments, "base_commit"),
                metadata=self._dict(arguments, "metadata"),
            )
            return ExecutionResult(ok=True, result={"batch": value})
        if operation == "batch_update":
            values = {key: arguments.get(key) for key in (
                "status", "head_commit", "changed_files", "added_lines", "deleted_lines",
                "diff_summary", "receipts", "verification", "metadata",
            ) if key in arguments}
            value = self.store.update_batch(self._required(arguments, "batch_id"), **values)
            return ExecutionResult(ok=True, result={"batch": value})
        if operation == "integration_create":
            value = self.store.create_integration(
                project_id=self._required(arguments, "project_id"),
                title=self._required(arguments, "title"),
                scope=str(arguments.get("scope") or "project"),
                source_cells=self._list(arguments, "source_cells"),
                source_batches=self._list(arguments, "source_batches"),
                base_commit=self._optional(arguments, "base_commit"),
                metadata=self._dict(arguments, "metadata"),
            )
            return ExecutionResult(ok=True, result={"integration": value})
        if operation == "integration_update":
            values = {key: arguments.get(key) for key in (
                "status", "result_commit", "conflicts", "receipts", "verification", "metadata",
            ) if key in arguments}
            value = self.store.update_integration(
                self._required(arguments, "integration_id"), **values
            )
            return ExecutionResult(ok=True, result={"integration": value})
        if operation == "wiring_upsert":
            value = self.store.upsert_wiring(
                project_id=self._required(arguments, "project_id"),
                feature_key=self._required(arguments, "feature_key"),
                title=self._required(arguments, "title"),
                states=self._dict(arguments, "states"),
                evidence=self._list(arguments, "evidence"),
                work_order_id=self._optional(arguments, "work_order_id"),
                metadata=self._dict(arguments, "metadata"),
            )
            return ExecutionResult(ok=True, result={"wiring": value})
        raise ValueError(f"unsupported Massive Build operation: {operation}")

    def _worktree_create(self, target: Target, arguments: dict[str, Any]) -> ExecutionResult:
        project_id = self._required(arguments, "project_id")
        branch = self._git_ref(self._required(arguments, "branch"), "branch")
        base_ref = self._git_ref(str(arguments.get("base_ref") or "HEAD"), "base_ref")
        cwd = self._cwd(target, arguments)
        path = self._confined_path(target, cwd, self._required(arguments, "path"))
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and any(path.iterdir()):
            raise ValueError("worktree path already exists and is not empty")
        completed = subprocess.run(
            ["git", "worktree", "add", "-b", branch, str(path), base_ref],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        if completed.returncode != 0:
            raise RuntimeError((completed.stderr or completed.stdout or "git worktree add failed").strip())
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=str(path), capture_output=True,
            text=True, timeout=20, check=False,
        ).stdout.strip()
        value = self.store.register_worktree(
            project_id=project_id,
            cell_id=self._optional(arguments, "cell_id"),
            path=str(path),
            branch=branch,
            base_ref=base_ref,
            head_commit=head or None,
            metadata={"repository_cwd": str(cwd)},
        )
        return ExecutionResult(
            ok=True,
            result={"worktree": value, "stdout": completed.stdout.strip()},
            exit_code=completed.returncode,
        )

    def _worktree_remove(self, target: Target, arguments: dict[str, Any]) -> ExecutionResult:
        worktree_id = self._required(arguments, "worktree_id")
        brief_project = None
        with self.store._connect() as connection:
            row = connection.execute(
                "SELECT * FROM lh_project_worktrees WHERE id=%s", (worktree_id,)
            ).fetchone()
        if not row:
            raise KeyError("project worktree not found")
        cwd = self._cwd(target, arguments)
        path = self._confined_path(target, cwd, str(row["path"]))
        command = ["git", "worktree", "remove"]
        if bool(arguments.get("force")):
            command.append("--force")
        command.append(str(path))
        completed = subprocess.run(
            command, cwd=str(cwd), capture_output=True, text=True,
            timeout=120, check=False,
        )
        if completed.returncode != 0:
            raise RuntimeError((completed.stderr or completed.stdout or "git worktree remove failed").strip())
        value = self.store.update_worktree(worktree_id, status="removed")
        return ExecutionResult(
            ok=True,
            result={"worktree": value, "stdout": completed.stdout.strip()},
            exit_code=completed.returncode,
        )

    @staticmethod
    def _required(arguments: dict[str, Any], key: str) -> str:
        value = str(arguments.get(key) or "").strip()
        if not value:
            raise ValueError(f"{key} is required")
        return value

    @staticmethod
    def _optional(arguments: dict[str, Any], key: str) -> str | None:
        value = str(arguments.get(key) or "").strip()
        return value or None

    @staticmethod
    def _list(arguments: dict[str, Any], key: str) -> list[Any]:
        value = arguments.get(key)
        if value is None:
            return []
        if not isinstance(value, list):
            raise ValueError(f"{key} must be an array")
        return value

    @staticmethod
    def _dict(arguments: dict[str, Any], key: str) -> dict[str, Any]:
        value = arguments.get(key)
        if value is None:
            return {}
        if not isinstance(value, dict):
            raise ValueError(f"{key} must be an object")
        return value

    @staticmethod
    def _git_ref(value: str, label: str) -> str:
        value = str(value or "").strip()
        if not _REF_RE.fullmatch(value) or ".." in value or value.startswith("-"):
            raise ValueError(f"invalid Git {label}")
        return value

    @staticmethod
    def _cwd(target: Target, arguments: dict[str, Any]) -> Path:
        return Path(
            str(arguments.get("cwd") or target.config.get("default_cwd") or ".")
        ).expanduser().resolve()

    @staticmethod
    def _confined_path(target: Target, cwd: Path, raw: str) -> Path:
        candidate = Path(raw).expanduser()
        candidate = candidate.resolve() if candidate.is_absolute() else (cwd / candidate).resolve()
        roots = [
            Path(str(item)).expanduser().resolve()
            for item in (target.config.get("allowed_roots") or [cwd])
        ]
        if not any(candidate == root or root in candidate.parents for root in roots):
            raise PermissionError("worktree path is outside the System Target allowed roots")
        return candidate
