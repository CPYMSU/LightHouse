from __future__ import annotations

from .models import Capability, ConfirmationMode, KernelMode, Risk


def _cap(
    tool_name: str,
    command: str,
    description: str,
    operation: str,
    *,
    aliases: tuple[str, ...] = (),
    arguments: dict | None = None,
    risk: Risk = Risk.LOW,
) -> Capability:
    return Capability(
        tool_name=tool_name,
        command=command,
        description=description,
        kernel=KernelMode.SYSTEM,
        executor="agent_bus",
        operation=operation,
        risk=risk,
        confirmation=ConfirmationMode.DIRECT,
        writes=False,
        aliases=aliases,
        arguments=arguments or {},
    )


AGENT_BUS_CAPABILITIES: tuple[Capability, ...] = (
    _cap(
        "agent.bus.dispatch.v1",
        "agent dispatch",
        (
            "Delegate an investigation, research, taste, frontend, backend, wiring, "
            "integration, testing or memory task. The main AI chooses whether delegation helps."
        ),
        "dispatch",
        aliases=("dispatch agent", "agent bus", "分派 agent", "調用 agent", "代理協作"),
        arguments={
            "role": {"type": "string", "required": True},
            "goal": {"type": "string", "required": True},
            "payload": {"type": "object", "required": False},
            "priority": {"type": "integer", "required": False},
            "visibility": {"type": "string", "required": False},
        },
    ),
    _cap(
        "agent.bus.dispatch_many.v1",
        "agent dispatch many",
        (
            "Create any number of logical specialist Work Orders chosen by the main AI. "
            "The durable queue controls physical concurrency without a product-level logical limit."
        ),
        "dispatch_many",
        aliases=("dispatch agents", "scale agents", "批量分派 agents", "規模化調查"),
        arguments={
            "work_orders": {"type": "array", "required": True},
            "project_id": {"type": "string", "required": False},
            "shared_payload": {"type": "object", "required": False},
        },
    ),
    _cap(
        "agent.bus.status.v1",
        "agent status",
        "Read or briefly wait for one Work Order and return its current progress and durable result.",
        "status",
        aliases=("agent result", "wait agent", "查看 agent", "等待 agent"),
        arguments={
            "work_order_id": {"type": "string", "required": True},
            "wait_seconds": {"type": "number", "required": False},
        },
    ),
    _cap(
        "agent.bus.results.v1",
        "agent results",
        "Read a batch of durable Work Order states and completed results without waiting.",
        "results",
        aliases=("agent batch results", "匯總 agent 結果", "批量查看 agents"),
        arguments={"work_order_ids": {"type": "array", "required": True}},
    ),
    _cap(
        "agent.bus.wait_many.v1",
        "agent wait many",
        (
            "Optionally wait for all selected Agents or only selected critical roles. "
            "Waiting is a main-AI choice, never an enforced workflow."
        ),
        "wait_many",
        aliases=("wait for agents", "等待關鍵 agents", "等 agents 返回"),
        arguments={
            "work_order_ids": {"type": "array", "required": True},
            "wait_seconds": {"type": "number", "required": False},
            "critical_roles": {"type": "array", "required": False},
        },
    ),
    _cap(
        "agent.bus.events.v1",
        "agent events",
        "Read partial Agent progress, critical findings, permission needs and completion events.",
        "events",
        aliases=("agent progress", "agents 在做什麼", "agent 部分結果"),
        arguments={
            "work_order_id": {"type": "string", "required": True},
            "after_id": {"type": "integer", "required": False},
        },
    ),
    _cap(
        "agent.bus.coordination.v1",
        "agent coordination",
        (
            "Return advisory waiting or parallel-work guidance from current Agent state. "
            "The main AI remains free to wait, continue, or review later."
        ),
        "coordination",
        aliases=("should wait agents", "協同建議", "並行後回看"),
        arguments={
            "project_id": {"type": "string", "required": False},
        },
    ),
    _cap(
        "agent.bus.cancel.v1",
        "agent cancel",
        "Cancel a queued or running delegated Work Order.",
        "cancel",
        aliases=("cancel agent", "取消 agent"),
        arguments={"work_order_id": {"type": "string", "required": True}},
        risk=Risk.NORMAL,
    ),
)
