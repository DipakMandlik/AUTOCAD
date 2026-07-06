"""Single source of truth for every MCP tool: name, JSON schema, and how it
turns into a DrawingPlan.

This is the module that eliminates the original repo's duplication between
`handle_call_tool` and `process_command` (each reimplemented the same 11
operations with near-identical argument handling). Every tool is defined
once here; both the direct MCP tool-call path and the natural-language
`process_command` path route through `execute_plan`, which always runs the
same plan -> validate -> (autofix) -> execute pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

from apps.context import ServerContext
from cad.backend import ExecutionResult
from engine.geometry.primitives import DrawingPlan
from engine.planner.planner import NonGeometryIntent, PlanningError
from engine.validator.engine import ValidationReport
from engine.validator.issue import Issue
from nlp.fallback import FallbackParser

__all__ = [
    "ServerContext",
    "TOOL_REGISTRY",
    "TOOLS_BY_NAME",
    "ToolSpec",
    "execute_plan",
    "run_pipeline",
    "issue_to_dict",
]


def _point_schema(description: str) -> Dict[str, Any]:
    return {
        "type": "array",
        "description": description,
        "items": {"type": "number"},
        "minItems": 2,
        "maxItems": 3,
    }


def _common_props() -> Dict[str, Any]:
    return {
        "layer": {"type": "string", "description": "layer name (optional)"},
        "color": {"description": "color index 1-255, or a color name such as 'red' (optional)"},
        "lineweight": {
            "type": "number",
            "description": "lineweight in 1/100mm; must be a valid AutoCAD lineweight (optional)",
        },
    }


def issue_to_dict(issue: Issue) -> Dict[str, Any]:
    return {
        "severity": issue.severity,
        "code": issue.code,
        "message": issue.message,
        "indices": issue.indices,
    }


def _resolve_color(value: Any, color_parser: FallbackParser) -> Optional[int]:
    if value is None or isinstance(value, int):
        return value
    return color_parser.extract_color(str(value))


def run_pipeline(
    plan: DrawingPlan, ctx: ServerContext
) -> Tuple[DrawingPlan, ValidationReport, List[Issue], Optional[ExecutionResult]]:
    """The one pipeline every drawing operation goes through: validate,
    autofix if needed, re-validate, then execute against the backend.

    Returns the (possibly autofixed) plan, the final validation report, the
    fixes that were applied, and the execution result — or `None` for the
    result if the plan is still invalid after autofixing. Shared by the MCP
    tool dispatch below and the REST API's batch execute endpoint, so both
    transports run identical validate/autofix/execute semantics.
    """
    report = ctx.validator.validate(plan)
    applied_fixes: List[Issue] = []
    # Gate on "any autofixable issue", not "any error": duplicate entities
    # and similar autofixable problems are warning-severity, so gating on
    # is_valid alone would silently skip fixing them whenever no separate
    # error was also present.
    if any(issue.autofixable for issue in report.issues):
        plan, applied_fixes = ctx.validator.autofix(plan)
        report = ctx.validator.validate(plan)

    if not report.is_valid:
        return plan, report, applied_fixes, None

    result = ctx.backend.execute(plan)
    return plan, report, applied_fixes, result


def execute_plan(plan: DrawingPlan, ctx: ServerContext) -> Dict[str, Any]:
    """MCP-facing wrapper around run_pipeline for single-operation plans."""
    _plan, report, applied_fixes, result = run_pipeline(plan, ctx)
    if result is None:
        return {
            "success": False,
            "message": "validation failed",
            "issues": [issue_to_dict(i) for i in report.issues],
        }

    entity_result = result.results[0] if result.results else None
    return {
        "success": result.success,
        "message": "drawn" if result.success else "failed to draw entity",
        "handle": entity_result.handle if entity_result else None,
        "error": entity_result.error if entity_result and not entity_result.success else None,
        "warnings": [issue_to_dict(i) for i in report.warnings],
        "autofixed": [issue_to_dict(i) for i in applied_fixes],
    }


def _handle_geometry_tool(operation: str):
    def handler(arguments: Dict[str, Any], ctx: ServerContext) -> Dict[str, Any]:
        params = dict(arguments)
        params["color"] = _resolve_color(params.get("color"), ctx.color_parser)
        try:
            plan = ctx.planner.plan_from_operation(operation, params)
        except PlanningError as exc:
            return {"success": False, "message": str(exc)}
        return execute_plan(plan, ctx)

    return handler


def _handle_save(arguments: Dict[str, Any], ctx: ServerContext) -> Dict[str, Any]:
    file_path = arguments.get("file_path")
    if not file_path:
        return {"success": False, "message": "file_path is required"}
    if not ctx.backend.is_running():
        ctx.backend.start()
    try:
        success = ctx.backend.save(file_path)
    except ValueError as exc:  # unsafe path
        return {"success": False, "message": str(exc)}
    return {"success": success, "message": f"saved to {file_path}" if success else "save failed"}


def _handle_create_layer(arguments: Dict[str, Any], ctx: ServerContext) -> Dict[str, Any]:
    layer_name = arguments.get("layer_name")
    if not layer_name:
        return {"success": False, "message": "layer_name is required"}
    if not ctx.backend.is_running():
        ctx.backend.start()
    success = ctx.backend.create_layer(layer_name)
    message = f"layer '{layer_name}' ready" if success else "failed to create layer"
    return {"success": success, "message": message}


def _handle_process_command(arguments: Dict[str, Any], ctx: ServerContext) -> Dict[str, Any]:
    command = arguments.get("command")
    if not command:
        return {"success": False, "message": "command is required"}
    try:
        plan = ctx.planner.plan_from_text(command)
    except NonGeometryIntent as intent:
        dispatch = {"save": _handle_save, "create_layer": _handle_create_layer}[intent.operation]
        return dispatch(intent.params, ctx)
    except PlanningError as exc:
        return {"success": False, "message": str(exc)}
    return execute_plan(plan, ctx)


@dataclass
class ToolSpec:
    name: str
    description: str
    input_schema: Dict[str, Any]
    handler: Callable[[Dict[str, Any], ServerContext], Dict[str, Any]]


TOOL_REGISTRY: List[ToolSpec] = [
    ToolSpec(
        "draw_line",
        "Draw a line in the CAD drawing",
        {
            "type": "object",
            "properties": {
                "start": _point_schema("start point [x, y, (z)]"),
                "end": _point_schema("end point [x, y, (z)]"),
                **_common_props(),
            },
            "required": ["start", "end"],
        },
        _handle_geometry_tool("draw_line"),
    ),
    ToolSpec(
        "draw_circle",
        "Draw a circle in the CAD drawing",
        {
            "type": "object",
            "properties": {
                "center": _point_schema("center point [x, y, (z)]"),
                "radius": {"type": "number", "description": "circle radius"},
                **_common_props(),
            },
            "required": ["center", "radius"],
        },
        _handle_geometry_tool("draw_circle"),
    ),
    ToolSpec(
        "draw_arc",
        "Draw an arc in the CAD drawing",
        {
            "type": "object",
            "properties": {
                "center": _point_schema("center point [x, y, (z)]"),
                "radius": {"type": "number", "description": "arc radius"},
                "start_angle": {"type": "number", "description": "start angle in degrees"},
                "end_angle": {"type": "number", "description": "end angle in degrees"},
                **_common_props(),
            },
            "required": ["center", "radius", "start_angle", "end_angle"],
        },
        _handle_geometry_tool("draw_arc"),
    ),
    ToolSpec(
        "draw_ellipse",
        "Draw an ellipse in the CAD drawing",
        {
            "type": "object",
            "properties": {
                "center": _point_schema("center point [x, y, (z)]"),
                "major_axis": {"type": "number", "description": "major axis length"},
                "minor_axis": {"type": "number", "description": "minor axis length"},
                "rotation": {"type": "number", "description": "rotation angle in degrees (optional)"},
                **_common_props(),
            },
            "required": ["center", "major_axis", "minor_axis"],
        },
        _handle_geometry_tool("draw_ellipse"),
    ),
    ToolSpec(
        "draw_polyline",
        "Draw a polyline in the CAD drawing",
        {
            "type": "object",
            "properties": {
                "points": {
                    "type": "array",
                    "description": "list of [x, y, (z)] points",
                    "items": {"type": "array", "items": {"type": "number"}, "minItems": 2, "maxItems": 3},
                    "minItems": 2,
                },
                "closed": {"type": "boolean", "description": "whether to close the polyline (optional)"},
                **_common_props(),
            },
            "required": ["points"],
        },
        _handle_geometry_tool("draw_polyline"),
    ),
    ToolSpec(
        "draw_rectangle",
        "Draw a rectangle in the CAD drawing",
        {
            "type": "object",
            "properties": {
                "corner1": _point_schema("first corner [x, y, (z)]"),
                "corner2": _point_schema("opposite corner [x, y, (z)]"),
                **_common_props(),
            },
            "required": ["corner1", "corner2"],
        },
        _handle_geometry_tool("draw_rectangle"),
    ),
    ToolSpec(
        "draw_text",
        "Add text to the CAD drawing",
        {
            "type": "object",
            "properties": {
                "position": _point_schema("insertion point [x, y, (z)]"),
                "text": {"type": "string", "description": "text content"},
                "height": {"type": "number", "description": "text height (optional)"},
                "rotation": {"type": "number", "description": "rotation angle in degrees (optional)"},
                "layer": _common_props()["layer"],
                "color": _common_props()["color"],
            },
            "required": ["position", "text"],
        },
        _handle_geometry_tool("draw_text"),
    ),
    ToolSpec(
        "draw_hatch",
        "Draw a hatch fill in the CAD drawing",
        {
            "type": "object",
            "properties": {
                "points": {
                    "type": "array",
                    "description": "boundary points [[x, y, (z)], ...], at least 3",
                    "items": {"type": "array", "items": {"type": "number"}, "minItems": 2, "maxItems": 3},
                    "minItems": 3,
                },
                "pattern_name": {
                    "type": "string",
                    "description": "hatch pattern name (optional, default SOLID)",
                },
                "scale": {"type": "number", "description": "hatch pattern scale (optional)"},
                "layer": _common_props()["layer"],
                "color": _common_props()["color"],
            },
            "required": ["points"],
        },
        _handle_geometry_tool("draw_hatch"),
    ),
    ToolSpec(
        "add_dimension",
        "Add an aligned linear dimension to the CAD drawing",
        {
            "type": "object",
            "properties": {
                "start": _point_schema("start point [x, y, (z)]"),
                "end": _point_schema("end point [x, y, (z)]"),
                "text_position": _point_schema("dimension text position (optional)"),
                "textheight": {"type": "number", "description": "dimension text height (optional)"},
                "layer": _common_props()["layer"],
                "color": _common_props()["color"],
            },
            "required": ["start", "end"],
        },
        _handle_geometry_tool("add_dimension"),
    ),
    ToolSpec(
        "save_drawing",
        "Save the current drawing to a file",
        {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "output path, relative to the configured output directory",
                },
            },
            "required": ["file_path"],
        },
        _handle_save,
    ),
    ToolSpec(
        "create_layer",
        "Create (or activate) a layer in the CAD drawing",
        {
            "type": "object",
            "properties": {
                "layer_name": {"type": "string", "description": "layer name"},
            },
            "required": ["layer_name"],
        },
        _handle_create_layer,
    ),
    ToolSpec(
        "process_command",
        "Parse a natural-language drawing command and execute it",
        {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "natural-language drawing command"},
            },
            "required": ["command"],
        },
        _handle_process_command,
    ),
]

TOOLS_BY_NAME: Dict[str, ToolSpec] = {tool.name: tool for tool in TOOL_REGISTRY}
