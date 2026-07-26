from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Any

from .brief import CodeBrief
from .evidence import EvidenceLedger
from .events import CodeRunEvent, CodeRunEventSink
from .history import CodeHistory, CodeHistoryItem
from .models import CodeResult, CodeResultStatus
from .provider import CodeModelAdapter, CodeModelResponse, CodeResponseKind
from .runtime import CodeRuntime
from .tool_context import CodeToolContext
from .tools import CodeActionRegistry
from .verification import VerificationGate


_BASE_INSTRUCTIONS = """You are the LightHouse CodeFoundry coding agent.
Inspect the relevant code before editing. Make the smallest coherent change.
Use action batches only for independent inspection. After every patch, obtain a
current diff, run selected validation, and review the resulting change. A final
message is a concise evidence-backed summary, not proof by itself."""


@dataclass(frozen=True)
class CodeRunOutcome:
    result: CodeResult
    turns: int
    history: tuple[CodeHistoryItem, ...]


class CodeFoundryLoop:
    """Native code-agent loop independent of any model provider wire format."""

    def __init__(
        self,
        *,
        model: CodeModelAdapter,
        runtime: CodeRuntime,
        registry: CodeActionRegistry | None = None,
        verification: VerificationGate | None = None,
        max_turns: int = 32,
        max_history_items: int = 64,
        max_observation_bytes: int = 16 * 1024,
        instructions: str = _BASE_INSTRUCTIONS,
        event_sink: CodeRunEventSink | None = None,
    ):
        self.model = model
        self.runtime = runtime
        self.registry = registry or CodeActionRegistry()
        self.verification = verification or VerificationGate()
        self.max_turns = max(1, int(max_turns))
        self.max_history_items = max(8, int(max_history_items))
        self.max_observation_bytes = max(256, int(max_observation_bytes))
        self.instructions = instructions.strip()
        self.event_sink = event_sink

    async def run(self, brief: CodeBrief) -> CodeRunOutcome:
        history = CodeHistory()
        history.add_brief(brief.public_dict())
        ledger = EvidenceLedger()
        tool_context = CodeToolContext(self.registry.visible_specs())
        previous_tool_context: dict[str, str] | None = None
        await self._emit("started", {"brief": brief.public_dict()})

        for turn in range(1, self.max_turns + 1):
            await self._emit("turn_started", {"turn": turn})
            rendered_tools = tool_context.render_diff(previous_tool_context)
            previous_tool_context = tool_context.snapshot()
            response = await self._respond(brief, history, rendered_tools)
            await self._emit(
                "model_response",
                {
                    "turn": turn,
                    "kind": response.kind.value,
                    "message": response.message,
                    "action_ids": [action.id for action in response.actions],
                },
            )
            if response.kind is CodeResponseKind.ASK:
                result = CodeResult(
                    status=CodeResultStatus.NEEDS_INPUT,
                    summary=response.message,
                    changed_paths=ledger.changed_paths,
                    evidence_ids=ledger.current_evidence_ids(),
                )
                return await self._complete(result, turn, history)

            if response.kind is CodeResponseKind.FINAL:
                decision = self.verification.evaluate(ledger, summary=response.message)
                if decision.result.status is CodeResultStatus.VERIFIED:
                    history.add_summary(response.message)
                    return await self._complete(decision.result, turn, history)
                history.add_summary(
                    "Verification gate blocked completion: "
                    + "; ".join(decision.result.blockers)
                )
                await self._emit(
                    "verification_blocked",
                    {
                        "turn": turn,
                        "status": decision.result.status.value,
                        "blockers": list(decision.result.blockers),
                    },
                )
                if decision.result.status is CodeResultStatus.FAILED:
                    return await self._complete(decision.result, turn, history)
                continue

            for action in response.actions:
                history.add_action(action)
                await self._emit(
                    "action_requested",
                    {
                        "turn": turn,
                        "id": action.id,
                        "kind": action.kind.value,
                        "arguments": dict(action.arguments),
                        "mutates_workspace": action.mutates_workspace,
                    },
                )
            batch = await self.runtime.execute_batch(response.actions)
            for action, observation in zip(batch.actions, batch.observations, strict=True):
                evidence = ledger.record(action, observation)
                history.add_observation(observation, pinned=evidence is not None)
                await self._emit(
                    "observation_recorded",
                    {
                        "turn": turn,
                        "id": observation.id,
                        "action_id": observation.action_id,
                        "kind": observation.kind.value,
                        "ok": observation.ok,
                        "payload": dict(observation.payload),
                        "evidence_id": evidence.id if evidence is not None else None,
                        "workspace_generation": ledger.workspace_generation,
                    },
                )
                if evidence is not None and evidence.kind.value == "patch":
                    changed = evidence.summary.get("changed_paths") or []
                    history.invalidate_paths(changed)

        result = CodeResult(
            status=CodeResultStatus.UNVERIFIED,
            summary="CodeFoundry reached its turn budget before receiving a verified final result.",
            changed_paths=ledger.changed_paths,
            evidence_ids=ledger.current_evidence_ids(),
            blockers=("turn budget exhausted",),
        )
        return await self._complete(result, self.max_turns, history)

    async def _respond(
        self,
        brief: CodeBrief,
        history: CodeHistory,
        rendered_tools: str | None,
    ) -> CodeModelResponse:
        instructions = self.instructions
        if rendered_tools:
            instructions = f"{instructions}\n\n{rendered_tools}"
        value = self.model.respond(
            instructions=instructions,
            brief=brief,
            history=history.for_model(
                max_items=self.max_history_items,
                max_observation_bytes=self.max_observation_bytes,
            ),
            tools=self.registry.visible_specs(),
        )
        response = await value if inspect.isawaitable(value) else value
        if not isinstance(response, CodeModelResponse):
            raise TypeError("code model adapter must return CodeModelResponse")
        return response

    def _outcome(self, result: CodeResult, turns: int, history: CodeHistory) -> CodeRunOutcome:
        return CodeRunOutcome(
            result=result,
            turns=turns,
            history=history.items(include_stale=True),
        )

    async def _complete(
        self,
        result: CodeResult,
        turns: int,
        history: CodeHistory,
    ) -> CodeRunOutcome:
        outcome = self._outcome(result, turns, history)
        await self._emit(
            "completed",
            {
                "turns": turns,
                "status": result.status.value,
                "summary": result.summary,
                "changed_paths": list(result.changed_paths),
                "evidence_ids": list(result.evidence_ids),
                "blockers": list(result.blockers),
            },
        )
        return outcome

    async def _emit(self, kind: str, payload: dict[str, Any]) -> None:
        if self.event_sink is None:
            return
        value = self.event_sink.emit(CodeRunEvent(kind, payload))
        if inspect.isawaitable(value):
            await value
