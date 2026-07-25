from __future__ import annotations

from importlib.resources import files
from typing import Any

from .agent_adapter import AgentRepositoryAdapter
from .agent_bus import PostgresAgentBus
from .agent_capabilities import AGENT_BUS_CAPABILITIES
from .agent_store import PostgresAgentStore
from .background_intelligence import BackgroundIntelligenceWorker
from .brain import LightHouseBrain
from .capabilities import CapabilityRegistry, DEFAULT_CAPABILITIES
from .config import Settings
from .data_capabilities import DATA_KERNEL_CAPABILITIES
from .data_kernel import DataTargetResolver, PostgresDataCatalog
from .executors import (
    AgentBusExecutor,
    DesktopExecutor,
    PostgresExecutor,
    ProjectFileExecutor,
    SystemExecutor,
)
from .extra_capabilities import SYSTEM_TYPED_CAPABILITIES
from .kernel import OperationKernel
from .memory_search import PostgresMemoryFabric
from .neuron_adaptation import AdaptivePostgresNeuronRuntime
from .neuron_context import NeuronAwareContextCompiler
from .neuron_runtime import NeuronReflexWorker
from .provider import DisabledProvider, OpenAICompatibleProvider
from .repository import PostgresRepository


class _WorkerGroup:
    """Expose one lifecycle handle for all invisible intelligence workers."""

    def __init__(self, *workers: Any):
        self.workers = workers

    def stop(self, timeout: float = 2.0) -> None:
        for worker in self.workers:
            worker.stop(timeout=timeout)


def migration_sql() -> str:
    root = files("lighthouse.sql")
    return "\n".join(
        root.joinpath(name).read_text(encoding="utf-8")
        for name in (
            "0001_core.sql",
            "0002_memory_runtime.sql",
            "0003_context_intelligence.sql",
            "0004_emergent_neurons.sql",
        )
    )


def build_kernel(settings: Settings, *, migrate: bool = True) -> OperationKernel:
    repository = PostgresRepository(settings.database_url)
    if migrate:
        repository.migrate(migration_sql())
    catalog = PostgresDataCatalog(settings.database_url)
    memory = PostgresMemoryFabric(settings.database_url)
    agent_bus = PostgresAgentBus(settings.database_url)
    neuron_runtime = AdaptivePostgresNeuronRuntime(settings.database_url)
    agent_bus.register_builtin_agents()
    memory.bind_agent_bus(agent_bus)
    context_compiler = NeuronAwareContextCompiler(
        memory,
        agent_bus,
        neuron_runtime,
    )
    registry = CapabilityRegistry(
        (
            *DEFAULT_CAPABILITIES,
            *SYSTEM_TYPED_CAPABILITIES,
            *DATA_KERNEL_CAPABILITIES,
            *AGENT_BUS_CAPABILITIES,
        )
    )
    agent_bus_executor = AgentBusExecutor(
        agent_bus=agent_bus,
        context_compiler=context_compiler,
        repository=repository,
        registry=registry,
    )
    kernel = OperationKernel(
        repository,
        registry,
        {
            "postgres": PostgresExecutor(catalog),
            "system": SystemExecutor(),
            "project_file": ProjectFileExecutor(),
            "desktop": DesktopExecutor(),
            "agent_bus": agent_bus_executor,
        },
        target_resolver=DataTargetResolver(catalog),
        data_catalog=catalog,
    )
    kernel.memory = memory
    kernel.agent_bus = agent_bus
    kernel.context_compiler = context_compiler
    kernel.neuron_runtime = neuron_runtime
    return kernel


def build_brain(settings: Settings, kernel: OperationKernel) -> LightHouseBrain:
    if settings.model and settings.model_base_url and settings.model_api_key:
        provider = OpenAICompatibleProvider(
            base_url=settings.model_base_url,
            api_key=settings.model_api_key,
            model=settings.model,
            timeout=settings.model_timeout,
            json_mode=settings.model_json_mode,
            max_state_chars=settings.model_max_state_chars,
        )
    else:
        provider = DisabledProvider()
    state_repository = AgentRepositoryAdapter(
        PostgresAgentStore(settings.database_url),
        kernel.repository,
    )
    memory = getattr(kernel, "memory", None) or PostgresMemoryFabric(settings.database_url)
    agent_bus = getattr(kernel, "agent_bus", None) or PostgresAgentBus(settings.database_url)
    neuron_runtime = (
        getattr(kernel, "neuron_runtime", None)
        or AdaptivePostgresNeuronRuntime(settings.database_url)
    )
    agent_bus.register_builtin_agents()
    memory.bind_agent_bus(agent_bus)
    context_compiler = (
        getattr(kernel, "context_compiler", None)
        or NeuronAwareContextCompiler(memory, agent_bus, neuron_runtime)
    )
    brain = LightHouseBrain(
        state_repository,
        kernel,
        provider,
        memory=memory,
        agent_bus=agent_bus,
        context_compiler=context_compiler,
    )
    worker = BackgroundIntelligenceWorker(
        agent_bus=agent_bus,
        memory=memory,
        context_compiler=context_compiler,
        provider=provider,
        repository=kernel.repository,
    )
    neuron_worker = NeuronReflexWorker(neuron_runtime)
    worker.start()
    neuron_worker.start()
    brain.background_worker = _WorkerGroup(worker, neuron_worker)
    brain.memory_worker = worker
    brain.neuron_runtime = neuron_runtime
    brain.neuron_worker = neuron_worker
    return brain


build_agent_runtime = build_brain
