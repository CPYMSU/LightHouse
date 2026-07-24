from __future__ import annotations

import os
import posixpath
import re
import shlex
import subprocess
import time
from pathlib import Path, PurePosixPath
from typing import Any

from ..models import Capability, ExecutionResult, Target


_SERVICE = re.compile(r"^[A-Za-z0-9_.@:-]{1,160}$")
_TYPED_DIRECTORY_COMMAND = re.compile(r"^\s*(?:command\s+)?(?:sudo\s+)?mkdir(?:\s|$)", re.IGNORECASE)
_MAX_PATCH_BYTES = 2_000_000


class SystemExecutor:
    """Local/OpenSSH Linux executor.

    Typed read operations are converted to fixed command shapes. Arbitrary shell,
    patch, test and commit operations remain capabilities that require the
    Operation Kernel's confirmation policy before this executor is reached.
    """

    def execute(
        self,
        capability: Capability,
        target: Target,
        arguments: dict[str, Any],
    ) -> ExecutionResult:
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
            service = self._service(arguments)
            return self._run(target, f"systemctl status --no-pager --full -- {shlex.quote(service)}")
        if operation == "service_restart":
            service = self._service(arguments)
            return self._run(target, f"systemctl restart -- {shlex.quote(service)}")
        if operation == "journal_read":
            service = self._service(arguments)
            lines = max(1, min(int(arguments.get("lines") or 200), 5000))
            return self._run(
                target,
                "journalctl --no-pager -o short-iso "
                f"-n {lines} -u {shlex.quote(service)}",
            )
        if operation == "git_status":
            cwd = self._cwd(target, arguments)
            return self._run(target, "git status --short --branch", cwd=cwd)
        if operation == "git_diff":
            return self._git_diff(target, arguments)
        if operation == "git_commit":
            return self._git_commit(target, arguments)
        raise ValueError(f"unsupported system operation: {operation}")

    @staticmethod
    def _service(arguments: dict[str, Any]) -> str:
        service = str(arguments.get("service") or "").strip()
        if not _SERVICE.fullmatch(service):
            raise ValueError("service name is invalid")
        return service

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
    def _configured_roots(target: Target) -> list[str]:
        raw = target.config.get("allowed_roots") or [target.config.get("default_cwd") or "/"]
        roots = []
        for item in raw:
            normalized = posixpath.normpath(str(item))
            if not normalized.startswith("/"):
                raise ValueError("system target allowed roots must be absolute")
            roots.append(normalized)
        return roots

    def _cwd(self, target: Target, arguments: dict[str, Any]) -> str:
        raw = str(arguments.get("cwd") or target.config.get("default_cwd") or "/").strip()
        cwd = posixpath.normpath(raw)
        if not cwd.startswith("/") or "\x00" in cwd:
            raise ValueError("cwd must be an absolute path")
        roots = self._configured_roots(target)
        if not any(cwd == root or cwd.startswith(root.rstrip("/") + "/") for root in roots):
            raise PermissionError("cwd is outside the target's allowed roots")
        if str(target.config.get("transport") or "local").lower() == "local":
            path = Path(cwd)
            if not path.exists() or not path.is_dir() or path.is_symlink():
                raise ValueError("cwd must be a real regular directory")
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

    def _ssh_argv(self, target: Target) -> list[str]:
        config = target.config
        host = str(config.get("host") or "").strip()
        user = str(config.get("user") or "").strip()
        if not host or not user:
            raise ValueError("SSH target requires host and user")
        argv = [
            "ssh",
            "-p",
            str(int(config.get("port") or 22)),
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=15",
            "-o",
            "IdentitiesOnly=yes",
        ]
        strict = bool(config.get("strict_host_key", True))
        argv.extend(["-o", f"StrictHostKeyChecking={'yes' if strict else 'accept-new'}"])
        identity_env = str(config.get("identity_file_env") or "").strip()
        if identity_env:
            identity_path = os.environ.get(identity_env, "").strip()
            if not identity_path:
                raise ValueError(f"SSH identity environment variable is missing: {identity_env}")
            path = Path(identity_path).expanduser()
            if not path.is_absolute() or not path.is_file() or path.is_symlink():
                raise ValueError("SSH identity file must be an absolute regular file")
            argv.extend(["-i", str(path)])
        known_hosts_env = str(config.get("known_hosts_env") or "").strip()
        if known_hosts_env:
            known_hosts = os.environ.get(known_hosts_env, "").strip()
            if not known_hosts:
                raise ValueError(f"known-hosts environment variable is missing: {known_hosts_env}")
            path = Path(known_hosts).expanduser()
            if not path.is_absolute() or not path.is_file() or path.is_symlink():
                raise ValueError("known-hosts file must be an absolute regular file")
            argv.extend(["-o", f"UserKnownHostsFile={path}"])
        argv.extend(["--", f"{user}@{host}"])
        return argv

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
        shell = str(target.config.get("shell") or "/bin/bash")
        full_script = f"cd -- {shlex.quote(cwd)} && {script}"
        transport = str(target.config.get("transport") or "local").lower()
        if transport == "local":
            argv = [shell, "-lc", full_script]
        elif transport == "ssh":
            remote = f"{shlex.quote(shell)} -lc {shlex.quote(full_script)}"
            argv = [*self._ssh_argv(target), remote]
        else:
            raise ValueError("system target transport must be local or ssh")

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
            "transport": transport,
            "cwd": cwd,
            "exit_code": exit_code,
            "stdout": stdout,
            "stderr": stderr,
            "stdout_truncated": stdout_truncated,
            "stderr_truncated": stderr_truncated,
            "duration_ms": int((time.monotonic() - started) * 1000),
        }
        return ExecutionResult(ok=exit_code == 0, result=result, exit_code=exit_code)

    def _shell_exec(self, target: Target, arguments: dict[str, Any]) -> ExecutionResult:
        command = str(arguments.get("command") or "").strip()
        if not command or "\x00" in command:
            raise ValueError("command is required")
        if _TYPED_DIRECTORY_COMMAND.match(command):
            raise ValueError("mkdir must use system.directory.create.v1 so the path is typed and grounded")
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
        command = f"head -c {max_bytes + 1} -- {shlex.quote(path)}"
        execution = self._run(target, command, cwd=cwd)
        if not execution.ok:
            return execution
        content = str(execution.result.get("stdout") or "")
        truncated = len(content.encode("utf-8", "replace")) > max_bytes
        if truncated:
            content = content.encode("utf-8", "replace")[:max_bytes].decode("utf-8", "replace")
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
        max_results = max(1, min(int(arguments.get("max_results") or 200), 2000))
        quoted_query = shlex.quote(query)
        quoted_path = shlex.quote(path)
        command = (
            "if command -v rg >/dev/null 2>&1; then "
            f"rg -n --no-heading --color never -F -- {quoted_query} {quoted_path}; "
            "else "
            f"grep -RFn -- {quoted_query} {quoted_path}; "
            f"fi | head -n {max_results}"
        )
        execution = self._run(target, command, cwd=cwd)
        if execution.exit_code not in {0, 1}:
            return execution
        lines = [line for line in str(execution.result.get("stdout") or "").splitlines() if line]
        result = dict(execution.result)
        result.update({"query": query, "path": path, "matches": lines, "count": len(lines)})
        result.pop("stdout", None)
        result["exit_code"] = 0
        return ExecutionResult(ok=True, result=result, exit_code=0)

    def _file_patch(self, target: Target, arguments: dict[str, Any]) -> ExecutionResult:
        cwd = self._cwd(target, arguments)
        patch = str(arguments.get("patch") or "")
        if not patch.strip():
            raise ValueError("patch is required")
        if len(patch.encode("utf-8")) > _MAX_PATCH_BYTES:
            raise ValueError("patch is too large")
        command = "git apply --whitespace=nowarn --recount"
        if bool(arguments.get("check", False)):
            command += " --check"
        return self._run(target, command, cwd=cwd, stdin_text=patch)

    def _git_diff(self, target: Target, arguments: dict[str, Any]) -> ExecutionResult:
        cwd = self._cwd(target, arguments)
        staged = bool(arguments.get("staged", False))
        max_bytes = max(4096, min(int(arguments.get("max_bytes") or 524288), 2_000_000))
        command = "git --no-pager diff --no-ext-diff"
        if staged:
            command += " --cached"
        execution = self._run(target, command, cwd=cwd)
        diff = str(execution.result.get("stdout") or "")
        if len(diff.encode("utf-8", "replace")) > max_bytes:
            diff = diff.encode("utf-8", "replace")[:max_bytes].decode("utf-8", "replace")
        result = dict(execution.result)
        result.update({"staged": staged, "diff": diff})
        result.pop("stdout", None)
        return ExecutionResult(ok=execution.ok, result=result, exit_code=execution.exit_code)

    def _git_commit(self, target: Target, arguments: dict[str, Any]) -> ExecutionResult:
        cwd = self._cwd(target, arguments)
        message = str(arguments.get("message") or "").strip()
        if not message or "\x00" in message or "\n" in message:
            raise ValueError("commit message must be one non-empty line")
        paths = arguments.get("paths") or []
        if not isinstance(paths, list):
            raise ValueError("paths must be an array")
        safe_paths = [self._relative_path(item) for item in paths]
        if safe_paths:
            add = "git add -- " + " ".join(shlex.quote(item) for item in safe_paths)
        else:
            add = "git add -u"
        command = f"{add} && git commit -m {shlex.quote(message)}"
        return self._run(target, command, cwd=cwd)

    def _project_context(self, target: Target, arguments: dict[str, Any]) -> ExecutionResult:
        cwd = self._cwd(target, arguments)
        max_files = max(1, min(int(arguments.get("max_files") or 500), 5000))
        max_instruction_bytes = max(1024, min(int(arguments.get("max_instruction_bytes") or 131072), 1_000_000))
        instructions = []
        used = 0
        for name in target.config.get("project_instruction_files") or []:
            quoted = shlex.quote(name)
            probe = self._run(target, f"test -f -- {quoted} && head -c {max_instruction_bytes} -- {quoted} || true", cwd=cwd)
            content = str(probe.result.get("stdout") or "")
            if not content:
                continue
            remaining = max_instruction_bytes - used
            if remaining <= 0:
                break
            encoded = content.encode("utf-8", "replace")[:remaining]
            content = encoded.decode("utf-8", "replace")
            used += len(encoded)
            instructions.append({"path": name, "content": content})
        index_command = (
            "if command -v fd >/dev/null 2>&1; then "
            f"fd -t f -H -E .git . | head -n {max_files}; "
            "elif command -v find >/dev/null 2>&1; then "
            f"find . -type f -not -path './.git/*' -print | sed 's#^./##' | head -n {max_files}; "
            "else true; fi"
        )
        index_execution = self._run(target, index_command, cwd=cwd)
        files = [line for line in str(index_execution.result.get("stdout") or "").splitlines() if line]
        return ExecutionResult(
            ok=True,
            result={
                "cwd": cwd,
                "files": files,
                "file_count": len(files),
                "instructions": instructions,
                "instructions_bytes": used,
            },
            exit_code=0,
        )
