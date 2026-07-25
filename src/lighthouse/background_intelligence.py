from __future__ import annotations

import threading
import time
from typing import Any
from uuid import uuid4

from .models import ConfirmationMode, KernelMode, OperationRequest, OperationStatus, digest_json
from .provider import ModelNotConfiguredError


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
            allowed_tools = [
                name for name in (agent.get("capabilities") or [])
                if self._capability_exists(str(name))
            ]
            tool_results = list(payload.get("tool_results") or [])
            result: dict[str, Any] = {}
            for round_index in range(1, 9):
                result = self.provider.distill(
                    kind="specialist_work",
                    payload={
                        "role": work_order["role"],
                        "goal": work_order["goal"],
                        "context": payload,
                        "allowed_tools": allowed_tools,
                        "tool_results": tool_results[-20:],
                        "round": round_index,
                        "constraints": {
                            "main_ai_is_project_director": True,
                            "main_ai_may_wait_or_continue": True,
                            "do_not_claim_unreceipted_execution": True,
                            "writes_require_run_scope": True,
                            "massive_project_writes_require_lease": bool(project_id),
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
                calls = result.get("tool_calls")
                if not isinstance(calls, list) or not calls:
                    if result.get("complete") is False and round_index < 8:
                        continue
                    break
                round_results = []
                for call_index, call in enumerate(calls[:8]):
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
                        "A proposed side effect needs the main AI or user to establish a compatible Run scope."
                    )
                    break
                if result.get("complete") is True and not calls:
                    break
            result["tool_results"] = tool_results[-40:]
            result["allowed_tools"] = allowed_tools
            result["work_order_id"] = work_order_id
            self._store_specialist_findings(work_order, result, project_id=project_id)
            self.agent_bus.report_progress(
                work_order_id,
                progress=1.0 if result.get("complete") is not False else max(0.01, float(result.get("progress") or 0.0)),
                summary=str(result.get("summary") or "Specialist result ready"),
                criticality=str(result.get("criticality") or "checkpoint"),
            )
            self.agent_bus.complete(work_order_id, result=result)
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
            return {"ok": False, "capability": capability_name, "error": "tool is not authorized for this Agent"}
        if self.kernel is None:
            return {"ok": False, "capability": capability_name, "error": "Operation Kernel is unavailable"}
        capability = self.kernel.registry.get(capability_name)
        if capability.confirmation != ConfirmationMode.DIRECT:
            scope = self._parent_auto_scope(parent_run_id)
            if not self._scope_allows(scope, work_order, capability_name):
                return {
                    "ok": False,
                    "capability": capability_name,
                    "permission_required": True,
                    "error": "The parent Run has no compatible Auto scope for this side effect.",
                }
            if project_id and self.massive_build is not None:
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
            if self._scope_allows(scope, work_order, capability_name):
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
                    "operation_id": snapshot["operation"]["id"],
                    "permission_required": True,
                }
        return {
            "ok": bool((snapshot.get("receipt") or {}).get("ok")),
            "capability": capability_name,
            "operation": snapshot.get("operation"),
            "receipt": snapshot.get("receipt"),
        }

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
    ) -> bool:
        return bool(
            scope
            and scope.get("workspace_id") == work_order.get("workspace_id")
            and capability_name in set(scope.get("allowed_capabilities") or [])
        )

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
                metadata = {key: value for key, value in item.items() if key not in {"claim", "evidence", "confidence"}}
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
