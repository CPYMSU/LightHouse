from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .models import CodeActionKind


@dataclass(frozen=True)
class CodeToolSpec:
    kind: CodeActionKind
    description: str
    capability: str | None
    supports_parallel: bool
    mutates_workspace: bool
    arguments: dict[str, dict[str, Any]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "arguments", {key: dict(value) for key, value in self.arguments.items()})


_DEFAULT_TOOL_SPECS = (
    CodeToolSpec(
        kind=CodeActionKind.SEARCH,
        description="Search repository text for an exact query.",
        capability="system.file.search.v1",
        supports_parallel=True,
        mutates_workspace=False,
        arguments={
            "query": {"type": "string", "required": True},
            "path": {"type": "string", "required": False},
            "cwd": {"type": "string", "required": False},
            "max_results": {"type": "integer", "required": False},
        },
    ),
    CodeToolSpec(
        kind=CodeActionKind.READ,
        description="Read one repository file.",
        capability="system.file.read.v1",
        supports_parallel=True,
        mutates_workspace=False,
        arguments={
            "path": {"type": "string", "required": True},
            "cwd": {"type": "string", "required": False},
            "max_bytes": {"type": "integer", "required": False},
        },
    ),
    CodeToolSpec(
        kind=CodeActionKind.LIST,
        description="Refresh the bounded project file and instruction index.",
        capability="system.project.context.v1",
        supports_parallel=True,
        mutates_workspace=False,
        arguments={
            "cwd": {"type": "string", "required": False},
            "max_files": {"type": "integer", "required": False},
            "max_instruction_bytes": {"type": "integer", "required": False},
        },
    ),
    CodeToolSpec(
        kind=CodeActionKind.STATUS,
        description="Inspect the current Git working-tree status.",
        capability="system.git.status.v1",
        supports_parallel=True,
        mutates_workspace=False,
        arguments={"cwd": {"type": "string", "required": False}},
    ),
    CodeToolSpec(
        kind=CodeActionKind.PATCH,
        description="Apply one minimal unified patch to the working tree.",
        capability="system.file.patch.v1",
        supports_parallel=False,
        mutates_workspace=True,
        arguments={
            "patch": {"type": "string", "required": True},
            "cwd": {"type": "string", "required": False},
            "check": {"type": "boolean", "required": False},
        },
    ),
    CodeToolSpec(
        kind=CodeActionKind.DIFF,
        description="Inspect the current Git diff after a change.",
        capability="system.git.diff.v1",
        supports_parallel=True,
        mutates_workspace=False,
        arguments={
            "cwd": {"type": "string", "required": False},
            "staged": {"type": "boolean", "required": False},
            "max_bytes": {"type": "integer", "required": False},
        },
    ),
    CodeToolSpec(
        kind=CodeActionKind.TEST,
        description="Run a selected project validation command.",
        capability="system.test.run.v1",
        supports_parallel=False,
        mutates_workspace=False,
        arguments={
            "command": {"type": "string", "required": False},
            "cwd": {"type": "string", "required": False},
            "timeout": {"type": "integer", "required": False},
        },
    ),
    CodeToolSpec(
        kind=CodeActionKind.REVIEW,
        description="Review the final change evidence for defects and scope drift.",
        capability="lighthouse.code_review.v1",
        supports_parallel=False,
        mutates_workspace=False,
        arguments={"cwd": {"type": "string", "required": False}},
    ),
)


class CodeActionRegistry:
    """The small coding-tool surface, independent of the global capability atlas."""

    def __init__(self, specs: tuple[CodeToolSpec, ...] = _DEFAULT_TOOL_SPECS):
        self._specs = tuple(specs)
        self._by_kind = {spec.kind: spec for spec in self._specs}
        if len(self._by_kind) != len(self._specs):
            raise ValueError("duplicate CodeFoundry tool kind")

    def get(self, kind: CodeActionKind) -> CodeToolSpec:
        try:
            return self._by_kind[kind]
        except KeyError as exc:
            raise KeyError(f"unsupported CodeFoundry action: {kind.value}") from exc

    def visible_specs(self) -> tuple[CodeToolSpec, ...]:
        return self._specs
