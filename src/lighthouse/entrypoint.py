from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

from . import terminal
from .instances import (
    InstanceError,
    create_instance,
    instance_config,
    list_instances,
    start_instance,
    stop_instance,
)


def _instance_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="lh new", description="Start another isolated LightHouse API instance")
    parser.add_argument("name", nargs="?")
    parser.add_argument("--project")
    parser.add_argument("--port", type=int)
    parser.add_argument("--no-attach", action="store_true")
    return parser


def _activate(config_path: Path) -> None:
    os.environ["LIGHTHOUSE_CONFIG"] = str(config_path)


def _print_instances() -> int:
    print(json.dumps({"items": [record.public() for record in list_instances()]}, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def _new(argv: list[str]) -> int:
    args = _instance_parser().parse_args(argv)
    record = create_instance(args.name, project_path=args.project, preferred_port=args.port)
    print(
        json.dumps(
            {
                "instance": record.public(),
                "message": "LightHouse instance started",
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    if args.no_attach:
        return 0
    _activate(Path(record.config_path))
    if args.project:
        os.chdir(Path(args.project).expanduser().resolve())
    return terminal.interactive()


def _attach(name: str, remaining: list[str]) -> int:
    config = instance_config(name, start_if_needed=True)
    _activate(config)
    if remaining:
        return terminal.main(remaining)
    return terminal.interactive()


def _help() -> int:
    terminal.help_text()
    print(
        """Instance Kernel:\n"
        "  lh new [NAME] [--project PATH] [--port PORT]   start and attach to another instance\n"
        "  lh instances                                  list all local instances\n"
        "  lh attach NAME                                attach to an existing instance\n"
        "  lh stop NAME                                  stop an instance\n"
        "  lh --instance NAME [COMMAND]                  run against a named instance\n"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        if argv[:1] == ["--instance"]:
            if len(argv) < 2:
                raise InstanceError("--instance requires an instance name")
            return _attach(argv[1], argv[2:])
        if not argv:
            return terminal.main([])
        first = argv[0]
        if first in {"help", "--help", "-h"}:
            return _help()
        if first == "new":
            return _new(argv[1:])
        if first in {"instances", "instance-list"}:
            return _print_instances()
        if first in {"attach", "instance-use"}:
            if len(argv) < 2:
                raise InstanceError(f"{first} requires an instance name")
            return _attach(argv[1], argv[2:])
        if first in {"stop", "instance-stop"}:
            if len(argv) != 2:
                raise InstanceError(f"{first} requires exactly one instance name")
            record = stop_instance(argv[1])
            print(json.dumps({"instance": record.public()}, ensure_ascii=False, indent=2, sort_keys=True))
            return 0
        if first in {"start", "instance-start"}:
            if len(argv) != 2:
                raise InstanceError(f"{first} requires exactly one instance name")
            record = start_instance(argv[1])
            print(json.dumps({"instance": record.public()}, ensure_ascii=False, indent=2, sort_keys=True))
            return 0
        return terminal.main(argv)
    except InstanceError as exc:
        terminal.SwissTerminal().error(str(exc))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
