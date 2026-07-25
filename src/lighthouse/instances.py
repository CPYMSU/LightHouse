from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import signal
import socket
import subprocess
import sys
import time
from typing import Any

import httpx


DEFAULT_INSTANCE_ID = "default"
DEFAULT_START_PORT = 8787
MAX_PORT = 65535
_TRANSIENT_CONFIG_KEYS = {
    "conversation_id",
    "desktop_target",
    "memory_scanned_workspace",
    "project_path",
    "system_target",
    "workspace",
    "workspace_name",
}


class InstanceError(RuntimeError):
    pass


def lighthouse_home() -> Path:
    return Path(os.environ.get("LIGHTHOUSE_HOME") or Path.home() / ".lighthouse").expanduser().resolve()


def base_config_path() -> Path:
    return lighthouse_home() / "config.json"


def instances_root() -> Path:
    return lighthouse_home() / "instances"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, UnicodeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if os.name != "nt":
        temporary.chmod(0o600)
    os.replace(temporary, path)
    if os.name != "nt":
        path.chmod(0o600)


def _slug(value: str) -> str:
    result = re.sub(r"[^a-z0-9]+", "-", str(value or "").strip().lower()).strip("-")
    return result[:48] or "instance"


def _automatic_name() -> str:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return f"instance-{stamp}-{os.urandom(2).hex()}"


def _instance_dir(instance_id: str) -> Path:
    return instances_root() / instance_id


def _record_path(instance_id: str) -> Path:
    return _instance_dir(instance_id) / "instance.json"


def _config_path(instance_id: str) -> Path:
    return base_config_path() if instance_id == DEFAULT_INSTANCE_ID else _instance_dir(instance_id) / "config.json"


def _port_from_url(value: str) -> int | None:
    try:
        parsed = httpx.URL(value)
    except Exception:
        return None
    return parsed.port


def port_is_free(port: int, host: str = "127.0.0.1") -> bool:
    if not 1 <= int(port) <= MAX_PORT:
        return False
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    try:
        with socket.socket(family, socket.SOCK_STREAM) as handle:
            handle.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            handle.bind((host, int(port)))
    except OSError:
        return False
    return True


def find_free_port(start: int = DEFAULT_START_PORT, host: str = "127.0.0.1") -> int:
    start = max(1, int(start))
    for port in range(start, MAX_PORT + 1):
        if port_is_free(port, host):
            return port
    for port in range(1024, start):
        if port_is_free(port, host):
            return port
    raise InstanceError("no free local TCP port is available for a new LightHouse instance")


@dataclass
class InstanceRecord:
    id: str
    name: str
    port: int
    url: str
    config_path: str
    log_dir: str
    kind: str
    platform: str
    created_at: str
    project_path: str | None = None
    pid: int | None = None
    stopped_at: str | None = None

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "InstanceRecord":
        return cls(
            id=str(value["id"]),
            name=str(value.get("name") or value["id"]),
            port=int(value["port"]),
            url=str(value.get("url") or f"http://127.0.0.1:{int(value['port'])}"),
            config_path=str(value["config_path"]),
            log_dir=str(value["log_dir"]),
            kind=str(value.get("kind") or "managed"),
            platform=str(value.get("platform") or sys.platform),
            created_at=str(value.get("created_at") or _utc_now()),
            project_path=str(value["project_path"]) if value.get("project_path") else None,
            pid=int(value["pid"]) if value.get("pid") else None,
            stopped_at=str(value["stopped_at"]) if value.get("stopped_at") else None,
        )

    def save(self) -> None:
        _write_json(_record_path(self.id), asdict(self))

    def public(self) -> dict[str, Any]:
        value = asdict(self)
        value["status"] = instance_status(self)
        return value


def ensure_default_instance() -> InstanceRecord:
    config_path = base_config_path()
    config = _read_json(config_path)
    preferred = int(config.get("port") or _port_from_url(str(config.get("url") or "")) or DEFAULT_START_PORT)
    record_value = _read_json(_record_path(DEFAULT_INSTANCE_ID))
    created_at = str(record_value.get("created_at") or _utc_now())
    config.setdefault("instance_id", DEFAULT_INSTANCE_ID)
    config.setdefault("instance_name", DEFAULT_INSTANCE_ID)
    config.setdefault("instance_kind", "system")
    config.setdefault("host", "127.0.0.1")
    config["port"] = preferred
    config["url"] = f"http://127.0.0.1:{preferred}"
    if config:
        _write_json(config_path, config)
    record = InstanceRecord(
        id=DEFAULT_INSTANCE_ID,
        name=str(config.get("instance_name") or DEFAULT_INSTANCE_ID),
        port=preferred,
        url=str(config.get("url") or f"http://127.0.0.1:{preferred}"),
        config_path=str(config_path),
        log_dir=str(lighthouse_home() / "logs"),
        kind="system",
        platform=sys.platform,
        created_at=created_at,
        project_path=str(config["project_path"]) if config.get("project_path") else None,
        pid=None,
    )
    record.save()
    return record


