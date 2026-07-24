from lighthouse.capabilities import CapabilityRegistry
from lighthouse.models import KernelMode


def test_registry_contains_coding_agent_capabilities():
    registry = CapabilityRegistry()
    names = {item.tool_name for item in registry.list(kernel=KernelMode.SYSTEM)}
    assert "system.project.context.v1" in names
    assert "system.file.patch.v1" in names
    assert "system.git.diff.v1" in names
    assert "system.test.run.v1" in names


def test_exact_capability_search_wins():
    registry = CapabilityRegistry()
    result = registry.search("git diff", kernel=KernelMode.SYSTEM)
    assert result[0].tool_name == "system.git.diff.v1"
