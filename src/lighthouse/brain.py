from __future__ import annotations

from .agent import AgentRuntime


class LightHouseBrain(AgentRuntime):
    """LightHouse's built-in reasoning and action loop.

    The brain is not an external agent product. It plans against the current
    LightHouse capability atlas, dispatches every action through OperationKernel,
    observes durable Receipts, verifies outcomes and resumes from PostgreSQL.
    """


ReasoningLoop = LightHouseBrain
