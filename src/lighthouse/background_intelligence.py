from __future__ import annotations

import threading
from typing import Any
from uuid import uuid4

from .provider import ModelNotConfiguredError


class BackgroundIntelligenceWorker:
    """Invisible worker that upgrades memory and executes advisory specialist work."""

    def __init__(
        self,
        *,
        agent_bus,
        memory,
        context_compiler,
        provider,
        repository,
        poll_interval: float = 0.5,
    ):
        self.agent_bus = agent_bus
        self.memory = memory
        self.context_compiler = context_compiler
        self.provider = provider
        self.repository = repository
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
                    lease_seconds=180,
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
                         WHERE status IN ('running','awaiting_confirmation','waiting_input')
                           AND updated_at > now()-interval '10 minutes'
                       ) AS busy"""
                ).fetchone()
            return bool(row and row["busy"])
        except Exception:
            return False

    def _run_model_work_order(self, work_order: dict[str, Any]) -> None:
        work_order_id = work_order["id"]
        try:
            self.agent_bus.mark_running(work_order_id, worker_id=self.worker_id)
            result = self.provider.distill(
                kind="specialist_work",
                payload={
                    "role": work_order["role"],
                    "goal": work_order["goal"],
                    "context": work_order.get("payload") or {},
                    "constraints": {
                        "advisory_only": True,
                        "do_not_claim_execution": True,
                        "return_evidence": True,
                    },
                },
            )
            self.agent_bus.complete(work_order_id, result=result)
        except Exception as exc:
            self.agent_bus.fail(work_order_id, error=str(exc))

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
        }
        level = 1
        model = None
        try:
            result = self.provider.distill(
                kind="conversation_memory",
                payload=payload,
            )
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
                    "SELECT actor FROM lh_conversations WHERE id=%s",
                    (conversation_id,),
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
                "Active task: "
                + str(task.get("goal") or "")
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
