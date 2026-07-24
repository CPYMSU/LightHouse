from __future__ import annotations

from typing import Any


class MemoryRuntimeBridge:
    """Idempotently project durable agent steps into Memory Fabric."""

    def __init__(self, memory, kernel):
        self.memory = memory
        self.kernel = kernel

    def sync(self, run_id: str, snapshot: dict[str, Any]) -> None:
        for step in snapshot.get("steps") or []:
            if not isinstance(step, dict):
                continue
            sequence = int(step.get("sequence") or 0)
            kind = str(step.get("kind") or "")
            if sequence <= 0 or not kind or not self._claim(run_id, sequence, kind):
                continue
            payload = step.get("payload") if isinstance(step.get("payload"), dict) else {}
            try:
                if kind == "observation":
                    operation_id = str(payload.get("operation_id") or "")
                    if operation_id:
                        self.memory.project_operation(run_id, self.kernel.snapshot(operation_id))
                elif kind == "user_input":
                    conversation = self.memory.conversation_for_run(run_id)
                    if conversation and payload.get("message"):
                        self.memory.record_message(
                            conversation_id=conversation["id"],
                            role="user",
                            content=str(payload["message"]),
                            run_id=run_id,
                            metadata={"step_sequence": sequence, "kind": kind},
                        )
                        self.memory.update_task_input(run_id, str(payload["message"]))
                elif kind in {"input_required", "run_completed", "run_failed"}:
                    conversation = self.memory.conversation_for_run(run_id)
                    message = str(payload.get("message") or payload.get("reason") or "").strip()
                    if conversation and message:
                        self.memory.record_message(
                            conversation_id=conversation["id"],
                            role="assistant",
                            content=message,
                            run_id=run_id,
                            metadata={"step_sequence": sequence, "kind": kind},
                        )
                    if kind == "run_completed":
                        self.memory.complete_task(run_id, status="succeeded", summary=message)
                    elif kind == "run_failed":
                        self.memory.complete_task(run_id, status="failed", summary=message)
            except Exception:
                self._release(run_id, sequence, kind)

    def _claim(self, run_id: str, sequence: int, kind: str) -> bool:
        with self.memory._connect() as connection:  # intentional internal bridge
            row = connection.execute(
                """INSERT INTO lh_memory_projections(run_id,sequence,kind)
                   VALUES (%s,%s,%s) ON CONFLICT DO NOTHING RETURNING run_id""",
                (run_id, sequence, kind),
            ).fetchone()
        return bool(row)

    def _release(self, run_id: str, sequence: int, kind: str) -> None:
        with self.memory._connect() as connection:
            connection.execute(
                "DELETE FROM lh_memory_projections WHERE run_id=%s AND sequence=%s AND kind=%s",
                (run_id, sequence, kind),
            )
