from __future__ import annotations

from importlib.resources import files

from .agent_adapter import AgentRepositoryAdapter
from .agent_store import PostgresAgentStore
from .brain import LightHouseBrain
from .capabilities import CapabilityRegistry, DEFAULT_CAPABILITIES
from .config import Settings
from .data_capabilities import DATA_KERNEL_CAPABILITIES
from .data_kernel import DataTargetResolver, PostgresDataCatalog
from .executors import DesktopExecutor, PostgresExecutor, ProjectFileExecutor, SystemExecutor
from .extra_capabilities import SYSTEM_TYPED_CAPABILITIES
from .kernel import OperationKernel
from .memory import PostgresMemoryFabric
from .provider import DisabledProvider, OpenAICompatibleProvider
from .repository import PostgresRepository


def migration_sql() -> str:
    root = files("lighthouse.sql")
    return "\n".join(
        root.joinpath(name).read_text(encoding="utf-8")
        for name in ("0001_core.sql", "0002_memory_runtime.sql")
    )


def build_kernel(settings: Settings, *, migrate: bool = True) -> OperationKernel:
    repository = PostgresRepository(settings.database_url)
    if migrate:
        repository.migrate(migration_sql())
    catalog = PostgresDataCatalog(settings.database_url)
    registry = CapabilityRegistry((*DEFAULT_CAPABILITIES, *SYSTEM_TYPED_CAPABILITIES, *DATA_KERNEL_CAPABILITIES))
    return OperationKernel(
        repository,
        registry,
        {
            "postgres": PostgresExecutor(catalog),
            "system": SystemExecutor(),
            "project_file": ProjectFileExecutor(),
            "desktop": DesktopExecutor(),
        },
        target_resolver=DataTargetResolver(catalog),
        data_catalog=catalog,
    )


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
    state_repository = AgentRepositoryAdapter(PostgresAgentStore(settings.database_url), kernel.repository)
    memory = PostgresMemoryFabric(settings.database_url)
    return LightHouseBrain(state_repository, kernel, provider, memory=memory)


build_agent_runtime = build_brain
