from __future__ import annotations

from importlib.resources import files

from .capabilities import CapabilityRegistry
from .config import Settings
from .executors import PostgresExecutor, SystemExecutor
from .kernel import OperationKernel
from .repository import PostgresRepository


def migration_sql() -> str:
    return files("lighthouse.sql").joinpath("0001_core.sql").read_text(encoding="utf-8")


def build_kernel(settings: Settings, *, migrate: bool = True) -> OperationKernel:
    repository = PostgresRepository(settings.database_url)
    if migrate:
        repository.migrate(migration_sql())
    return OperationKernel(repository, CapabilityRegistry(), {"postgres": PostgresExecutor(), "system": SystemExecutor()})
