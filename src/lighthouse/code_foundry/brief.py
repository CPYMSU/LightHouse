from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class CodeInstruction:
    path: str
    content: str


@dataclass(frozen=True)
class CodeBrief:
    """The bounded, task-specific context supplied to a coding turn."""

    task: str
    repository_root: str | None
    instructions: tuple[CodeInstruction, ...]
    git_status: dict[str, Any]
    relevant_files: tuple[str, ...]
    verified_facts: tuple[str, ...]
    active_task: str | None
    uncertainties: tuple[str, ...]
    existing_diff: str | None
    test_commands: tuple[str, ...]

    def public_dict(self) -> dict[str, Any]:
        return {
            "task": self.task,
            "repository_root": self.repository_root,
            "instructions": [
                {"path": item.path, "content": item.content}
                for item in self.instructions
            ],
            "git_status": dict(self.git_status),
            "relevant_files": list(self.relevant_files),
            "verified_facts": list(self.verified_facts),
            "active_task": self.active_task,
            "uncertainties": list(self.uncertainties),
            "existing_diff": self.existing_diff,
            "test_commands": list(self.test_commands),
        }


class CodeBriefCompiler:
    """Compiles existing LightHouse context into a small code working set."""

    def compile(
        self,
        *,
        task: str,
        project_context: dict[str, Any] | None = None,
        cognitive_context: dict[str, Any] | None = None,
        git_status: dict[str, Any] | None = None,
        existing_diff: str | None = None,
        test_commands: tuple[str, ...] | list[str] = (),
        relevant_files: tuple[str, ...] | list[str] = (),
        file_limit: int = 24,
    ) -> CodeBrief:
        clean_task = str(task or "").strip()
        if not clean_task:
            raise ValueError("code brief task must not be empty")
        project = project_context if isinstance(project_context, dict) else {}
        cognitive = cognitive_context if isinstance(cognitive_context, dict) else {}
        limit = max(1, min(int(file_limit), 128))

        instructions = _instructions(project.get("instructions"))
        selected_files = _unique_strings(
            [
                *relevant_files,
                *_file_values(cognitive.get("relevant_files")),
                *_file_values(cognitive.get("recent_locators")),
            ],
            limit=limit,
        )
        facts = _text_values(cognitive.get("verified_facts"), limit=24)
        uncertainties = _text_values(cognitive.get("uncertainties"), limit=16)
        active_task = _task_text(cognitive.get("active_task"))

        return CodeBrief(
            task=clean_task,
            repository_root=_optional_text(project.get("cwd")),
            instructions=instructions,
            git_status=dict(git_status or {}),
            relevant_files=selected_files,
            verified_facts=facts,
            active_task=active_task,
            uncertainties=uncertainties,
            existing_diff=_optional_text(existing_diff),
            test_commands=_unique_strings(test_commands, limit=16),
        )


def _instructions(value: Any) -> tuple[CodeInstruction, ...]:
    if not isinstance(value, list):
        return ()
    results: list[CodeInstruction] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, dict):
            continue
        path = _optional_text(item.get("path"))
        content = _optional_text(item.get("content"))
        if not path or content is None or path in seen:
            continue
        seen.add(path)
        results.append(CodeInstruction(path=path, content=content))
    return tuple(results)


def _file_values(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    values: list[str] = []
    for item in value:
        if isinstance(item, str):
            values.append(item)
        elif isinstance(item, dict):
            for key in ("path", "relative_path", "locator", "file"):
                candidate = _optional_text(item.get(key))
                if candidate:
                    values.append(candidate)
                    break
    return tuple(values)


def _text_values(value: Any, *, limit: int) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    results: list[str] = []
    for item in value:
        if isinstance(item, str):
            candidate = item
        elif isinstance(item, dict):
            candidate = next(
                (
                    item.get(key)
                    for key in ("fact", "claim", "summary", "message", "content")
                    if _optional_text(item.get(key))
                ),
                None,
            )
        else:
            candidate = None
        text = _optional_text(candidate)
        if text and text not in results:
            results.append(text)
        if len(results) >= limit:
            break
    return tuple(results)


def _task_text(value: Any) -> str | None:
    if isinstance(value, str):
        return _optional_text(value)
    if isinstance(value, dict):
        for key in ("goal", "subject", "summary", "content"):
            text = _optional_text(value.get(key))
            if text:
                return text
    return None


def _unique_strings(values: Any, *, limit: int) -> tuple[str, ...]:
    if not isinstance(values, (list, tuple, set)):
        return ()
    results: list[str] = []
    for value in values:
        text = _optional_text(value)
        if text and text not in results:
            results.append(text)
        if len(results) >= limit:
            break
    return tuple(results)


def _optional_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None
