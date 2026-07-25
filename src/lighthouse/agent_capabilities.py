from __future__ import annotations

from .models import Capability, ConfirmationMode, KernelMode, Risk


AGENT_BUS_CAPABILITIES: tuple[Capability, ...] = (
    Capability(
        tool_name="agent.bus.dispatch.v1",
        command="agent dispatch",
        description=(
            "Delegate an investigation, design, coding, verification or memory task "
            "through the durable Agent Bus. The main AI chooses whether delegation helps."
        ),
        kernel=KernelMode.SYSTEM,
        executor="agent_bus",
        operation="dispatch",
        risk=Risk.LOW,
        confirmation=ConfirmationMode.DIRECT,
        writes=False,
        aliases=("dispatch agent", "agent bus", "分派 agent", "調用 agent", "代理協作"),
        arguments={
            "role": {"type": "string", "required": True},
            "goal": {"type": "string", "required": True},
            "payload": {"type": "object", "required": False},
            "priority": {"type": "integer", "required": False},
            "visibility": {"type": "string", "required": False},
        },
    ),
    Capability(
        tool_name="agent.bus.dispatch_many.v1",
        command="agent dispatch many",
        description=(
            "Create any number of logical specialist Work Orders chosen by the main AI. "
            "The durable queue controls physical concurrency without imposing a project-size workflow."
        ),
        kernel=KernelMode.SYSTEM,
        executor="agent_bus",
        operation="dispatch_many",
        risk=Risk.LOW,
        confirmation=ConfirmationMode.DIRECT,
        writes=False,
        aliases=("dispatch agents", "scale agents", "批量分派 agents", "規模化調查"),
        arguments={
            "work_orders": {"type": "array", "required": True},
            "project_id": {"type": "string", "required": False},
            "shared_payload": {"type": "object", "required": False},
        },
    ),
    Capability(
        tool_name="agent.bus.status.v1",
        command="agent status",
        description=(
            "Read or briefly wait for a delegated work order and return its durable result."
        ),
        kernel=KernelMode.SYSTEM,
        executor="agent_bus",
        operation="status",
        risk=Risk.LOW,
        confirmation=ConfirmationMode.DIRECT,
        writes=False,
        aliases=("agent result", "wait agent", "查看 agent", "等待 agent"),
        arguments={
            "work_order_id": {"type": "string", "required": True},
            "wait_seconds": {"type": "number", "required": False},
        },
    ),
    Capability(
        tool_name="agent.bus.results.v1",
        command="agent results",
        description="Read a batch of durable Work Order states and results for synthesis by the main AI.",
        kernel=KernelMode.SYSTEM,
        executor="agent_bus",
        operation="results",
        risk=Risk.LOW,
        confirmation=ConfirmationMode.DIRECT,
        writes=False,
        aliases=("agent batch results", "匯總 agent 結果", "批量查看 agents"),
        arguments={
            "work_order_ids": {"type": "array", "required": True},
        },
    ),
    Capability(
        tool_name="agent.bus.cancel.v1",
        command="agent cancel",
        description="Cancel a queued or running delegated work order.",
        kernel=KernelMode.SYSTEM,
        executor="agent_bus",
        operation="cancel",
        risk=Risk.NORMAL,
        confirmation=ConfirmationMode.DIRECT,
        writes=False,
        aliases=("cancel agent", "取消 agent"),
        arguments={
            "work_order_id": {"type": "string", "required": True},
        },
    ),
)