def list_instances() -> list[InstanceRecord]:
    records: dict[str, InstanceRecord] = {}
    if base_config_path().exists():
        default = ensure_default_instance()
        records[default.id] = default
    root = instances_root()
    if root.exists():
        for path in sorted(root.glob("*/instance.json")):
            value = _read_json(path)
            try:
                record = InstanceRecord.from_dict(value)
            except (KeyError, TypeError, ValueError):
                continue
            records[record.id] = record
    return sorted(records.values(), key=lambda item: (item.id != DEFAULT_INSTANCE_ID, item.created_at, item.id))


def resolve_instance(name: str) -> InstanceRecord:
    requested = str(name or "").strip().lower()
    for record in list_instances():
        if record.id.lower() == requested or record.name.lower() == requested:
            return record
    raise InstanceError(f"unknown LightHouse instance: {name}")


def _health(record: InstanceRecord, timeout: float = 0.75) -> bool:
    try:
        with httpx.Client(timeout=timeout, trust_env=False) as client:
            response = client.get(record.url.rstrip("/") + "/healthz")
        if response.status_code != 200:
            return False
        value = response.json()
    except Exception:
        return False
    if not isinstance(value, dict) or value.get("status") != "ok":
        return False
    server_instance = str(value.get("instance_id") or DEFAULT_INSTANCE_ID)
    return server_instance == record.id


def _pid_alive(pid: int | None) -> bool:
    if not pid:
        return False
    if os.name == "nt":
        try:
            import ctypes

            process = ctypes.windll.kernel32.OpenProcess(0x1000, False, int(pid))
            if not process:
                return False
            ctypes.windll.kernel32.CloseHandle(process)
            return True
        except Exception:
            return False
    try:
        os.kill(int(pid), 0)
        return True
    except OSError:
        return False


def instance_status(record: InstanceRecord) -> str:
    if _health(record):
        return "running"
    if record.pid and _pid_alive(record.pid):
        return "starting"
    return "stopped" if record.stopped_at else "unhealthy"


def _tail(path: Path, limit: int = 40) -> str:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    return "\n".join(lines[-limit:])


def _ensure_managed_database(config: dict[str, Any]) -> None:
    if not bool(config.get("database_managed")):
        return
    postgres_bin = Path(str(config.get("postgres_bin") or ""))
    data_dir = Path(str(config.get("postgres_data_dir") or ""))
    port = int(config.get("database_port") or 55432)
    executable = postgres_bin / ("pg_ctl.exe" if os.name == "nt" else "pg_ctl")
    if not executable.is_file() or not (data_dir / "PG_VERSION").is_file():
        raise InstanceError("managed PostgreSQL runtime is missing")
    flags = int(getattr(subprocess, "CREATE_NO_WINDOW", 0)) if os.name == "nt" else 0
    status = subprocess.run(
        [str(executable), "status", "-D", str(data_dir)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
        creationflags=flags,
    )
    if status.returncode == 0:
        return
    log_path = lighthouse_home() / "logs" / "postgres.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    start = subprocess.run(
        [
            str(executable),
            "start",
            "-D",
            str(data_dir),
            "-l",
            str(log_path),
            "-o",
            f"-h 127.0.0.1 -p {port}",
            "-w",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        creationflags=flags,
    )
    if start.returncode != 0:
        raise InstanceError(f"failed to start the shared LightHouse PostgreSQL kernel: {(start.stdout or '').strip()}")


def _base_instance_config() -> dict[str, Any]:
    config = _read_json(base_config_path())
    if not config.get("database_url"):
        raise InstanceError("LightHouse is not installed; run the platform installer first")
    return config


def _new_instance_id(name: str | None) -> tuple[str, str]:
    display = str(name or _automatic_name()).strip()
    base = _slug(display)
    candidate = base
    counter = 2
    while _record_path(candidate).exists():
        if name:
            raise InstanceError(f"a LightHouse instance named {display!r} already exists")
        candidate = f"{base}-{counter}"
        counter += 1
    return candidate, display


def _launch(record: InstanceRecord) -> subprocess.Popen[bytes]:
    config = _read_json(Path(record.config_path))
    _ensure_managed_database(config)
    log_dir = Path(record.log_dir)
    log_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    stdout_path = log_dir / "server.log"
    stderr_path = log_dir / "server-error.log"
    environment = os.environ.copy()
    environment.update(
        {
            "LIGHTHOUSE_CONFIG": record.config_path,
            "LIGHTHOUSE_INSTANCE_ID": record.id,
            "PYTHONUTF8": "1",
        }
    )
    app_dir = lighthouse_home() / "app"
    working_directory = app_dir if app_dir.is_dir() else Path.cwd()
    stdout_handle = stdout_path.open("ab", buffering=0)
    stderr_handle = stderr_path.open("ab", buffering=0)
    kwargs: dict[str, Any] = {
        "cwd": str(working_directory),
        "env": environment,
        "stdin": subprocess.DEVNULL,
        "stdout": stdout_handle,
        "stderr": stderr_handle,
        "close_fds": True,
    }
    if os.name == "nt":
        kwargs["creationflags"] = int(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)) | int(
            getattr(subprocess, "DETACHED_PROCESS", 0)
        ) | int(getattr(subprocess, "CREATE_NO_WINDOW", 0))
    else:
        kwargs["start_new_session"] = True
    try:
        process = subprocess.Popen([sys.executable, "-m", "lighthouse.server"], **kwargs)
    finally:
        stdout_handle.close()
        stderr_handle.close()
    return process


