from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
from typing import Any
from urllib.parse import urlencode, urlsplit

import httpx


class CLIError(RuntimeError):
    pass


def config_path(args) -> Path:
    return Path(
        args.config
        or os.environ.get("LIGHTHOUSE_CONFIG")
        or Path.home() / ".lighthouse" / "config.json"
    ).expanduser()


def load_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    if path.is_symlink() or not path.is_file():
        raise CLIError("configuration path must be a regular file")
    if os.name != "nt" and stat.S_IMODE(path.stat().st_mode) not in {0o400, 0o600}:
        raise CLIError("configuration file permissions must be 0600 or 0400")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise CLIError("configuration root must be an object")
    return value


def save_config(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, name = tempfile.mkstemp(prefix=".config.", dir=path.parent)
    try:
        if os.name != "nt":
            os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            descriptor = -1
            json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(name, path)
        if os.name != "nt":
            os.chmod(path, 0o600)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            os.unlink(name)
        except FileNotFoundError:
            pass


def parse_json_object(value: str, label: str) -> dict[str, Any]:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise CLIError(f"{label} is not valid JSON") from exc
    if not isinstance(parsed, dict):
        raise CLIError(f"{label} must be a JSON object")
    return parsed


class Client:
    def __init__(self, base_url: str, api_key: str, timeout: int = 600):
        self.base_url = base_url.rstrip("/")
        parsed = urlsplit(self.base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise CLIError("LIGHTHOUSE_URL is invalid")
        if parsed.scheme == "http" and parsed.hostname not in {
            "localhost",
            "127.0.0.1",
            "::1",
        }:
            raise CLIError("operator credentials require HTTPS except on loopback")
        if len(api_key) < 16:
            raise CLIError("LIGHTHOUSE_API_KEY is missing or too short")
        self.client = httpx.Client(
            base_url=self.base_url,
            headers={"Authorization": "Bearer " + api_key},
            timeout=timeout,
            follow_redirects=False,
        )

    def request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
    ) -> Any:
        response = self.client.request(method, path, json=payload)
        try:
            value = response.json()
        except ValueError:
            value = {"detail": response.text}
        if response.is_error:
            raise CLIError(
                str(value.get("detail") if isinstance(value, dict) else value)
            )
        return value


def print_json(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="lh",
        description="LightHouse OS governed AI super terminal",
    )
    parser.add_argument("--url")
    parser.add_argument("--config")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status")
    sub.add_parser("migrate")

    caps = sub.add_parser("capabilities")
    caps.add_argument("query", nargs="?", default="")
    caps.add_argument("--kernel", choices=("auto", "data", "system"), default="auto")

    sub.add_parser("targets")
    target_add = sub.add_parser("target-add")
    target_add.add_argument("name")
    target_add.add_argument("--kind", choices=("data", "system"), required=True)
    target_add.add_argument("--config-json", required=True)

    sub.add_parser("workspaces")
    workspace_add = sub.add_parser("workspace-add")
    workspace_add.add_argument("name")
    workspace_add.add_argument("--data-target")
    workspace_add.add_argument("--system-target")

    configure = sub.add_parser("configure")
    configure.add_argument("--workspace")
    configure.add_argument("--mode", choices=("auto", "data", "system"))
    configure.add_argument("--actor")

    use = sub.add_parser("use")
    use.add_argument("mode", choices=("auto", "data", "system"))
    use.add_argument("workspace")
    use.add_argument("--actor")

    mode = sub.add_parser("mode")
    mode.add_argument("mode", choices=("auto", "data", "system"))

    run = sub.add_parser("run")
    run.add_argument("capability")
    run.add_argument("--args-json", default="{}")
    run.add_argument("--workspace")
    run.add_argument("--mode", choices=("auto", "data", "system"))
    run.add_argument("--actor")
    run.add_argument("--idempotency-key")
    run.add_argument("--confirm", action="store_true")

    confirm = sub.add_parser("confirm")
    confirm.add_argument("operation_id")
    confirm.add_argument("--actor")

    for name in ("operation", "events", "receipt"):
        item = sub.add_parser(name)
        item.add_argument("operation_id")

    agent = sub.add_parser("agent")
    agent.add_argument("task", nargs="+")
    agent.add_argument("--workspace")
    agent.add_argument("--mode", choices=("auto", "data", "system"))
    agent.add_argument("--actor")
    agent.add_argument("--max-steps", type=int, default=12)
    agent.add_argument(
        "--yes",
        action="store_true",
        help="auto-confirm explicit (never Passkey) operations for this run",
    )

    agent_show = sub.add_parser("agent-show")
    agent_show.add_argument("run_id")

    agent_resume = sub.add_parser("agent-resume")
    agent_resume.add_argument("run_id")

    agent_input = sub.add_parser("agent-input")
    agent_input.add_argument("run_id")
    agent_input.add_argument("message", nargs="+")
    agent_input.add_argument("--actor")

    agent_events = sub.add_parser("agent-events")
    agent_events.add_argument("run_id")
    return parser


def context(args, config: dict[str, Any]) -> tuple[str, str, str]:
    workspace = getattr(args, "workspace", None) or config.get("workspace")
    mode = getattr(args, "mode", None) or config.get("mode") or "auto"
    actor = (
        getattr(args, "actor", None)
        or config.get("actor")
        or os.environ.get("LIGHTHOUSE_ACTOR")
        or "operator"
    )
    if not workspace:
        raise CLIError("no workspace selected; run lh use MODE WORKSPACE")
    return str(workspace), str(mode), str(actor)


def selected_actor(args, config: dict[str, Any]) -> str:
    return str(
        getattr(args, "actor", None)
        or config.get("actor")
        or os.environ.get("LIGHTHOUSE_ACTOR")
        or "operator"
    )


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    path = config_path(args)
    config = load_config(path)
    try:
        if args.command == "status":
            print_json(
                {
                    "url": args.url
                    or os.environ.get("LIGHTHOUSE_URL")
                    or config.get("url")
                    or "http://127.0.0.1:8787",
                    "workspace": config.get("workspace"),
                    "mode": config.get("mode", "auto"),
                    "actor": config.get("actor", "operator"),
                    "api_key_stored": False,
                }
            )
            return 0

        if args.command in {"configure", "use", "mode"}:
            if args.command == "use":
                config.update({"workspace": args.workspace, "mode": args.mode})
                if args.actor:
                    config["actor"] = args.actor
            elif args.command == "mode":
                config["mode"] = args.mode
            else:
                for key in ("workspace", "mode", "actor"):
                    value = getattr(args, key)
                    if value:
                        config[key] = value
            if args.url:
                config["url"] = args.url
            save_config(path, config)
            print_json(config)
            return 0

        base_url = (
            args.url
            or os.environ.get("LIGHTHOUSE_URL")
            or config.get("url")
            or "http://127.0.0.1:8787"
        )
        client = Client(base_url, os.environ.get("LIGHTHOUSE_API_KEY", ""))

        if args.command == "migrate":
            print_json(client.request("POST", "/v1/admin/migrate", {}))
        elif args.command == "capabilities":
            query = urlencode({"q": args.query, "kernel": args.kernel})
            print_json(client.request("GET", "/v1/capabilities?" + query))
        elif args.command == "targets":
            print_json(client.request("GET", "/v1/targets"))
        elif args.command == "target-add":
            print_json(
                client.request(
                    "POST",
                    "/v1/targets",
                    {
                        "name": args.name,
                        "kind": args.kind,
                        "config": parse_json_object(
                            args.config_json,
                            "--config-json",
                        ),
                    },
                )
            )
        elif args.command == "workspaces":
            print_json(client.request("GET", "/v1/workspaces"))
        elif args.command == "workspace-add":
            print_json(
                client.request(
                    "POST",
                    "/v1/workspaces",
                    {
                        "name": args.name,
                        "data_target_id": args.data_target,
                        "system_target_id": args.system_target,
                    },
                )
            )
        elif args.command == "run":
            workspace, mode, actor = context(args, config)
            value = client.request(
                "POST",
                "/v1/operations",
                {
                    "capability": args.capability,
                    "arguments": parse_json_object(args.args_json, "--args-json"),
                    "workspace_id": workspace,
                    "actor": actor,
                    "mode": mode,
                    "idempotency_key": args.idempotency_key,
                },
            )
            if (
                args.confirm
                and value["operation"]["status"] == "awaiting_confirmation"
            ):
                value = client.request(
                    "POST",
                    f"/v1/operations/{value['operation']['id']}/confirm",
                    {"actor": actor},
                )
            print_json(value)
        elif args.command == "confirm":
            print_json(
                client.request(
                    "POST",
                    f"/v1/operations/{args.operation_id}/confirm",
                    {"actor": selected_actor(args, config)},
                )
            )
        elif args.command == "operation":
            print_json(client.request("GET", f"/v1/operations/{args.operation_id}"))
        elif args.command == "events":
            print_json(
                client.request(
                    "GET",
                    f"/v1/operations/{args.operation_id}/events",
                )
            )
        elif args.command == "receipt":
            print_json(
                client.request(
                    "GET",
                    f"/v1/operations/{args.operation_id}/receipt",
                )
            )
        elif args.command == "agent":
            workspace, mode, actor = context(args, config)
            print_json(
                client.request(
                    "POST",
                    "/v1/agent/runs",
                    {
                        "task": " ".join(args.task),
                        "workspace_id": workspace,
                        "actor": actor,
                        "mode": mode,
                        "max_steps": args.max_steps,
                        "auto_confirm": args.yes,
                    },
                )
            )
        elif args.command == "agent-show":
            print_json(client.request("GET", f"/v1/agent/runs/{args.run_id}"))
        elif args.command == "agent-resume":
            print_json(
                client.request(
                    "POST",
                    f"/v1/agent/runs/{args.run_id}/advance",
                    {},
                )
            )
        elif args.command == "agent-input":
            print_json(
                client.request(
                    "POST",
                    f"/v1/agent/runs/{args.run_id}/input",
                    {
                        "actor": selected_actor(args, config),
                        "message": " ".join(args.message),
                    },
                )
            )
        elif args.command == "agent-events":
            print_json(client.request("GET", f"/v1/agent/runs/{args.run_id}"))
        return 0
    except (CLIError, OSError, ValueError) as exc:
        print(f"lh: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
