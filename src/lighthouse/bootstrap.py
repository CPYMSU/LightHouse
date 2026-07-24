from __future__ import annotations

from importlib.resources import files

from .agent_adapter import AgentRepositoryAdapter
from .agent_store import PostgresAgentStore
from .brain import LightHouseBrain
from .capabilities import CapabilityRegistry, DEFAULT_CAPABILITIES
from .config import Settings
from .executors import DesktopExecutor, PostgresExecutor, ProjectFileExecutor, SystemExecutor
from .extra_capabilities import PROJECT_FILE_WRITE_CAPABILITY
from .kernel import OperationKernel
from .provider import DisabledProvider, OpenAICompatibleProvider
from .repository import PostgresRepository


def migration_sql() -> str:
    return files("lighthouse.sql").joinpath("0001_core.sql").read_text(encoding="utf-8")


def build_kernel(settings: Settings, *, migrate: bool = True) -> OperationKernel:
    repository = PostgresRepository(settings.database_url)
    if migrate:
        repository.migrate(migration_sql())
    registry = CapabilityRegistry((*DEFAULT_CAPABILITIES, PROJECT_FILE_WRITE_CAPABILITY))
    return OperationKernel(
        repository,
        registry,
        {
            "postgres": PostgresExecutor(),
            "system": SystemExecutor(),
            "project_file": ProjectFileExecutor(),
            "desktop": DesktopExecutor(),
        },
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
    return LightHouseBrain(state_repository, kernel, provider)


build_agent_runtime = build_brain
