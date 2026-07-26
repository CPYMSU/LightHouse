from __future__ import annotations

import threading
import time
from typing import Any
from uuid import uuid4

from .agent_coordination import (
    normalise_collaboration_requests,
    tool_call_signature,
    write_paths_for_tool,
)
from .agent_results import normalise_agent_result
from .models import ConfirmationMode, KernelMode, OperationRequest, OperationStatus, digest_json
from .provider import ModelNotConfiguredError
from .work_intensity import resolve_intensity


class BackgroundIntelligenceWorker:
    """Invisible worker for memory distillation and authorized specialist Agent work."""

    def __init__(
        self,
        *,
        agent_bus,
        memory,
        context_compiler,
        provider,
        repository,
        kernel=None,
        run_repository=None,
        project_store=None,
        massive_build=None,
        poll_interval: float = 0.5,
    ):
        self.agent_bus = agent_bus
        self.memory = memory
        self.context_compiler = context_compiler
        self.provider = provider
        self.repository = repository
        self.kernel = kernel
        self.run_repository = run_repository
        self.project_store = project_store
        self.massive_build = massive_build
        self.poll_interval = max(0.1, min(float(poll_interval), 5.0))
        self.worker_id = f"memory-steward-{uuid4().hex[:10]}"
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop,
            name="lighthouse-background-intelligence",
            daemon=True,
        )
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(max(0.0, min(float(timeout), 10.0)))

    def _loop(self) -> None:
        while not self._stop.is_set():
            did_work = False
            try:
                work_order = self.agent_bus.claim_work_order(
                    worker_id=self.worker_id,
                    execution_modes=("model",),
                    lease_seconds=300,
                )
                if work_order:
                    did_work = True
                    self._run_model_work_order(work_order)
            except Exception:
                pass
            if not self._foreground_busy():
                try:
                    job = self.agent_bus.claim_background_job(
                        worker_id=self.worker_id,
                        lease_seconds=180,
                    )
                    if job:
                        did_work = True
                        self._run_background_job(job)
                except Exception:
                    pass
            if not did_work:
                self._stop.wait(self.poll_interval)

    def _foreground_busy(self) -> bool:
        try:
            with self.memory._connect() as connection:
                row = connection.execute(
                    """SELECT EXISTS(
                         SELECT 1 FROM lh_agent_runs
                         WHERE status='running'
                           AND updated_at > now()-interval '10 minutes'
                       ) AS busy"""
                ).fetchone()
            return bool(row and row["busy"])
        except Exception:
            return False

    def _run_model_work_order(self, work_order: dict[str, Any]) -> None:
        work_order_id = work_order["id"]
        try:
            work_order = self.agent_bus.mark_running(work_order_id, worker_id=self.worker_id)
            agent = self.agent_bus.agent_for_work_order(work_order_id)
            payload = dict(work_order.get("payload") or {})
            project_id = str(payload.get("project_id") or "") or None
            parent_run_id = str(work_order.get("parent_run_id") or "") or None
            intensity_value = payload.get("intensity")
            if isinstance(intensity_value, dict):
                intensity_value = intensity_value.get("selected") or intensity_value.get("name")
            policy = resolve_intensity(intensity_value or "balanced")
            allowed_tools = [
                name for name in (agent.get("capabilities") or [])
                if self._capability_exists(str(name))
            ]
            tool_results = list(payload.get("tool_results") or [])
            local_state = (
                dict(payload.get("local_cognitive_state") or {})
                if isinstance(payload.get("local_cognitive_state"), dict)
                else {}
            )
            result: dict[str, Any] = {}
            round_limit = policy.agent_initial_rounds
            hard_limit = policy.agent_hard_rounds
            seen_calls: set[str] = set()
            no_progress_rounds = 0
            last_progress = -1.0
            round_index = 0

            while round_index < round_limit:
                round_index += 1
                if hasattr(self.agent_bus, "shared_findings"):
                    payload["shared_findings"] = self.agent_bus.shared_findings(
                        workspace_id=work_order["workspace_id"],
                        parent_run_id=parent_run_id,
                        limit=40,
                    )
                result = self.provider.distill(
                    kind="specialist_work",
                    payload={
                        "role": work_order["role"],
                        "goal": work_order["goal"],
                        "assignment": payload.get("assignment") or {},
                        "shared_cognitive_brief": payload.get("shared_cognitive_brief") or {},
                        "shared_findings": payload.get("shared_findings") or [],
                        "local_cognitive_state": local_state,
                        "context": payload,
                        "intensity": {"selected": policy.name, "effective": policy.public_dict()},
                        "allowed_tools": allowed_tools,
                        "tool_results": tool_results[-30:],
                        "round": round_index,
                        "round_budget": {
                            "current_limit": round_limit,
                            "hard_limit": hard_limit,
                            "extend_only_with_new_evidence": True,
                        },
                        "constraints": {
                            "main_ai_is_project_director": True,
                            "main_ai_may_wait_or_continue": True,
                            "do_not_claim_unreceipted_execution": True,
                            "writes_require_run_scope": True,
                            "massive_project_writes_require_lease": bool(project_id),
                            "avoid_duplicate_work": True,
                        },
                        "_usage_context": {
                            "workspace_id": work_order["workspace_id"],
                            "run_id": parent_run_id,
                            "work_order_id": work_order_id,
                            "agent_id": agent["id"],
                            "project_id": project_id,
                        },
                    },
                )
                progress = max(0.0, min(float(result.get("progress") or 0.0), 1.0))
                criticality = str(result.get("criticality") or "background")
                summary = str(result.get("summary") or work_order["goal"])
                self.agent_bus.report_progress(
                    work_order_id,
                    progress=progress,
                    summary=summary,
                    criticality=criticality,
                )
                if criticality in {"important", "critical"}:
                    self.agent_bus.append_work_event(
                        work_order_id,
                        "agent_attention",
                        {
                            "criticality": criticality,
                            "summary": summary,
                            "findings": result.get("findings") or [],
                        },
                    )

                proposed_state = result.get("cognitive_state")
                if isinstance(proposed_state, dict):
                    local_state.update(proposed_state)
                local_state.update(
                    {
                        "understanding": str(
                            local_state.get("understanding") or result.get("summary") or ""
                        ),
                        "verified_facts": list(result.get("findings") or [])[-30:],
                        "open_questions": list(
                            result.get("open_questions") or result.get("uncertainties") or []
                        )[-20:],
                        "next_intent": str(
                            result.get("next_intent")
                            or local_state.get("next_intent")
                            or "review the latest tool Receipts and continue only with new evidence"
                        ),
                        "round": round_index,
                    }
                )
                if hasattr(self.agent_bus, "update_work_payload"):
                    self.agent_bus.update_work_payload(
                        work_order_id,
                        {
                            "local_cognitive_state": local_state,
                            "tool_results": tool_results[-40:],
                        },
                    )
                    self.agent_bus.append_work_event(
                        work_order_id,
                        "agent_cognitive_update",
                        {"round": round_index, "state": local_state},
                    )

                calls = result.get("tool_calls")
                calls = calls if isinstance(calls, list) else []
                unique_calls: list[dict[str, Any]] = []
                for call in calls[:8]:
                    if not isinstance(call, dict):
                        continue
                    signature = tool_call_signature(call)
                    if signature in seen_calls:
                        continue
                    seen_calls.add(signature)
                    unique_calls.append(call)

                evidence_gained = progress > last_progress or bool(result.get("findings")) or bool(unique_calls)
                last_progress = max(last_progress, progress)
                if evidence_gained:
                    no_progress_rounds = 0
                else:
                    no_progress_rounds += 1

                if unique_calls:
                    round_results = []
                    for call_index, call in enumerate(unique_calls):
                        round_results.append(
                            self._execute_specialist_tool(
                                work_order=work_order,
                                agent=agent,
                                call=call,
                                round_index=round_index,
                                call_index=call_index,
                                project_id=project_id,
                                parent_run_id=parent_run_id,
                            )
                        )
                    tool_results.extend(round_results)
                    if any(item.get("permission_required") for item in round_results):
                        result["complete"] = False
                        result.setdefault("uncertainties", []).append(
                            "A proposed side effect needs a compatible parent Run Auto scope."
                        )
                        break
                elif result.get("complete") is not False:
                    break

                if no_progress_rounds >= 2:
                    self.agent_bus.append_work_event(
                        work_order_id,
                        "agent_budget_stopped",
                        {
                            "round": round_index,
                            "reason": "no new evidence or strategy for two rounds",
                        },
                    )
                    break

                if round_index >= round_limit:
                    if round_limit < hard_limit and evidence_gained:
                        previous = round_limit
                        round_limit = min(hard_limit, round_limit + policy.agent_extension_rounds)
                        self.agent_bus.append_work_event(
                            work_order_id,
                            "budget_extended",
                            {
                                "previous_limit": previous,
                                "new_limit": round_limit,
                                "reason": "new evidence or executable progress remains",
                                "intensity": policy.name,
                            },
                        )
                    else:
                        break

            structured = normalise_agent_result(
                agent=agent,
                work_order={**work_order, "payload": payload},
                result=result,
                tool_results=tool_results,
            )
            structured["allowed_tools"] = allowed_tools
            structured["local_cognitive_state"] = local_state
            if hasattr(self.agent_bus, "publish_findings"):
                self.agent_bus.publish_findings(work_order_id, structured.get("findings") or [])
            collaboration = self._dispatch_collaborations(
                work_order=work_order,
                payload=payload,
                result=structured,
                policy=policy,
            )
            if collaboration:
                structured["collaboration_work_orders"] = collaboration
            self._store_specialist_findings(work_order, structured, project_id=project_id)
            self.agent_bus.report_progress(
                work_order_id,
                progress=(
                    1.0
                    if structured.get("complete") is not False
                    else max(0.01, float(structured.get("progress") or 0.0))
                ),
                summary=str(structured.get("summary") or "Specialist result ready"),
                criticality=str(structured.get("criticality") or "checkpoint"),
            )
            self.agent_bus.append_work_event(
                work_order_id,
                "result_fused",
                {
                    "result_type": structured.get("result_type"),
                    "findings": len(structured.get("findings") or []),
                    "changed_files": structured.get("changed_files") or [],
                    "tests": structured.get("tests") or [],
                },
            )
            self.agent_bus.complete(work_order_id, result=structured)
        except Exception as exc:
            self.agent_bus.fail(work_order_id, error=str(exc))

    def _execute_specialist_tool(
        self,
        *,
        work_order: dict[str, Any],
        agent: dict[str, Any],
        call: Any,
        round_index: int,
        call_index: int,
        project_id: str | None,
        parent_run_id: str | None,
    ) -> dict[str, Any]:
        if not isinstance(call, dict):
            return {"ok": False, "error": "tool call must be an object"}
        capability_name = str(call.get("capability") or "").strip()
        arguments = call.get("arguments")
        if not capability_name or not isinstance(arguments, dict):
            return {"ok": False, "error": "tool call requires capability and arguments"}
        if capability_name not in set(agent.get("capabilities") or []):
            return {
                "ok": False,
                "capability": capability_name,
                "arguments": arguments,
                "error": "tool is not authorized for this Agent",
            }
        if self.kernel is None:
            return {
                "ok": False,
                "capability": capability_name,
                "arguments": arguments,
                "error": "Operation Kernel is unavailable",
            }
        capability = self.kernel.registry.get(capability_name)
        if capability.writes and hasattr(self.agent_bus, "acquire_write_intent"):
            paths = write_paths_for_tool(capability_name, arguments)
            if paths:
                self.agent_bus.acquire_write_intent(work_order["id"], paths)
        if capability.confirmation != ConfirmationMode.DIRECT:
            scope = self._parent_auto_scope(parent_run_id)
            if not self._scope_allows(scope, work_order, capability_name):
                return {
                    "ok": False,
                    "capability": capability_name,
                    "arguments": arguments,
                    "permission_required": True,
                    "error": "The parent Run has no compatible Auto scope for this side effect.",
                }
            if capability.writes and project_id and self.massive_build is not None:
                path = str(arguments.get("path") or arguments.get("cwd") or ".")
                lease = self.massive_build.valid_lease(
                    project_id=project_id,
                    owner_work_order_id=work_order["id"],
                    path=path,
                )
                if lease is None:
                    return {
                        "ok": False,
                        "capability": capability_name,
                        "arguments": arguments,
                        "permission_required": True,
                        "error": "Massive Build write requires an active non-overlapping Write Lease.",
                    }
        actor = str(work_order.get("requested_by") or "main-ai")
        idempotency_key = (
            f"agent-work:{work_order['id']}:{round_index}:{call_index}:"
            + digest_json({"capability": capability_name, "arguments": arguments})
        )
        snapshot = self.kernel.submit(
            OperationRequest(
                capability=capability_name,
                arguments=dict(arguments),
                workspace_id=work_order["workspace_id"],
                actor=actor,
                mode=KernelMode.AUTO,
                idempotency_key=idempotency_key,
            )
        )
        if snapshot["operation"]["status"] == OperationStatus.AWAITING_CONFIRMATION.value:
            scope = self._parent_auto_scope(parent_run_id)
            if self._scope_allows(
                scope,
                work_order,
                capability_name,
                operation=snapshot.get("operation"),
            ):
                snapshot = self.kernel.confirm(snapshot["operation"]["id"], actor=actor)
            else:
                self.agent_bus.mark_waiting_confirmation(
                    work_order["id"],
                    operation_id=snapshot["operation"]["id"],
                    capability=capability_name,
                )
                return {
                    "ok": False,
                    "capability": capability_name,
                    "arguments": arguments,
                    "operation_id": snapshot["operation"]["id"],
                    "permission_required": True,
                }
        return {
            "ok": bool((snapshot.get("receipt") or {}).get("ok")),
            "capability": capability_name,
            "arguments": arguments,
            "operation": snapshot.get("operation"),
            "receipt": snapshot.get("receipt"),
        }

    def _dispatch_collaborations(
        self,
        *,
        work_order: dict[str, Any],
        payload: dict[str, Any],
        result: dict[str, Any],
        policy,
    ) -> list[dict[str, Any]]:
        requests = normalise_collaboration_requests(
            result.get("collaboration_requests") or result.get("collaboration_request")
        )
        coordination = payload.get("coordination") if isinstance(payload.get("coordination"), dict) else {}
        current_depth = int(coordination.get("collaboration_depth") or 0)
        if not requests or current_depth >= policy.collaboration_depth:
            return []
        values: list[dict[str, Any]] = []
        for request in requests[:4]:
            child_payload = {
                "assignment": {
                    "goal": request["goal"],
                    "intent": "collaborate",
                    "parent_goal": work_order["goal"],
                    "scope": request.get("scope") or {},
                    "deliverables": request.get("deliverables") or [],
                    "preserve": (payload.get("assignment") or {}).get("preserve") or [],
                },
                "shared_cognitive_brief": payload.get("shared_cognitive_brief") or {},
                "intensity": payload.get("intensity") or {"selected": policy.name},
                "collaboration_depth": current_depth + 1,
                "requested_by_work_order_id": work_order["id"],
            }
            child = self.agent_bus.dispatch(
                workspace_id=work_order["workspace_id"],
                parent_run_id=work_order.get("parent_run_id"),
                requested_by=str(work_order.get("requested_by") or "main-ai"),
                role=request["role"],
                goal=request["goal"],
                payload=child_payload,
                priority=request["priority"],
                visibility="foreground",
            )
            values.append(child)
            self.agent_bus.append_work_event(
                work_order["id"],
                "collaboration_requested",
                {
                    "child_work_order_id": child["id"],
                    "role": request["role"],
                    "goal": request["goal"],
                    "reason": request["reason"],
                    "depth": current_depth + 1,
                },
            )
        return values

    def _parent_auto_scope(self, parent_run_id: str | None) -> dict[str, Any]:
        if not parent_run_id or self.run_repository is None:
            return {}
        try:
            run = self.run_repository.get_agent_run(parent_run_id)
            return dict(run.auto_scope or {}) if run.auto_confirm else {}
        except Exception:
            return {}

    @staticmethod
    def _scope_allows(
        scope: dict[str, Any],
        work_order: dict[str, Any],
        capability_name: str,
        *,
        operation: dict[str, Any] | None = None,
    ) -> bool:
        if not scope or scope.get("workspace_id") != work_order.get("workspace_id"):
            return False
        allowed = set(scope.get("allowed_capabilities") or [])
        if "*" not in allowed and capability_name not in allowed:
            return False
        if operation:
            target_ids = set(scope.get("target_ids") or [])
            kernels = set(scope.get("allowed_kernels") or [])
            if target_ids and operation.get("target_id") not in target_ids:
                return False
            if kernels and operation.get("kernel") not in kernels:
                return False
        return True

    def _capability_exists(self, name: str) -> bool:
        if self.kernel is None:
            return False
        try:
            self.kernel.registry.get(name)
            return True
        except KeyError:
            return False

    def _store_specialist_findings(
        self,
        work_order: dict[str, Any],
        result: dict[str, Any],
        *,
        project_id: str | None,
    ) -> None:
        if not project_id or self.project_store is None:
            return
        findings = result.get("findings")
        if not isinstance(findings, list):
            return
        for item in findings[:40]:
            if isinstance(item, str):
                claim = item
                metadata = {}
                confidence = 0.6
                evidence = result.get("evidence") or []
            elif isinstance(item, dict):
                claim = str(item.get("claim") or item.get("finding") or item.get("summary") or "")
                metadata = {
                    key: value
                    for key, value in item.items()
                    if key not in {"claim", "evidence", "confidence"}
                }
                confidence = float(item.get("confidence") or 0.6)
                evidence = item.get("evidence") or result.get("evidence") or []
            else:
                continue
            if not claim.strip():
                continue
            try:
                self.project_store.store_finding(
                    project_id=project_id,
                    finding_type="verified_fact" if evidence else "inference",
                    claim=claim.strip(),
                    domain=str(work_order.get("role") or "general"),
                    confidence=confidence,
                    evidence=evidence if isinstance(evidence, list) else [evidence],
                    source_work_order_id=work_order["id"],
                    metadata=metadata,
                )
            except Exception:
                pass

    def _run_background_job(self, job: dict[str, Any]) -> None:
        job_id = job["id"]
        work_order_id = job.get("work_order_id")
        try:
            job_type = str(job["job_type"])
            if job_type == "memory.conversation.distill":
                result = self._distill_conversation(job)
            elif job_type == "memory.context.refresh":
                result = self._refresh_context(job)
            elif job_type == "memory.file.index":
                result = self._index_file(job)
            elif job_type == "memory.workspace.scan":
                result = self._scan_workspace(job)
            else:
                raise ValueError(f"unsupported background job type: {job_type}")
            self.agent_bus.complete_background_job(job_id, result=result)
            if work_order_id:
                self.agent_bus.complete(work_order_id, result=result)
        except Exception as exc:
            self.agent_bus.fail_background_job(job_id, error=str(exc))
            if work_order_id:
                try:
                    self.agent_bus.fail(work_order_id, error=str(exc))
                except Exception:
                    pass

    def _distill_conversation(self, job: dict[str, Any]) -> dict[str, Any]:
        conversation_id = str(job.get("conversation_id") or "")
        if not conversation_id:
            raise ValueError("conversation distillation requires conversation_id")
        source = self.memory.distillation_source(
            conversation_id=conversation_id,
            keep_recent_turns=8,
            max_messages=240,
        )
        with self.memory._connect() as connection:
            conversation = connection.execute(
                "SELECT workspace_id,actor FROM lh_conversations WHERE id=%s",
                (conversation_id,),
            ).fetchone()
            active_task = connection.execute(
                """SELECT t.goal,t.status,t.summary,l.kind AS subject_kind,
                          l.canonical_value AS subject
                   FROM lh_conversations c
                   LEFT JOIN lh_memory_tasks t ON t.id=c.active_task_id
                   LEFT JOIN lh_locators l ON l.id=t.subject_locator_id
                   WHERE c.id=%s""",
                (conversation_id,),
            ).fetchone()
        if not conversation:
            raise KeyError("conversation not found")
        payload = {
            "older_messages": source["messages"],
            "active_task": dict(active_task) if active_task else None,
            "preserve_recent_turns": source["recent_turns_preserved"],
            "instruction": (
                "Summarize older context only. Do not replace or paraphrase the recent "
                "turns that the main AI receives verbatim."
            ),
            "_usage_context": {
                "workspace_id": str(conversation["workspace_id"]),
                "conversation_id": conversation_id,
                "run_id": job.get("run_id"),
            },
        }
        level = 1
        model = None
        try:
            result = self.provider.distill(kind="conversation_memory", payload=payload)
            level = 2
            model = getattr(self.provider, "model", None)
        except ModelNotConfiguredError:
            result = self._structural_fallback(payload)
        if not source["messages"] and active_task:
            result.setdefault("summary", str(active_task.get("summary") or active_task.get("goal") or ""))
        stored = self.memory.store_conversation_distillation(
            conversation_id=conversation_id,
            workspace_id=str(conversation["workspace_id"]),
            result=result,
            source_message_id=source["source_message_id"],
            source_hash=source["source_hash"],
            distillation_level=level,
            model=model,
        )
        self.context_compiler.invalidate(
            workspace_id=str(conversation["workspace_id"]),
            conversation_id=conversation_id,
        )
        return {
            "conversation_id": conversation_id,
            "distillation_level": level,
            "source_message_id": source["source_message_id"],
            "summary_chars": len(str(stored.get("summary") or "")),
        }

    def _refresh_context(self, job: dict[str, Any]) -> dict[str, Any]:
        payload = job.get("payload") or {}
        conversation_id = str(job.get("conversation_id") or "") or None
        actor = str(payload.get("actor") or "")
        query = str(payload.get("query") or "")
        if not actor and conversation_id:
            with self.memory._connect() as connection:
                row = connection.execute(
                    "SELECT actor FROM lh_conversations WHERE id=%s", (conversation_id,)
                ).fetchone()
            actor = str(row["actor"]) if row else ""
        bundle = self.context_compiler.compile(
            workspace_id=job["workspace_id"],
            actor=actor or "operator",
            conversation_id=conversation_id,
            run_id=job.get("run_id"),
            query=query,
            force=True,
        )
        return {
            "source_cursor": (bundle.get("snapshot") or {}).get("source_cursor"),
            "distillation_level": (bundle.get("distillation") or {}).get("level"),
        }

    def _index_file(self, job: dict[str, Any]) -> dict[str, Any]:
        payload = job.get("payload") or {}
        path = str(payload.get("path") or "")
        if not path:
            raise ValueError("file index job requires path")
        value = self.memory.index_file(
            workspace_id=job["workspace_id"],
            path=path,
            run_id=job.get("run_id"),
            operation_id=str(payload.get("operation_id") or "") or None,
            opened=bool(payload.get("opened")),
            supplied_hash=str(payload.get("sha256") or "") or None,
        )
        return {"indexed": bool(value), "path": path}

    def _scan_workspace(self, job: dict[str, Any]) -> dict[str, Any]:
        payload = job.get("payload") or {}
        roots = payload.get("roots")
        if not isinstance(roots, list) or not roots:
            raise ValueError("workspace scan job requires roots")
        return self.memory.scan_workspace(
            workspace_id=job["workspace_id"],
            roots=[str(item) for item in roots],
            max_files=int(payload.get("max_files") or 5000),
        )

    @staticmethod
    def _structural_fallback(payload: dict[str, Any]) -> dict[str, Any]:
        messages = payload.get("older_messages") or []
        lines = []
        for item in messages[-80:]:
            role = str(item.get("role") or "")
            content = " ".join(str(item.get("content") or "").split())
            if content:
                lines.append(f"{role}: {content[:240]}")
        task = payload.get("active_task") or {}
        summary_parts = []
        if task:
            summary_parts.append(
                "Active task: " + str(task.get("goal") or "")
                + (f" ({task.get('status')})" if task.get("status") else "")
            )
        if lines:
            summary_parts.append("Earlier conversation:\n" + "\n".join(lines))
        return {
            "summary": "\n\n".join(summary_parts)[:12000],
            "entities": [],
            "relations": [],
            "inferences": [],
            "uncertainties": [],
        }
