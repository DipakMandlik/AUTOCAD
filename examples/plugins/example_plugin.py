"""Example plugin: a template for building your own.

Copy this file into your configured plugins directory
(`config.PluginSettings.directory`, default `./plugins_installed`) and the
platform picks it up automatically at startup — no core file needs to
change. A plugin is just a .py file defining a module-level `PLUGIN`
object of type `plugins.base.Plugin`.

This one demonstrates both extension points:

- a new tool, `draw_regular_polygon`, built entirely out of the existing
  `draw_polyline` operation via `ctx.planner`/`execute_plan` — a plugin
  does not need a new Entity type in `engine/geometry/primitives.py` to
  add a genuinely new drawing capability; composing existing primitives
  is usually enough and is much less invasive.
- a new validation rule, `_rule_no_default_layer`, enforcing an
  organization-specific convention (don't draw on the unnamed default
  layer "0") that has no place in the platform's built-in rule set
  because it is a policy choice, not a geometric correctness check.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List

from apps.context import ServerContext
from apps.server.tools import ToolSpec, execute_plan
from engine.geometry.engine import GeometryEngine
from engine.geometry.primitives import DrawingPlan
from engine.validator.issue import Issue
from plugins.base import Plugin


def _handle_draw_regular_polygon(arguments: Dict[str, Any], ctx: ServerContext) -> Dict[str, Any]:
    center = arguments["center"]
    radius = arguments["radius"]
    sides = arguments["sides"]
    if sides < 3:
        return {"success": False, "message": "a polygon needs at least 3 sides"}

    points = [
        (
            center[0] + radius * math.cos(2 * math.pi * i / sides),
            center[1] + radius * math.sin(2 * math.pi * i / sides),
        )
        for i in range(sides)
    ]

    params: Dict[str, Any] = {"points": points, "closed": True}
    if arguments.get("layer") is not None:
        params["layer"] = arguments["layer"]
    if arguments.get("color") is not None:
        # A real plugin drawing tool would typically also resolve color
        # names through ctx.color_parser.extract_color(), the same as the
        # built-in draw_* tools — omitted here to keep the example short.
        params["color"] = arguments["color"]

    plan = ctx.planner.plan_from_operation("draw_polyline", params)
    return execute_plan(plan, ctx)


def _rule_no_default_layer(plan: DrawingPlan, geometry: GeometryEngine) -> List[Issue]:
    return [
        Issue(
            severity="warning",
            code="default_layer_used",
            message=f"entity at index {i} is on layer '0'; assign a named layer",
            indices=[i],
        )
        for i, entity in enumerate(plan.operations)
        if entity.layer == "0"
    ]


PLUGIN = Plugin(
    name="example-plugin",
    version="0.1.0",
    tools=[
        ToolSpec(
            "draw_regular_polygon",
            "Draw a regular N-sided polygon centered at a point (example plugin tool)",
            {
                "type": "object",
                "properties": {
                    "center": {
                        "type": "array",
                        "description": "center point [x, y, (z)]",
                        "items": {"type": "number"},
                        "minItems": 2,
                        "maxItems": 3,
                    },
                    "radius": {"type": "number", "description": "distance from center to each vertex"},
                    "sides": {"type": "integer", "minimum": 3, "description": "number of sides"},
                    "layer": {"type": "string", "description": "layer name (optional)"},
                    "color": {"description": "color index 1-255, or a color name (optional)"},
                },
                "required": ["center", "radius", "sides"],
            },
            _handle_draw_regular_polygon,
        ),
    ],
    validation_rules=[_rule_no_default_layer],
)
