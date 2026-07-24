from lighthouse.agent import AgentRuntime
from lighthouse.brain import LightHouseBrain, ReasoningLoop


def test_lighthouse_brain_is_the_integrated_reasoning_loop():
    assert issubclass(LightHouseBrain, AgentRuntime)
    assert ReasoningLoop is LightHouseBrain
