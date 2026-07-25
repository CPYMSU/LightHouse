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
