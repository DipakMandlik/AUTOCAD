"""Planner: turns an Intent (or already-typed tool args) into a DrawingPlan.

Both the natural-language path (`plan_from_text`) and the direct MCP
tool-call path (`plan_from_operation`) funnel through `_build_entity`, so
there is exactly one place that constructs and validates entities — this is
what removes the duplicated argument-handling the original repo had between
`process_command` and `handle_call_tool`.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from pydantic import ValidationError

from engine.geometry.primitives import ENTITY_TYPES, DrawingPlan, Entity
from engine.planner.intent import FallbackIntentSource, IntentSource

OPERATION_TO_ENTITY_TYPE = {
    "draw_line": "line",
    "draw_circle": "circle",
    "draw_arc": "arc",
    "draw_ellipse": "ellipse",
    "draw_rectangle": "rectangle",
    "draw_polyline": "polyline",
    "draw_text": "text",
    "draw_hatch": "hatch",
    "add_dimension": "dimension",
}

NON_GEOMETRY_OPERATIONS = {"save", "create_layer"}


class PlanningError(Exception):
    """Raised when an intent cannot be turned into a valid DrawingPlan."""


class NonGeometryIntent(Exception):
    """Raised when the parsed intent is a backend action (save/create_layer)
    rather than something that belongs in a DrawingPlan. The MCP server's
    `process_command` handler catches this and dispatches to the matching
    tool directly instead of trying to build geometry from it.
    """

    def __init__(self, operation: str, params: Dict[str, Any]):
        super().__init__(f"'{operation}' is not a drawable operation")
        self.operation = operation
        self.params = params


class Planner:
    def __init__(
        self,
        intent_source: Optional[IntentSource] = None,
        default_layer: str = "0",
        default_color: Optional[int] = None,
    ) -> None:
        self.intent_source = intent_source or FallbackIntentSource()
        self.default_layer = default_layer
        self.default_color = default_color

    def plan_from_text(self, text: str, name: str = "untitled") -> DrawingPlan:
        intent = self.intent_source.detect(text)
        if intent.operation in ("unknown", "error"):
            raise PlanningError(intent.note or f"could not plan a drawing operation from: {text!r}")
        if intent.operation in NON_GEOMETRY_OPERATIONS:
            raise NonGeometryIntent(intent.operation, intent.params)
        entity = self._build_entity(intent.operation, intent.params)
        return DrawingPlan(name=name, source_text=text, operations=[entity])

    def plan_from_operation(
        self, operation_type: str, params: Dict[str, Any], name: str = "untitled"
    ) -> DrawingPlan:
        entity = self._build_entity(operation_type, params)
        return DrawingPlan(name=name, operations=[entity])

    def _build_entity(self, operation_type: str, params: Dict[str, Any]) -> Entity:
        entity_type = OPERATION_TO_ENTITY_TYPE.get(operation_type, operation_type)
        model_cls = ENTITY_TYPES.get(entity_type)
        if model_cls is None:
            raise PlanningError(f"unknown drawing operation '{operation_type}'")

        payload = {k: v for k, v in params.items() if v is not None}
        payload.setdefault("layer", self.default_layer)
        if self.default_color is not None:
            payload.setdefault("color", self.default_color)

        try:
            return model_cls(**payload)
        except ValidationError as exc:
            raise PlanningError(f"invalid parameters for '{operation_type}': {exc}") from exc
