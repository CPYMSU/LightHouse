from __future__ import annotations

from typing import Any

from ..models import Capability, ExecutionResult, Target
from .agent_bus import AgentBusExecutor
from .desktop import DesktopExecutor
from .elastic_agent_bus import ElasticAgentBusExecutor
from .massive_build import MassiveBuildExecutor
from .mega_project import MegaProjectExecutor
from .postgres import PostgresExecutor
from .project_file import ProjectFileExecutor
from .research import ResearchExecutor
from .rust_system import RustBackedSystemExecutor
from .system import SystemExecutor as PosixSystemExecutor
from .windows_system import WindowsSystemExecutor


class SystemExecutor:
    """Route governed System operations to the target's native executor."""

    def __init__(
        self,
        *,
        posix: PosixSystemExecutor | None = None,
        windows: WindowsSystemExecutor | None = None,
        rust_kernel_binary: str | None = None,
    ) -> None:
        base_posix = posix or PosixSystemExecutor()
        self.posix = (
            RustBackedSystemExecutor(binary=rust_kernel_binary, fallback=base_posix)
            if rust_kernel_binary
            else base_posix
        )
        self.windows = windows or WindowsSystemExecutor()

    def execute(
        self,
        capability: Capability,
        target: Target,
        arguments: dict[str, Any],
    ) -> ExecutionResult:
        platform = str(target.config.get("platform") or "linux").strip().lower()
        executor = self.windows if platform == "windows" else self.posix
        return executor.execute(capability, target, arguments)


__all__ = [
    "AgentBusExecutor",
    "DesktopExecutor",
    "ElasticAgentBusExecutor",
    "MassiveBuildExecutor",
    "MegaProjectExecutor",
    "PostgresExecutor",
    "ProjectFileExecutor",
    "ResearchExecutor",
    "RustBackedSystemExecutor",
    "SystemExecutor",
    "PosixSystemExecutor",
    "WindowsSystemExecutor",
]
