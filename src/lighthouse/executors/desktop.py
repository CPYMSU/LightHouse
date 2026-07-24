from __future__ import annotations

import ntpath
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Any, Callable
from urllib.parse import unquote, urlparse

from ..models import Capability, ExecutionResult, Target


class DesktopExecutor:
    """Deterministic macOS and Windows Desktop Kernel executor.

    The executor uses semantic operating-system launch services instead of mouse
    coordinates: macOS `/usr/bin/open` and Windows PowerShell `Start-Process`.
    Targets still constrain roots, URL schemes and executable/application names.
    """

    def __init__(
        self,
        *,
        platform: str | None = None,
        runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
        path_exists: Callable[[str], bool] | None = None,
        path_is_file: Callable[[str], bool] | None = None,
    ) -> None:
        self.platform = platform or sys.platform
        self.runner = runner or subprocess.run
        self.path_exists = path_exists or os.path.exists
        self.path_is_file = path_is_file or os.path.isfile

    def execute(
        self,
        capability: Capability,
        target: Target,
        arguments: dict[str, Any],
    ) -> ExecutionResult:
        configured = self._require_platform(target)
        if capability.operation == "open_url":
            return self._open_url(configured, target, arguments)
        if capability.operation == "open_file":
            return self._open_file(configured, target, arguments)
        if capability.operation == "open_app":
            return self._open_app(configured, target, arguments)
        raise ValueError(f"unsupported desktop operation: {capability.operation}")

    def _require_platform(self, target: Target) -> str:
        configured = str(target.config.get("platform") or "macos").lower()
        if configured not in {"macos", "windows"}:
            raise ValueError("desktop target platform must be macos or windows")
        host = "macos" if self.platform == "darwin" else "windows" if self.platform == "win32" else "other"
        if configured != host:
            raise RuntimeError(f"the {configured} Desktop Executor cannot run on {self.platform}")
        return configured

    @staticmethod
    def _allowed_apps(target: Target) -> set[str]:
        platform = str(target.config.get("platform") or "macos").lower()
        defaults = (
            ["explorer.exe", "msedge.exe", "chrome.exe", "firefox.exe", "notepad.exe", "code.exe"]
            if platform == "windows"
            else ["Safari", "Google Chrome", "Firefox", "Arc", "Finder"]
        )
        values = target.config.get("allowed_apps") or defaults
        return {str(value).strip() for value in values if str(value).strip()}

    @staticmethod
    def _mac_roots(target: Target) -> list[Path]:
        raw = target.config.get("allowed_roots") or [target.config.get("default_cwd") or "/"]
        return [Path(str(value)).expanduser().resolve() for value in raw]

    @staticmethod
    def _windows_path(value: str, *, base: str | None = None) -> str:
        text = os.path.expandvars(os.path.expanduser(value.strip()))
        if base and not ntpath.isabs(text):
            text = ntpath.join(base, text)
        return ntpath.normpath(ntpath.abspath(text))

    @classmethod
    def _windows_roots(cls, target: Target) -> list[str]:
        default = str(target.config.get("default_cwd") or "C:\\")
        raw = target.config.get("allowed_roots") or [default]
        return [cls._windows_path(str(value)) for value in raw]

    @staticmethod
    def _windows_contains(path: str, root: str) -> bool:
        try:
            return ntpath.normcase(ntpath.commonpath([path, root])) == ntpath.normcase(root)
        except ValueError:
            return False

    def _resolve_file(self, platform: str, target: Target, value: Any) -> str:
        text = str(value or "").strip()
        if not text or "\x00" in text:
            raise ValueError("file path is required")
        if platform == "windows":
            default = str(target.config.get("default_cwd") or "C:\\")
            path = self._windows_path(text, base=default)
            if not any(self._windows_contains(path, root) for root in self._windows_roots(target)):
                raise PermissionError("desktop file is outside the target's allowed roots")
            if not self.path_exists(path) or not self.path_is_file(path):
                raise FileNotFoundError(f"desktop file does not exist: {path}")
            return path

        path = Path(text).expanduser()
        if not path.is_absolute():
            path = Path(str(target.config.get("default_cwd") or "/")) / path
        path = path.resolve()
        if not any(path == root or root in path.parents for root in self._mac_roots(target)):
            raise PermissionError("desktop file is outside the target's allowed roots")
        if not path.exists() or not path.is_file():
            raise FileNotFoundError(f"desktop file does not exist: {path}")
        return str(path)

    @staticmethod
    def _ps_quote(value: str) -> str:
        return "'" + value.replace("'", "''") + "'"

    def _mac_browser_argv(self, target: Target, browser: str | None) -> list[str]:
        selected = str(browser or target.config.get("browser") or "default").strip()
        if not selected or selected.lower() == "default":
            return ["/usr/bin/open"]
        if selected not in self._allowed_apps(target):
            raise PermissionError(f"desktop application is not allowed: {selected}")
        return ["/usr/bin/open", "-a", selected]

    def _windows_argv(self, *, subject: str, app: str | None = None) -> list[str]:
        if app:
            script = (
                f"Start-Process -FilePath {self._ps_quote(app)} "
                f"-ArgumentList @({self._ps_quote(subject)})"
            )
        else:
            script = f"Start-Process -FilePath {self._ps_quote(subject)}"
        return [
            "powershell.exe",
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            script,
        ]

    def _browser_argv(self, platform: str, target: Target, browser: str | None, subject: str) -> list[str]:
        selected = str(browser or target.config.get("browser") or "default").strip()
        if platform == "macos":
            return [*self._mac_browser_argv(target, selected), subject]
        if not selected or selected.lower() == "default":
            return self._windows_argv(subject=subject)
        if selected not in self._allowed_apps(target):
            raise PermissionError(f"desktop application is not allowed: {selected}")
        return self._windows_argv(subject=subject, app=selected)

    def _run(self, argv: list[str], *, operation: str, subject: str) -> ExecutionResult:
        process = self.runner(
            argv,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            check=False,
            creationflags=int(getattr(subprocess, "CREATE_NO_WINDOW", 0)) if self.platform == "win32" else 0,
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

    def _open_url(self, platform: str, target: Target, arguments: dict[str, Any]) -> ExecutionResult:
        url = str(arguments.get("url") or "").strip()
        parsed = urlparse(url)
        allowed_schemes = {
            str(value).lower()
            for value in (target.config.get("allowed_schemes") or ["http", "https", "file"])
        }
        if parsed.scheme.lower() not in allowed_schemes:
            raise PermissionError(f"URL scheme is not allowed: {parsed.scheme or '(missing)'}")
        if parsed.scheme == "file":
            raw_path = unquote(parsed.path)
            if platform == "windows" and re.match(r"^/[A-Za-z]:/", raw_path):
                raw_path = raw_path[1:]
            subject = self._resolve_file(platform, target, raw_path)
            url = Path(subject).as_uri() if platform == "macos" else "file:///" + subject.replace("\\", "/")
        elif parsed.scheme in {"http", "https"} and not parsed.netloc:
            raise ValueError("web URL requires a host")
        argv = self._browser_argv(platform, target, arguments.get("browser"), url)
        return self._run(argv, operation="open_url", subject=url)

    def _open_file(self, platform: str, target: Target, arguments: dict[str, Any]) -> ExecutionResult:
        path = self._resolve_file(platform, target, arguments.get("path"))
        argv = self._browser_argv(platform, target, arguments.get("browser"), path)
        return self._run(argv, operation="open_file", subject=path)

    def _open_app(self, platform: str, target: Target, arguments: dict[str, Any]) -> ExecutionResult:
        app = str(arguments.get("app") or "").strip()
        if not app:
            raise ValueError("application name is required")
        if app not in self._allowed_apps(target):
            raise PermissionError(f"desktop application is not allowed: {app}")
        argv = ["/usr/bin/open", "-a", app] if platform == "macos" else self._windows_argv(subject=app)
        return self._run(argv, operation="open_app", subject=app)
