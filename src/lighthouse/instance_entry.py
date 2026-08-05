from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

from . import terminal as base_terminal
from . import terminal_entry
from .config import Settings
from .instances import (
    InstanceError,
    create_instance,
    instance_config,
    list_instances,
    start_instance,
    stop_instance,
)
from .warehouse_federation import (
    WarehouseFederationError,
    disable_warehouse_federation,
    pair_warehouse_device,
    warehouse_federation_status,
)


def _instance_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="lh new",
        description="Start another isolated LightHouse API instance",
    )
    parser.add_argument("name", nargs="?")
    parser.add_argument("--project")
    parser.add_argument("--port", type=int)
    parser.add_argument("--no-attach", action="store_true")
    return parser


def _warehouse_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="lh warehouse",
        description="Pair and inspect Warehouse OS 2.1 federation",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    pair = subparsers.add_parser("pair", help="pair this LightHouse instance")
    pair.add_argument("origin", help="Warehouse HTTPS origin")
    pair.add_argument("pairing_code", help="one-time Warehouse pairing code")
    pair.add_argument("--label", default="LightHouse")
    pair.add_argument("--workspace", dest="workspace_id")
    subparsers.add_parser("status", help="show local federation configuration")
    subparsers.add_parser("disconnect", help="remove the local device credential")
    return parser


def _activate(config_path: Path) -> None:
    os.environ["LIGHTHOUSE_CONFIG"] = str(config_path)


def _print_json(value: object) -> int:
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, default=str))
    return 0


def _print_instances() -> int:
    return _print_json({"items": [record.public() for record in list_instances()]})


def _new(argv: list[str]) -> int:
    args = _instance_parser().parse_args(argv)
    record = create_instance(
        args.name,
        project_path=args.project,
        preferred_port=args.port,
    )
    _print_json(
        {
            "instance": record.public(),
            "message": "LightHouse instance started",
        }
    )
    if args.no_attach:
        return 0
    _activate(Path(record.config_path))
    if args.project:
        os.chdir(Path(args.project).expanduser().resolve())
    return terminal_entry.main([])


def _warehouse(argv: list[str]) -> int:
    args = _warehouse_parser().parse_args(argv)
    if args.command == "status":
        return _print_json(warehouse_federation_status())
    if args.command == "disconnect":
        return _print_json(disable_warehouse_federation())
    settings = Settings.from_env()
    result = pair_warehouse_device(
        origin=args.origin,
        pairing_code=args.pairing_code,
        instance_id=settings.instance_id,
        label=args.label,
        workspace_id=args.workspace_id,
    )
    result["connection"] = (
        "The running LightHouse service detects the new credential automatically."
    )
    return _print_json(result)


def _attach(name: str, remaining: list[str]) -> int:
    config = instance_config(name, start_if_needed=True)
    _activate(config)
    return terminal_entry.main(remaining)


def _help() -> int:
    base_terminal.help_text()
    print(
        """
Instance Kernel:
  lh new [NAME] [--project PATH] [--port PORT]   start and attach to another instance
  lh instances                                  list all local instances
  lh attach NAME                                attach to an existing instance
  lh stop NAME                                  stop an instance
  lh start NAME                                 restart a stopped instance
  lh --instance NAME [COMMAND]                  run against a named instance

Warehouse Federation:
  lh warehouse pair ORIGIN CODE [--workspace ID] [--label NAME]
  lh warehouse status
  lh warehouse disconnect
"""
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
            return terminal_entry.main([])
        first = argv[0]
        if first in {"help", "--help", "-h"}:
            return _help()
        if first == "new":
            return _new(argv[1:])
        if first == "warehouse":
            return _warehouse(argv[1:])
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
            return _print_json({"instance": record.public()})
        if first in {"start", "instance-start"}:
            if len(argv) != 2:
                raise InstanceError(f"{first} requires exactly one instance name")
            record = start_instance(argv[1])
            return _print_json({"instance": record.public()})
        return terminal_entry.main(argv)
    except (InstanceError, WarehouseFederationError) as exc:
        base_terminal.SwissTerminal().error(str(exc))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