def _wait_until_healthy(record: InstanceRecord, process: subprocess.Popen[bytes], timeout: float = 30.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _health(record):
            return
        code = process.poll()
        if code is not None:
            error = _tail(Path(record.log_dir) / "server-error.log") or _tail(Path(record.log_dir) / "server.log")
            raise InstanceError(f"LightHouse instance {record.id} exited with code {code}: {error or 'no server log'}")
        time.sleep(0.25)
    stop_instance(record.id, force=True)
    error = _tail(Path(record.log_dir) / "server-error.log") or _tail(Path(record.log_dir) / "server.log")
    raise InstanceError(f"LightHouse instance {record.id} did not become healthy on port {record.port}: {error or 'no server log'}")


def create_instance(
    name: str | None = None,
    *,
    project_path: str | Path | None = None,
    preferred_port: int | None = None,
) -> InstanceRecord:
    instance_id, display_name = _new_instance_id(name)
    base = _base_instance_config()
    host = "127.0.0.1"
    start = int(preferred_port or base.get("port") or DEFAULT_START_PORT)
    port = find_free_port(start, host)
    directory = _instance_dir(instance_id)
    config_path = directory / "config.json"
    log_dir = directory / "logs"
    project = Path(project_path).expanduser().resolve() if project_path else None
    config = {key: value for key, value in base.items() if key not in _TRANSIENT_CONFIG_KEYS}
    config.update(
        {
            "host": host,
            "port": port,
            "url": f"http://{host}:{port}",
            "instance_id": instance_id,
            "instance_name": display_name,
            "instance_kind": "managed",
            "instance_created_at": _utc_now(),
        }
    )
    if project is not None:
        if not project.is_dir():
            raise InstanceError(f"project path does not exist: {project}")
        config["project_path"] = str(project)
    _write_json(config_path, config)
    record = InstanceRecord(
        id=instance_id,
        name=display_name,
        port=port,
        url=config["url"],
        config_path=str(config_path),
        log_dir=str(log_dir),
        kind="managed",
        platform=sys.platform,
        created_at=str(config["instance_created_at"]),
        project_path=str(project) if project else None,
    )
    record.save()
    process = _launch(record)
    record.pid = process.pid
    record.stopped_at = None
    record.save()
    try:
        _wait_until_healthy(record, process)
    except Exception:
        if process.poll() is None:
            process.terminate()
        raise
    return record


def start_instance(name: str) -> InstanceRecord:
    record = resolve_instance(name)
    if _health(record):
        return record
    if record.id == DEFAULT_INSTANCE_ID:
        raise InstanceError("the default instance is managed by the platform service; rerun the installer or restart that service")
    config_path = Path(record.config_path)
    config = _read_json(config_path)
    preferred = int(config.get("port") or record.port or DEFAULT_START_PORT)
    if not port_is_free(preferred):
        preferred = find_free_port(preferred + 1)
        config["port"] = preferred
        config["url"] = f"http://127.0.0.1:{preferred}"
        _write_json(config_path, config)
        record.port = preferred
        record.url = config["url"]
    process = _launch(record)
    record.pid = process.pid
    record.stopped_at = None
    record.save()
    _wait_until_healthy(record, process)
    return record


def stop_instance(name: str, *, force: bool = False) -> InstanceRecord:
    record = resolve_instance(name)
    if record.id == DEFAULT_INSTANCE_ID:
        if sys.platform == "darwin":
            subprocess.run(
                ["launchctl", "bootout", f"gui/{os.getuid()}/com.cpym.su.lighthouse"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
        elif os.name == "nt":
            subprocess.run(
                ["schtasks.exe", "/End", "/TN", "LightHouse"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                creationflags=int(getattr(subprocess, "CREATE_NO_WINDOW", 0)),
            )
        else:
            raise InstanceError("the default instance service cannot be stopped automatically on this platform")
    elif record.pid and _pid_alive(record.pid):
        if os.name == "nt":
            command = ["taskkill.exe", "/PID", str(record.pid), "/T"]
            if force:
                command.append("/F")
            subprocess.run(
                command,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                creationflags=int(getattr(subprocess, "CREATE_NO_WINDOW", 0)),
            )
        else:
            os.kill(record.pid, signal.SIGKILL if force else signal.SIGTERM)
    record.pid = None
    record.stopped_at = _utc_now()
    record.save()
    return record


def instance_config(name: str, *, start_if_needed: bool = True) -> Path:
    record = resolve_instance(name)
    if start_if_needed and not _health(record):
        record = start_instance(name)
    return Path(record.config_path)
