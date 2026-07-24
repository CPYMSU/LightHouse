from __future__ import annotations

import ntpath
import os
from pathlib import PurePosixPath, PureWindowsPath
import re
import subprocess
import time
from typing import Any

from ..models import Capability, ExecutionResult, Target


_SERVICE = re.compile(r"^[A-Za-z0-9_.@:-]{1,160}$")
_MAX_PATCH_BYTES = 2_000_000


class WindowsSystemExecutor:
    """Deterministic local Windows System Kernel backed by PowerShell."""

    def execute(
        self,
        capability: Capability,
        target: Target,
        arguments: dict[str, Any],
    ) -> ExecutionResult:
        self._require_windows_local(target)
        operation = capability.operation
        if operation == "project_context":
            return self._project_context(target, arguments)
        if operation == "file_read":
            return self._file_read(target, arguments)
        if operation == "file_search":
            return self._file_search(target, arguments)
        if operation == "file_patch":
            return self._file_patch(target, arguments)
        if operation == "shell_exec":
            return self._shell_exec(target, arguments)
        if operation == "test_run":
            return self._test_run(target, arguments)
        if operation == "service_status":
            return self._service_status(target, arguments)
        if operation == "service_restart":
            return self._service_restart(target, arguments)
        if operation == "journal_read":
            return self._journal_read(target, arguments)
        if operation == "git_status":
            return self._run(target, "git status --short --branch", cwd=self._cwd(target, arguments))
        if operation == "git_diff":
            return self._git_diff(target, arguments)
        if operation == "git_commit":
            return self._git_commit(target, arguments)
        raise ValueError(f"unsupported Windows system operation: {operation}")

    @staticmethod
    def _require_windows_local(target: Target) -> None:
        if str(target.config.get("transport") or "local").lower() != "local":
            raise RuntimeError("Windows System Executor supports local targets only")
        if str(target.config.get("platform") or "").lower() != "windows":
            raise RuntimeError("Windows System Executor requires platform=windows")

    @staticmethod
    def _relative_path(value: Any, *, default: str | None = None) -> str:
        text = str(default or "" if value is None or value == "" else value).strip().replace("\\", "/")
        if not text or text.startswith("/") or "\x00" in text:
            raise ValueError("path must be a non-empty relative path")
        parts = PurePosixPath(text).parts
        if any(part in {"", ".", ".."} for part in parts):
            raise ValueError("path may not contain dot or parent segments")
        return "/".join(parts)

    @staticmethod
    def _ps_quote(value: Any) -> str:
        return "'" + str(value).replace("'", "''") + "'"

    @staticmethod
    def _path(value: Any) -> str:
        text = os.path.expandvars(os.path.expanduser(str(value).strip()))
        return ntpath.normpath(ntpath.abspath(text))

    @staticmethod
    def _contains(path: str, root: str) -> bool:
        try:
            return ntpath.normcase(ntpath.commonpath([path, root])) == ntpath.normcase(root)
        except ValueError:
            return False

    def _roots(self, target: Target) -> list[str]:
        default = str(target.config.get("default_cwd") or "C:\\")
        raw = target.config.get("allowed_roots") or [default]
        roots: list[str] = []
        for item in raw:
            if not PureWindowsPath(str(item)).is_absolute():
                raise ValueError("system target allowed roots must be absolute Windows paths")
            roots.append(self._path(item))
        return roots

    def _cwd(self, target: Target, arguments: dict[str, Any]) -> str:
        raw = str(arguments.get("cwd") or target.config.get("default_cwd") or "C:\\").strip()
        if "\x00" in raw or not PureWindowsPath(raw).is_absolute():
            raise ValueError("cwd must be an absolute Windows path")
        cwd = self._path(raw)
        if not any(self._contains(cwd, root) for root in self._roots(target)):
            raise PermissionError("cwd is outside the target's allowed roots")
        return cwd

    @staticmethod
    def _timeout(target: Target, arguments: dict[str, Any]) -> int:
        timeout = int(arguments.get("timeout") or target.config.get("timeout") or 600)
        if not 1 <= timeout <= 3600:
            raise ValueError("timeout must be between 1 and 3600 seconds")
        return timeout

    @staticmethod
    def _max_output(target: Target) -> int:
        return max(4096, min(int(target.config.get("max_output_chars") or 131072), 2_000_000))

    @staticmethod
    def _truncate(text: str, limit: int) -> tuple[str, bool]:
        if len(text) <= limit:
            return text, False
        head = limit * 2 // 3
        tail = limit - head
        return text[:head] + "\n...<truncated>...\n" + text[-tail:], True

    def _argv(self, target: Target, script: str, cwd: str) -> list[str]:
        shell = str(target.config.get("shell") or "powershell.exe")
        wrapped = (
            "$ErrorActionPreference='Stop'; $global:LASTEXITCODE=0; "
            "try { Set-Location -LiteralPath " + self._ps_quote(cwd) + "; " + script
            + "; if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE } } "
            "catch { [Console]::Error.WriteLine($_.Exception.Message); exit 1 }"
        )
        return [
            shell,
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            wrapped,
        ]

    def _run(
        self,
        target: Target,
        script: str,
        *,
        cwd: str | None = None,
        timeout: int | None = None,
        stdin_text: str | None = None,
    ) -> ExecutionResult:
        cwd = cwd or self._cwd(target, {})
        timeout = timeout or int(target.config.get("timeout") or 600)
        argv = self._argv(target, script, cwd)
        started = time.monotonic()
        try:
            process = subprocess.run(
                argv,
                input=stdin_text,
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
                timeout=timeout,
                check=False,
                creationflags=int(getattr(subprocess, "CREATE_NO_WINDOW", 0)),
            )
            exit_code = process.returncode
            stdout = process.stdout or ""
            stderr = process.stderr or ""
        except subprocess.TimeoutExpired as exc:
            exit_code = 124
            stdout = (exc.stdout or "") if isinstance(exc.stdout, str) else ""
            stderr = (exc.stderr or "") if isinstance(exc.stderr, str) else ""
            stderr += f"\ncommand timed out after {timeout} seconds"
        except FileNotFoundError as exc:
            exit_code = 127
            stdout = ""
            stderr = str(exc)

        limit = self._max_output(target)
        stdout, stdout_truncated = self._truncate(stdout, limit)
        stderr, stderr_truncated = self._truncate(stderr, limit)
        result = {
            "transport": "local",
            "platform": "windows",
            "cwd": cwd,
            "exit_code": exit_code,
            "stdout": stdout,
            "stderr": stderr,
            "stdout_truncated": stdout_truncated,
            "stderr_truncated": stderr_truncated,
            "duration_ms": int((time.monotonic() - started) * 1000),
        }
        return ExecutionResult(ok=exit_code == 0, result=result, exit_code=exit_code)

    @staticmethod
    def _service(arguments: dict[str, Any]) -> str:
        service = str(arguments.get("service") or "").strip()
        if not _SERVICE.fullmatch(service):
            raise ValueError("service name is invalid")
        return service

    def _service_status(self, target: Target, arguments: dict[str, Any]) -> ExecutionResult:
        service = self._ps_quote(self._service(arguments))
        return self._run(
            target,
            f"Get-Service -Name {service} | Select-Object Name,DisplayName,Status,StartType | ConvertTo-Json -Compress",
        )

    def _service_restart(self, target: Target, arguments: dict[str, Any]) -> ExecutionResult:
        service = self._ps_quote(self._service(arguments))
        return self._run(
            target,
            f"Restart-Service -Name {service} -ErrorAction Stop; "
            f"Get-Service -Name {service} | Select-Object Name,DisplayName,Status,StartType | ConvertTo-Json -Compress",
        )

    def _journal_read(self, target: Target, arguments: dict[str, Any]) -> ExecutionResult:
        service = self._ps_quote(self._service(arguments))
        lines = max(1, min(int(arguments.get("lines") or 200), 5000))
        script = (
            f"$needle={service}; "
            "Get-WinEvent -LogName System -MaxEvents 5000 -ErrorAction SilentlyContinue | "
            "Where-Object { $_.ProviderName -like ('*' + $needle + '*') -or $_.Message -like ('*' + $needle + '*') } | "
            f"Select-Object -First {lines} TimeCreated,ProviderName,Id,LevelDisplayName,Message | "
            "Format-List | Out-String | Write-Output"
        )
        return self._run(target, script)

    def _shell_exec(self, target: Target, arguments: dict[str, Any]) -> ExecutionResult:
        command = str(arguments.get("command") or "").strip()
        if not command or "\x00" in command:
            raise ValueError("command is required")
        return self._run(
            target,
            command,
            cwd=self._cwd(target, arguments),
            timeout=self._timeout(target, arguments),
        )

    def _test_run(self, target: Target, arguments: dict[str, Any]) -> ExecutionResult:
        command = str(arguments.get("command") or target.config.get("test_command") or "").strip()
        if not command:
            raise ValueError("test command is required or must be configured on the target")
        return self._run(
            target,
            command,
            cwd=self._cwd(target, arguments),
            timeout=self._timeout(target, arguments),
        )

    def _file_read(self, target: Target, arguments: dict[str, Any]) -> ExecutionResult:
        cwd = self._cwd(target, arguments)
        path = self._relative_path(arguments.get("path"))
        max_bytes = max(1, min(int(arguments.get("max_bytes") or 262144), 2_000_000))
        native = path.replace("/", "\\")
        script = (
            f"$p=Join-Path (Get-Location) {self._ps_quote(native)}; "
            "$content=[System.IO.File]::ReadAllText($p,[System.Text.Encoding]::UTF8); "
            "[Console]::Out.Write($content)"
        )
        execution = self._run(target, script, cwd=cwd)
        if not execution.ok:
            return execution
        content = str(execution.result.get("stdout") or "")
        encoded = content.encode("utf-8", "replace")
        truncated = len(encoded) > max_bytes
        if truncated:
            content = encoded[:max_bytes].decode("utf-8", "replace")
        result = dict(execution.result)
        result.update({"path": path, "content": content, "truncated": truncated})
        result.pop("stdout", None)
        return ExecutionResult(ok=True, result=result, exit_code=0)

    def _file_search(self, target: Target, arguments: dict[str, Any]) -> ExecutionResult:
        cwd = self._cwd(target, arguments)
        query = str(arguments.get("query") or "").strip()
        if not query or "\x00" in query or "\n" in query:
            raise ValueError("search query is required and must be one line")
        raw_path = arguments.get("path")
        path = "." if raw_path is None or raw_path == "" else self._relative_path(raw_path)
        native = path.replace("/", "\\")
        max_results = max(1, min(int(arguments.get("max_results") or 200), 2000))
        script = (
            f"$root=Get-Item -LiteralPath {self._ps_quote(native)}; "
            "$files=if($root.PSIsContainer){Get-ChildItem -LiteralPath $root.FullName -File -Recurse -Force -ErrorAction SilentlyContinue}else{@($root)}; "
            f"$files | Select-String -SimpleMatch -Pattern {self._ps_quote(query)} -Encoding UTF8 | "
            f"Select-Object -First {max_results} | ForEach-Object {{ "
            "[Console]::Out.WriteLine(($_.Path + ':' + $_.LineNumber + ':' + $_.Line)) }"
        )
        execution = self._run(target, script, cwd=cwd)
        if execution.exit_code not in {0, 1}:
            return execution
        lines = [line for line in str(execution.result.get("stdout") or "").splitlines() if line]
        result = dict(execution.result)
        result.update({"query": query, "path": path, "matches": lines, "count": len(lines), "exit_code": 0})
        result.pop("stdout", None)
        return ExecutionResult(ok=True, result=result, exit_code=0)

    def _file_patch(self, target: Target, arguments: dict[str, Any]) -> ExecutionResult:
        patch = str(arguments.get("patch") or "")
        if not patch.strip():
            raise ValueError("patch is required")
        if len(patch.encode("utf-8")) > _MAX_PATCH_BYTES:
            raise ValueError("patch is too large")
        command = "git apply --check -" if bool(arguments.get("check", False)) else "git apply --whitespace=nowarn -"
        return self._run(target, command, cwd=self._cwd(target, arguments), stdin_text=patch)

    def _git_diff(self, target: Target, arguments: dict[str, Any]) -> ExecutionResult:
        staged = bool(arguments.get("staged", False))
        max_bytes = max(1, min(int(arguments.get("max_bytes") or 524288), 2_000_000))
        command = "git diff --no-ext-diff --binary" + (" --cached" if staged else "")
        execution = self._run(target, command, cwd=self._cwd(target, arguments))
        if not execution.ok:
            return execution
        diff = str(execution.result.get("stdout") or "")
        raw = diff.encode("utf-8", "replace")
        truncated = len(raw) > max_bytes
        if truncated:
            diff = raw[:max_bytes].decode("utf-8", "replace")
        result = dict(execution.result)
        result.update({"diff": diff, "staged": staged, "truncated": truncated})
        result.pop("stdout", None)
        return ExecutionResult(ok=True, result=result, exit_code=0)

    def _git_commit(self, target: Target, arguments: dict[str, Any]) -> ExecutionResult:
        message = str(arguments.get("message") or "").strip()
        if not message or "\x00" in message or len(message) > 500:
            raise ValueError("commit message is required and must be at most 500 characters")
        raw_paths = arguments.get("paths")
        if not isinstance(raw_paths, list) or not raw_paths:
            raise ValueError("git commit requires an explicit non-empty paths array")
        paths = [self._relative_path(item).replace("/", "\\") for item in raw_paths]
        quoted_paths = " ".join(self._ps_quote(item) for item in paths)
        command = (
            f"git add -- {quoted_paths}; "
            f"if ($LASTEXITCODE -eq 0) {{ git commit -m {self._ps_quote(message)} }}"
        )
        return self._run(target, command, cwd=self._cwd(target, arguments))

    def _project_context(self, target: Target, arguments: dict[str, Any]) -> ExecutionResult:
        cwd = self._cwd(target, arguments)
        max_files = max(1, min(int(arguments.get("max_files") or 1000), 5000))
        max_instruction_bytes = max(1024, min(int(arguments.get("max_instruction_bytes") or 65536), 262144))
        file_command = (
            "git rev-parse --is-inside-work-tree *> $null; "
            f"if($LASTEXITCODE -eq 0){{git ls-files -co --exclude-standard | Select-Object -First {max_files}}}else{{"
            "$base=(Get-Location).Path; Get-ChildItem -File -Recurse -Force -ErrorAction SilentlyContinue | "
            "Where-Object { $_.FullName -notmatch '[\\/]\\.git[\\/]' } | "
            "ForEach-Object { $_.FullName.Substring($base.Length).TrimStart('\\','/').Replace('\\','/') } | "
            f"Select-Object -First {max_files}}}; $global:LASTEXITCODE=0"
        )
        file_result = self._run(target, file_command, cwd=cwd)
        if not file_result.ok:
            return file_result
        files = [line for line in str(file_result.result.get("stdout") or "").splitlines() if line]

        instruction_names = target.config.get("project_instruction_files") or [
            "AGENTS.md", "AGENTS.override.md", "LIGHTHOUSE.md", ".lighthouse/project.yaml"
        ]
        instructions: list[dict[str, Any]] = []
        remaining = max_instruction_bytes
        for name in instruction_names:
            if remaining <= 0:
                break
            safe_name = self._relative_path(name)
            read = self._file_read(target, {"cwd": cwd, "path": safe_name, "max_bytes": remaining})
            if not read.ok:
                continue
            content = str(read.result.get("content") or "")
            if not content.strip():
                continue
            size = len(content.encode("utf-8"))
            instructions.append({
                "path": safe_name,
                "content": content,
                "truncated": bool(read.result.get("truncated")),
            })
            remaining -= min(size, remaining)

        status = self._run(target, "git status --short --branch", cwd=cwd)
        return ExecutionResult(
            ok=True,
            result={
                "transport": "local",
                "platform": "windows",
                "cwd": cwd,
                "files": files,
                "file_count": len(files),
                "instructions": instructions,
                "git_status": status.result if status.ok else {"error": status.result},
            },
            exit_code=0,
        )
