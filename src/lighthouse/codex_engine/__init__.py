"""Codex-compatible coding engine for LightHouse.

The package speaks the public Codex app-server v2 JSONL protocol while keeping
LightHouse Memory, Run, Operation, Receipt and evaluation contracts authoritative.
Heavy runtime integrations are imported lazily so protocol/evaluation tooling can
also be used without bootstrapping the complete LightHouse service.
"""

from .app_server import CodexAppServerClient, CodexAppServerError
from .evaluation import EvaluationCase, EvaluationReport, EvaluationRunner, PromotionGate
from .models import (
    ApprovalDecision,
    ApprovalPolicy,
    CodeEngineMode,
    EnginePolicy,
    SandboxMode,
    TurnOutcome,
    normalize_engine_mode,
)
from .rust_kernel import RustCodeKernelClient, RustKernelError


def __getattr__(name: str):
    if name == "CodexEngineMixin":
        from .mixin import CodexEngineMixin
        return CodexEngineMixin
    if name == "CodexSessionManager":
        from .session import CodexSessionManager
        return CodexSessionManager
    raise AttributeError(name)


__all__ = [
    "ApprovalDecision",
    "ApprovalPolicy",
    "CodeEngineMode",
    "CodexAppServerClient",
    "CodexAppServerError",
    "CodexEngineMixin",
    "CodexSessionManager",
    "EnginePolicy",
    "EvaluationCase",
    "EvaluationReport",
    "EvaluationRunner",
    "PromotionGate",
    "RustCodeKernelClient",
    "RustKernelError",
    "SandboxMode",
    "TurnOutcome",
    "normalize_engine_mode",
]
