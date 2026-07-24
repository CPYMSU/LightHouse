from __future__ import annotations

from pathlib import Path
import subprocess
import sys
from typing import Any, Callable
from urllib.parse import unquote, urlparse

from ..models import Capability, ExecutionResult, Target


class DesktopExecutor:
    """Deterministic macOS Desktop Kernel executor.

    This first slice prefers semantic operating-system services over fragile
    coordinate-based mouse automation. macOS Launch Services opens URLs, files
    and allow-listed applications. Browser DOM automation can be added later as
    a Playwright/CDP adapter without weakening this capability boundary.
    """

    def __init__(
        self,
        *,
        platform: str | None = None,
        runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
    ) -> None:
        self.platform = platform or sys.platform
        self.runner = runner or subprocess.run

    def execute(
        self,
        capability: Capability,
        target: Target,
        arguments: dict[str, Any],
    ) -> ExecutionResult:
        self._require_macos(target)
        if capability.operation == "open_url":
            return self._open_url(target, arguments)
        if capability.operation == "open_file":
            return self._open_file(target, arguments)
        if capability.operation == "open_app":
            return self._open_app(target, arguments)
        raise ValueError(f"unsupported desktop operation: {capability.operation}")

    def _require_macos(self, target: Target) -> None:
        configured = str(target.config.get("platform") or "macos").lower()
        if configured != "macos":
            raise ValueError("desktop target platform must be macos")
        if self.platform != "darwin":
            raise RuntimeError("the macOS Desktop Executor can only run on macOS")

    @staticmethod
    def _allowed_apps(target: Target) -> set[str]:
        values = target.config.get("allowed_apps") or [
            "Safari",
            "Google Chrome",
            "Firefox",
            "Arc",
            "Finder",
        ]
        return {str(value).strip() for value in values if str(value).strip()}

    @staticmethod
    def _allowed_roots(target: Target) -> list[Path]:
        raw = target.config.get("allowed_roots") or [target.config.get("default_cwd") or "/"]
        return [Path(str(value)).expanduser().resolve() for value in raw]

    def _resolve_file(self, target: Target, value: Any) -> Path:
        text = str(value or "").strip()
        if not text or "\x00" in text:
            raise ValueError("file path is required")
        path = Path(text).expanduser()
        if not path.is_absolute():
            path = Path(str(target.config.get("default_cwd") or "/")) / path
        path = path.resolve()
        if not any(path == root or root in path.parents for root in self._allowed_roots(target)):
            raise PermissionError("desktop file is outside the target's allowed roots")
        if not path.exists() or not path.is_file():
            raise FileNotFoundError(f"desktop file does not exist: {path}")
        return path

    def _browser_argv(self, target: Target, browser: str | None) -> list[str]:
        selected = str(browser or target.config.get("browser") or "default").strip()
        if not selected or selected.lower() == "default":
            return ["/usr/bin/open"]
        if selected not in self._allowed_apps(target):
            raise PermissionError(f"desktop application is not allowed: {selected}")
        return ["/usr/bin/open", "-a", selected]

    def _run(self, argv: list[str], *, operation: str, subject: str) -> ExecutionResult:
        process = self.runner(
            argv,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            check=False,
        )
        result = {
            "operation": operation,
            "subject": subject,
            "argv": argv,
            "exit_code": int(process.returncode),
            "stdout": (process.stdout or "").strip(),
            "stderr": (process.stderr or "").strip(),
        }
        return ExecutionResult(ok=process.returncode == 0, result=result, exit_code=process.returncode)

    def _open_url(self, target: Target, arguments: dict[str, Any]) -> ExecutionResult:
        url = str(arguments.get("url") or "").strip()
        parsed = urlparse(url)
        allowed_schemes = {
            str(value).lower()
            for value in (target.config.get("allowed_schemes") or ["http", "https", "file"])
        }
        if parsed.scheme.lower() not in allowed_schemes:
            raise PermissionError(f"URL scheme is not allowed: {parsed.scheme or '(missing)'}")
        if parsed.scheme == "file":
            path = self._resolve_file(target, unquote(parsed.path))
            url = path.as_uri()
        elif parsed.scheme in {"http", "https"} and not parsed.netloc:
            raise ValueError("web URL requires a host")
        argv = [*self._browser_argv(target, arguments.get("browser")), url]
        return self._run(argv, operation="open_url", subject=url)

    def _open_file(self, target: Target, arguments: dict[str, Any]) -> ExecutionResult:
        path = self._resolve_file(target, arguments.get("path"))
        argv = [*self._browser_argv(target, arguments.get("browser")), str(path)]
        return self._run(argv, operation="open_file", subject=str(path))

    def _open_app(self, target: Target, arguments: dict[str, Any]) -> ExecutionResult:
        app = str(arguments.get("app") or "").strip()
        if not app:
            raise ValueError("application name is required")
        if app not in self._allowed_apps(target):
            raise PermissionError(f"desktop application is not allowed: {app}")
        return self._run(["/usr/bin/open", "-a", app], operation="open_app", subject=app)
