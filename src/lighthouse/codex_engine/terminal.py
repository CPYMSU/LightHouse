from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shlex
import shutil
import sys
import time
from typing import Any
from uuid import uuid4

from .evaluation import EvaluationRunner
from .models import ApprovalDecision, ApprovalPolicy, EnginePolicy, SandboxMode
from .rust_kernel import RustCodeKernelClient
from .session import CodexSessionManager


def _json(value: object) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, default=str))


def doctor(*, codex_binary: str = "codex", rust_binary: str = "lighthouse-code-kernel") -> dict[str, Any]:
    codex = shutil.which(codex_binary) or (codex_binary if Path(codex_binary).is_file() else None)
    rust = shutil.which(rust_binary) or (rust_binary if Path(rust_binary).is_file() else None)
    return {
        "codex": {"available": bool(codex), "path": codex},
        "rust_kernel": {"available": bool(rust), "path": rust},
        "cwd": str(Path.cwd()),
        "engine_modes": ["native", "codex", "hybrid", "shadow", "auto"],
        "slash_commands": [
            "/plan", "/review", "/compact", "/permissions", "/agents",
            "/diff", "/test", "/resume", "/fork", "/status", "/interrupt", "/exit",
        ],
    }


class CodeTerminal:
    def __init__(self, *, binary: str = "codex", model: str | None = None, cwd: str | None = None):
        self.cwd = str(Path(cwd or os.getcwd()).resolve())
        self.manager = CodexSessionManager(binary=binary, model=model)
        self.run_id = f"terminal-{uuid4()}"
        self.policy = EnginePolicy(
            sandbox=SandboxMode.WORKSPACE_WRITE,
            approval=ApprovalPolicy.ON_REQUEST,
            writable_roots=(self.cwd,),
        )
        self.started = False

    def _start_or_steer(self, text: str, *, read_only: bool = False) -> None:
        if not self.started:
            policy = self.policy
            if read_only:
                policy = EnginePolicy(sandbox=SandboxMode.READ_ONLY, approval=ApprovalPolicy.NEVER)
            self.manager.start(run_id=self.run_id, task=text, cwd=self.cwd, policy=policy)
            self.started = True
        else:
            self.manager.steer(self.run_id, text)
        self._stream()

    def _stream(self) -> None:
        while True:
            outcome = self.manager.poll(self.run_id, timeout=0.25)
            if outcome.message:
                print(outcome.message)
            if outcome.approval:
                print("\nAPPROVAL REQUIRED")
                _json(outcome.approval.public_dict())
                decision = input("allow [once/session/no/cancel]? ").strip().lower()
                mapping = {
                    "once": ApprovalDecision.ACCEPT,
                    "session": ApprovalDecision.ACCEPT_FOR_SESSION,
                    "yes": ApprovalDecision.ACCEPT,
                    "y": ApprovalDecision.ACCEPT,
                    "no": ApprovalDecision.DECLINE,
                    "n": ApprovalDecision.DECLINE,
                    "cancel": ApprovalDecision.CANCEL,
                }
                outcome = self.manager.approve(self.run_id, mapping.get(decision, ApprovalDecision.DECLINE))
                if outcome.message:
                    print(outcome.message)
            if outcome.terminal or outcome.approval:
                return
            time.sleep(0.05)

    def command(self, line: str) -> bool:
        command, _, argument = line.partition(" ")
        if command in {"/exit", "/quit"}:
            return False
        if command == "/plan":
            self._start_or_steer(
                "Plan mode. Inspect the repository read-only and provide an implementation plan, risks, files and tests. " + argument,
                read_only=not self.started,
            )
        elif command == "/review":
            if not self.started:
                print("Start a thread first.")
            else:
                self.manager.review(self.run_id)
                self._stream()
        elif command == "/compact":
            _json(self.manager.compact(self.run_id))
        elif command == "/permissions":
            mode = argument.strip() or "workspace-write"
            if mode == "read-only":
                self.policy = EnginePolicy(sandbox=SandboxMode.READ_ONLY, approval=ApprovalPolicy.NEVER)
            elif mode in {"danger", "danger-full-access"}:
                self.policy = EnginePolicy(sandbox=SandboxMode.DANGER_FULL_ACCESS, approval=ApprovalPolicy.ON_REQUEST)
            else:
                self.policy = EnginePolicy(
                    sandbox=SandboxMode.WORKSPACE_WRITE,
                    approval=ApprovalPolicy.ON_REQUEST,
                    writable_roots=(self.cwd,),
                )
            _json(self.policy.thread_params())
        elif command == "/agents":
            if not self.started:
                print("Start a thread first.")
            else:
                status = self.manager.status(self.run_id)
                _json(status)
        elif command == "/diff":
            self._utility(["git", "diff", "--"])
        elif command == "/test":
            self._utility(shlex.split(argument) if argument.strip() else ["pytest", "-q"])
        elif command == "/resume":
            thread_id = argument.strip()
            if not thread_id:
                raise ValueError("/resume requires a thread id")
            self.run_id = f"terminal-{uuid4()}"
            self.manager.start(
                run_id=self.run_id,
                task="Resume this coding thread and wait for my next direction.",
                cwd=self.cwd,
                policy=self.policy,
                thread_id=thread_id,
            )
            self.started = True
            self._stream()
        elif command == "/fork":
            if not self.started:
                print("Start a thread first.")
            else:
                new_run = f"terminal-{uuid4()}"
                binding = self.manager.fork(self.run_id, new_run_id=new_run)
                self.run_id = new_run
                self.started = True
                _json({"thread_id": binding.thread_id, "forked": True})
        elif command == "/status":
            _json(self.manager.status(self.run_id) if self.started else doctor())
        elif command == "/interrupt":
            _json(self.manager.interrupt(self.run_id))
        else:
            print("Unknown slash command. Use /status for current state.")
        return True

    def _utility(self, command: list[str]) -> None:
        if not self.started:
            self._start_or_steer("Inspect the repository briefly so utility commands can run in a thread.", read_only=True)
        session = self.manager.sessions[self.run_id]
        _json(session.client.command_exec(command, cwd=self.cwd, policy=self.policy.thread_params()))

    def repl(self) -> int:
        print("LightHouse Code Engine · Codex app-server v2")
        print("/plan /review /compact /permissions /agents /diff /test /resume /fork /status /interrupt /exit")
        try:
            while True:
                line = input("code> ").strip()
                if not line:
                    continue
                if line.startswith("/"):
                    if not self.command(line):
                        break
                else:
                    self._start_or_steer(line)
        finally:
            if self.started:
                self.manager.close(self.run_id)
        return 0


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="lh code", description="Codex-compatible LightHouse coding engine")
    root.add_argument("--codex-binary", default=os.environ.get("LIGHTHOUSE_CODEX_BINARY", "codex"))
    root.add_argument("--model", default=os.environ.get("LIGHTHOUSE_CODEX_MODEL") or None)
    root.add_argument("--cwd", default=os.getcwd())
    sub = root.add_subparsers(dest="command")
    sub.add_parser("doctor")
    run = sub.add_parser("run")
    run.add_argument("task", nargs="+")
    run.add_argument("--read-only", action="store_true")
    sub.add_parser("interactive")
    evaluate = sub.add_parser("evaluate")
    evaluate.add_argument("fixture")
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.command == "doctor":
        _json(doctor(codex_binary=args.codex_binary))
        return 0
    if args.command == "evaluate":
        cases = EvaluationRunner.load_cases(args.fixture)
        _json({"cases": [case.__dict__ for case in cases], "note": "Attach native/codex adapters through the Python API to execute the matrix."})
        return 0
    terminal = CodeTerminal(binary=args.codex_binary, model=args.model, cwd=args.cwd)
    if args.command == "run":
        task = " ".join(args.task)
        terminal._start_or_steer(task, read_only=args.read_only)
        return 0
    return terminal.repl()


if __name__ == "__main__":
    raise SystemExit(main())
