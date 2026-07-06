"""Shared wiring: builds the one `ServerContext` (planner + validator +
backend) that both the MCP server and the REST API dispatch tool calls
through. Keeping this in one place is what proves the engine underneath
is transport-agnostic — neither app re-implements planning or execution,
they just wire the same context into a different protocol.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List

from apps.execution_log import ExecutionLog
from cad.backend import CADBackend
from cad.registry import get_backend
from config import Settings
from engine.geometry.primitives import Entity
from engine.planner.planner import Planner
from engine.validator.engine import ValidationEngine
from nlp.fallback import FallbackParser
from storage.store import ProjectStore


@dataclass
class ServerContext:
    planner: Planner
    validator: ValidationEngine
    backend: CADBackend
    color_parser: FallbackParser
    project_store: ProjectStore
    # Every entity successfully drawn this session, across all tool calls —
    # the "current drawing," and what a saved Project snapshots. Not a
    # substitute for a real multi-document/multi-tenant model: there is
    # still exactly one backend document for the process's lifetime.
    history: List[Entity] = field(default_factory=list)
    # Every tool call, successful or not — the dashboard's "Logs" section
    # reads from this. Bounded and process-lifetime only; see
    # apps/execution_log.py.
    execution_log: ExecutionLog = field(default_factory=ExecutionLog)


def build_context(settings: Settings) -> ServerContext:
    _load_plugins(settings)
    backend = get_backend(
        settings.cad.backend,
        output_dir=settings.output.directory,
        startup_wait_time=settings.cad.startup_wait_time,
    )
    return ServerContext(
        planner=Planner(),
        # Built after plugins are loaded: ValidationEngine() snapshots
        # DEFAULT_RULES at construction time, so a plugin-contributed rule
        # added afterwards would silently never run.
        validator=ValidationEngine(),
        backend=backend,
        color_parser=FallbackParser(),
        project_store=ProjectStore(settings.storage.directory),
    )


def _load_plugins(settings: Settings) -> None:
    """Discover and apply plugins into the real shared registries.

    Imports are deferred to call time rather than module level: apps.server
    .tools imports ServerContext from this module, so this module cannot
    import apps.server.tools at *module load* time without a circular
    import. By the time build_context() actually runs, both modules have
    already finished initializing, so the import below is safe.
    """
    from apps.server.tools import TOOL_REGISTRY, TOOLS_BY_NAME
    from cad.registry import register_backend
    from engine.validator.rules import DEFAULT_RULES
    from plugins.loader import discover_and_apply

    discover_and_apply(
        settings.plugins.directory,
        TOOL_REGISTRY,
        TOOLS_BY_NAME,
        DEFAULT_RULES,
        register_backend,
    )
