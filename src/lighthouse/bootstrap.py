from __future__ import annotations

from importlib.resources import files
import os
from typing import Any

from .agent_adapter import AgentRepositoryAdapter
from .agent_capabilities import AGENT_BUS_CAPABILITIES
from .agent_registry import AgentBus2Registry
from .agent_store import PostgresAgentStore
from .capabilities import CapabilityRegistry, DEFAULT_CAPABILITIES
from .config import Settings
from .data_capabilities import DATA_KERNEL_CAPABILITIES
from .data_kernel import DataTargetResolver, PostgresDataCatalog
from .execution_observability import ObservableBackgroundIntelligenceWorker
from .executors import (
    DesktopExecutor,
    ElasticAgentBusExecutor,
    MassiveBuildExecutor,
    MegaProjectExecutor,
    PostgresExecutor,
    ProjectFileExecutor,
    ResearchExecutor,
    SystemExecutor,
)
from .extra_capabilities import SYSTEM_TYPED_CAPABILITIES
from .intensity_provider import IntensityAwareAgentBusProvider
from .kernel import OperationKernel
from .massive_build import PostgresMassiveBuildStore
from .massive_build_capabilities import MASSIVE_BUILD_CAPABILITIES
from .mega_brain import MegaProjectLightHouseBrain
from .mega_context import MegaProjectContextCompiler
from .mega_project_capabilities import MEGA_PROJECT_CAPABILITIES
from .mega_projects import PostgresMegaProjectStore
from .memory_search import PostgresMemoryFabric
from .model_usage import PostgresModelUsageStore
from .neuron_adaptation import AdaptivePostgresNeuronRuntime
from .neuron_runtime import NeuronReflexWorker
from .provider import DisabledProvider
from .repository import PostgresRepository
from .research_capabilities import RESEARCH_CAPABILITIES
from .tool_registry import PostgresToolRegistry


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
            "0005_neuron_trigger_hardening.sql",
            "0006_tool_registry_mega_projects.sql",
            "0007_agent_observatory_massive_build.sql",
            "0008_operation_event_sequence.sql",
            "0009_persistent_emergent_personality.sql",
        )
    )


def build_kernel(settings: Settings, *, migrate: bool = True) -> OperationKernel:
    repository = PostgresRepository(settings.database_url)
    if migrate:
        repository.migrate(migration_sql())
    catalog = PostgresDataCatalog(settings.database_url)
    memory = PostgresMemoryFabric(settings.database_url)
    agent_bus = AgentBus2Registry(settings.database_url)
    neuron_runtime = AdaptivePostgresNeuronRuntime(settings.database_url)
    tool_registry = PostgresToolRegistry(settings.database_url)
    project_store = PostgresMegaProjectStore(settings.database_url)
    massive_build = PostgresMassiveBuildStore(settings.database_url)
    usage_store = PostgresModelUsageStore(settings.database_url)
    agent_bus.register_builtin_agents()
    memory.bind_agent_bus(agent_bus)
    context_compiler = MegaProjectContextCompiler(
        memory,
        agent_bus,
        neuron_runtime,
        tool_registry,
        project_store,
        massive_build,
    )
    registry = CapabilityRegistry(
        (
            *DEFAULT_CAPABILITIES,
            *SYSTEM_TYPED_CAPABILITIES,
            *DATA_KERNEL_CAPABILITIES,
            *AGENT_BUS_CAPABILITIES,
            *MEGA_PROJECT_CAPABILITIES,
            *MASSIVE_BUILD_CAPABILITIES,
            *RESEARCH_CAPABILITIES,
        )
    )
    tool_registry.sync_capabilities(registry.list())
    agent_bus_executor = ElasticAgentBusExecutor(
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
            "mega_project": MegaProjectExecutor(
                tool_registry=tool_registry,
                project_store=project_store,
            ),
            "massive_build": MassiveBuildExecutor(store=massive_build),
            "research": ResearchExecutor(),
        },
        target_resolver=DataTargetResolver(catalog),
        data_catalog=catalog,
    )
    kernel.memory = memory
    kernel.agent_bus = agent_bus
    kernel.context_compiler = context_compiler
    kernel.neuron_runtime = neuron_runtime
    kernel.tool_registry = tool_registry
    kernel.mega_projects = project_store
    kernel.massive_build = massive_build
    kernel.usage_store = usage_store
    return kernel


def build_brain(settings: Settings, kernel: OperationKernel) -> MegaProjectLightHouseBrain:
    usage_store = getattr(kernel, "usage_store", None) or PostgresModelUsageStore(
        settings.database_url
    )
    if settings.model and settings.model_base_url and settings.model_api_key:
        provider = IntensityAwareAgentBusProvider(
            base_url=settings.model_base_url,
            api_key=settings.model_api_key,
            model=settings.model,
            timeout=settings.model_timeout,
            json_mode=settings.model_json_mode,
            max_state_chars=settings.model_max_state_chars,
            usage_recorder=usage_store.record,
            transport_retries=1,
        )
    else:
        provider = DisabledProvider()
    state_repository = AgentRepositoryAdapter(
        PostgresAgentStore(settings.database_url),
        kernel.repository,
    )
    memory = getattr(kernel, "memory", None) or PostgresMemoryFabric(
        settings.database_url
    )
    agent_bus = getattr(kernel, "agent_bus", None) or AgentBus2Registry(
        settings.database_url
    )
    neuron_runtime = getattr(
        kernel, "neuron_runtime", None
    ) or AdaptivePostgresNeuronRuntime(settings.database_url)
    tool_registry = getattr(kernel, "tool_registry", None) or PostgresToolRegistry(
        settings.database_url
    )
    project_store = getattr(kernel, "mega_projects", None) or PostgresMegaProjectStore(
        settings.database_url
    )
    massive_build = getattr(
        kernel, "massive_build", None
    ) or PostgresMassiveBuildStore(settings.database_url)
    agent_bus.register_builtin_agents()
    memory.bind_agent_bus(agent_bus)
    context_compiler = getattr(
        kernel, "context_compiler", None
    ) or MegaProjectContextCompiler(
        memory,
        agent_bus,
        neuron_runtime,
        tool_registry,
        project_store,
        massive_build,
    )
    brain = MegaProjectLightHouseBrain(
        state_repository,
        kernel,
        provider,
        memory=memory,
        agent_bus=agent_bus,
        context_compiler=context_compiler,
    )
    brain.usage_store = usage_store
    brain.mega_projects = project_store
    brain.massive_build = massive_build
    brain.code_foundry_mode = settings.code_foundry_mode

    try:
        requested_workers = int(os.getenv("LIGHTHOUSE_AGENT_WORKERS", "8"))
    except ValueError:
        requested_workers = 8
    worker_count = max(1, min(requested_workers, 64))
    workers = [
        ObservableBackgroundIntelligenceWorker(
            agent_bus=agent_bus,
            memory=memory,
            context_compiler=context_compiler,
            provider=provider,
            repository=kernel.repository,
            kernel=kernel,
            run_repository=state_repository,
            project_store=project_store,
            massive_build=massive_build,
        )
        for _ in range(worker_count)
    ]
    neuron_worker = NeuronReflexWorker(neuron_runtime)
    for worker in workers:
        worker.start()
    neuron_worker.start()
    brain.background_worker = _WorkerGroup(*workers, neuron_worker)
    brain.memory_worker = workers[0]
    brain.agent_workers = workers
    brain.neuron_runtime = neuron_runtime
    brain.neuron_worker = neuron_worker
    brain.tool_registry = tool_registry
    return brain


build_agent_runtime = build_brain
