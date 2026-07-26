"""Bridge the existing LightHouse JSON provider to the CodeFoundry contract."""

from __future__ import annotations

from typing import Any

from ..provider import AgentProtocolError, AgentProvider
from .brief import CodeBrief
from .history import CodeHistoryItem
from .models import CodeAction
from .provider import CodeModelResponse, CodeResponseKind
from .tools import CodeActionRegistry, CodeToolSpec


class AgentProviderCodeAdapter:
    """Use a native LightHouse provider without inheriting its generic tool atlas.

    The underlying provider still exchanges one strict JSON decision at a time.
    This adapter constrains that decision to the CodeFoundry action surface and
    turns it into typed ``CodeModelResponse`` values for the coding loop.
    """

    def __init__(
        self,
        provider: AgentProvider,
        *,
        registry: CodeActionRegistry | None = None,
    ):
        self.provider = provider
        self.registry = registry or CodeActionRegistry()

    def respond(
        self,
        *,
        instructions: str,
        brief: CodeBrief,
        history: tuple[CodeHistoryItem, ...],
        tools: tuple[CodeToolSpec, ...],
    ) -> CodeModelResponse:
        decision = self.provider.decide(
            system_prompt=_system_prompt(instructions),
            state={
                "code_foundry": {
                    "brief": brief.public_dict(),
                    "history": [item.public_dict() for item in history],
                    "tools": [_tool_public_dict(spec) for spec in tools],
                    "completion_rule": (
                        "After a patch, final requires a current diff, successful validation, "
                        "and native review evidence."
                    ),
                }
            },
        )
        if decision.kind == "final":
            return CodeModelResponse(CodeResponseKind.FINAL, message=str(decision.message or ""))
        if decision.kind == "ask":
            return CodeModelResponse(CodeResponseKind.ASK, message=str(decision.message or ""))
        if decision.kind != "tool":
            raise AgentProtocolError(f"unsupported CodeFoundry provider decision: {decision.kind}")

        capability = str(decision.capability or "").strip()
        spec = next((item for item in tools if item.capability == capability), None)
        if spec is None:
            raise AgentProtocolError(f"capability is outside the CodeFoundry tool surface: {capability}")
        arguments = decision.arguments if isinstance(decision.arguments, dict) else {}
        _validate_arguments(spec, arguments)
        action = CodeAction(
            id=_action_id(spec, history),
            kind=spec.kind,
            arguments=arguments,
            mutates_workspace=spec.mutates_workspace,
        )
        return CodeModelResponse(CodeResponseKind.ACTIONS, actions=(action,))


def _system_prompt(instructions: str) -> str:
    return (
        f"{instructions}\n\n"
        "Respond with one native LightHouse JSON decision. For a coding action, use "
        "kind='tool', choose only a capability in code_foundry.tools, and provide its "
        "arguments object. Do not claim tool results before they appear in code_foundry.history. "
        "Use kind='ask' when a required product decision is unknown. Use kind='final' only "
        "after the evidence rule in the supplied state is satisfied."
    )


def _tool_public_dict(spec: CodeToolSpec) -> dict[str, Any]:
    return {
        "name": spec.kind.value,
        "capability": spec.capability,
        "description": spec.description,
        "arguments": spec.arguments,
        "supports_parallel": spec.supports_parallel,
        "mutates_workspace": spec.mutates_workspace,
    }


def _validate_arguments(spec: CodeToolSpec, arguments: dict[str, Any]) -> None:
    unexpected = sorted(set(arguments) - set(spec.arguments))
    if unexpected:
        raise AgentProtocolError(
            f"unexpected arguments for CodeFoundry {spec.kind.value}: {', '.join(unexpected)}"
        )
    missing = [name for name, rule in spec.arguments.items() if rule.get("required") and name not in arguments]
    if missing:
        raise AgentProtocolError(
            f"missing required arguments for CodeFoundry {spec.kind.value}: {', '.join(missing)}"
        )


def _action_id(spec: CodeToolSpec, history: tuple[CodeHistoryItem, ...]) -> str:
    return f"code-{spec.kind.value}-{max((item.sequence for item in history), default=0) + 1}"
