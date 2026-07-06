"""Shared wiring: builds the one `ServerContext` (planner + validator +
backend) that both the MCP server and the REST API dispatch tool calls
through. Keeping this in one place is what proves the engine underneath
is transport-agnostic — neither app re-implements planning or execution,
they just wire the same context into a different protocol.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List

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


def build_context(settings: Settings) -> ServerContext:
    backend = get_backend(
        settings.cad.backend,
        output_dir=settings.output.directory,
        startup_wait_time=settings.cad.startup_wait_time,
    )
    return ServerContext(
        planner=Planner(),
        validator=ValidationEngine(),
        backend=backend,
        color_parser=FallbackParser(),
        project_store=ProjectStore(settings.storage.directory),
    )
