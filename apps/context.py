"""Shared wiring: builds the one `ServerContext` (planner + validator +
backend) that both the MCP server and the REST API dispatch tool calls
through. Keeping this in one place is what proves the engine underneath
is transport-agnostic — neither app re-implements planning or execution,
they just wire the same context into a different protocol.
"""

from __future__ import annotations

from dataclasses import dataclass

from cad.backend import CADBackend
from cad.registry import get_backend
from config import Settings
from engine.planner.planner import Planner
from engine.validator.engine import ValidationEngine
from nlp.fallback import FallbackParser


@dataclass
class ServerContext:
    planner: Planner
    validator: ValidationEngine
    backend: CADBackend
    color_parser: FallbackParser


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
    )
