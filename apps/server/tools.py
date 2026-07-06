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
from export.renderer import render_svg
from export.script import render_lisp, render_scr, unsupported_entities
from imports.svg_import import SvgImportError, import_svg
from nlp.fallback import FallbackParser
from storage.store import ProjectNotFoundError
from symbols.library import SYMBOL_LIBRARY

__all__ = [
    "ServerContext",
    "TOOL_REGISTRY",
    "TOOLS_BY_NAME",
    "ToolSpec",
    "execute_plan",
    "run_pipeline",
    "issue_to_dict",
    "result_entries",
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
    succeeded = {r.index for r in result.results if r.success}
    ctx.history.extend(op for i, op in enumerate(plan.operations) if i in succeeded)
    return plan, report, applied_fixes, result


def execute_plan(plan: DrawingPlan, ctx: ServerContext) -> Dict[str, Any]:
    """MCP-facing wrapper around run_pipeline for single-operation plans."""
    fixed_plan, report, applied_fixes, result = run_pipeline(plan, ctx)
    if result is None:
        return {
            "success": False,
            "message": "validation failed",
            "issues": [issue_to_dict(i) for i in report.issues],
        }

    entity_result = result.results[0] if result.results else None
    # Echo back the (possibly autofixed) entity's own data, not just its
    # handle — a client that only sent natural language to process_command
    # has no other way to know what geometry actually got drawn.
    entity = fixed_plan.operations[0].model_dump() if fixed_plan.operations else None
    return {
        "success": result.success,
        "message": "drawn" if result.success else "failed to draw entity",
        "handle": entity_result.handle if entity_result else None,
        "error": entity_result.error if entity_result and not entity_result.success else None,
        "entity": entity,
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


def _handle_get_current_drawing(arguments: Dict[str, Any], ctx: ServerContext) -> Dict[str, Any]:
    return {"success": True, "operations": [entity.model_dump() for entity in ctx.history]}


def _handle_clear_current_drawing(arguments: Dict[str, Any], ctx: ServerContext) -> Dict[str, Any]:
    count = len(ctx.history)
    ctx.history.clear()
    return {"success": True, "message": f"cleared {count} entit{'y' if count == 1 else 'ies'} from history"}


def _handle_render_current_drawing(arguments: Dict[str, Any], ctx: ServerContext) -> Dict[str, Any]:
    plan = DrawingPlan(operations=list(ctx.history))
    try:
        svg = render_svg(plan)
    except Exception as exc:  # noqa: BLE001 - rendering can fail in many ezdxf/Pillow-specific ways
        return {"success": False, "message": f"render failed: {exc}"}
    return {"success": True, "format": "svg", "svg": svg}


def _export_current_drawing(ctx: ServerContext, renderer, format_name: str) -> Dict[str, Any]:
    plan = DrawingPlan(operations=list(ctx.history))
    skipped = unsupported_entities(plan)
    result: Dict[str, Any] = {"success": True, "format": format_name, "script": renderer(plan)}
    if skipped:
        count = len(skipped)
        result["warning"] = (
            f"{count} entit{'y' if count == 1 else 'ies'} skipped "
            f"(hatch cannot be reliably scripted): indices {skipped}"
        )
    return result


def _handle_export_script(arguments: Dict[str, Any], ctx: ServerContext) -> Dict[str, Any]:
    return _export_current_drawing(ctx, render_scr, "scr")


def _handle_export_lisp(arguments: Dict[str, Any], ctx: ServerContext) -> Dict[str, Any]:
    return _export_current_drawing(ctx, render_lisp, "lsp")


def _handle_create_project(arguments: Dict[str, Any], ctx: ServerContext) -> Dict[str, Any]:
    name = arguments.get("name")
    if not name:
        return {"success": False, "message": "name is required"}
    plan = DrawingPlan(name=name, operations=list(ctx.history))
    project = ctx.project_store.create(name, plan)
    return {"success": True, "message": f"project '{name}' created", "project_id": project.id, "revision": 1}


def _handle_list_projects(arguments: Dict[str, Any], ctx: ServerContext) -> Dict[str, Any]:
    projects = ctx.project_store.list()
    return {
        "success": True,
        "projects": [
            {
                "id": p.id,
                "name": p.name,
                "created_at": p.created_at,
                "updated_at": p.updated_at,
                "revisions": len(p.revisions),
            }
            for p in projects
        ],
    }


def _handle_get_project(arguments: Dict[str, Any], ctx: ServerContext) -> Dict[str, Any]:
    project_id = arguments.get("project_id")
    if not project_id:
        return {"success": False, "message": "project_id is required"}
    try:
        project = ctx.project_store.get(project_id)
    except ProjectNotFoundError:
        return {"success": False, "message": f"project '{project_id}' not found"}
    except ValueError as exc:
        return {"success": False, "message": str(exc)}
    return {"success": True, "project": project.model_dump()}


def _handle_snapshot_project(arguments: Dict[str, Any], ctx: ServerContext) -> Dict[str, Any]:
    project_id = arguments.get("project_id")
    if not project_id:
        return {"success": False, "message": "project_id is required"}
    plan = DrawingPlan(operations=list(ctx.history))
    try:
        project = ctx.project_store.add_revision(project_id, plan, arguments.get("note"))
    except ProjectNotFoundError:
        return {"success": False, "message": f"project '{project_id}' not found"}
    except ValueError as exc:
        return {"success": False, "message": str(exc)}
    revision = len(project.revisions)
    return {"success": True, "message": f"revision {revision} saved", "revision": revision}


def result_entries(fixed_plan: DrawingPlan, result: ExecutionResult) -> List[Dict[str, Any]]:
    """Per-entity execution results, each including the entity's own data —
    shared by load_project below and the REST API's /drawings/execute."""
    return [
        {
            "index": r.index,
            "entity_type": r.entity_type,
            "success": r.success,
            "handle": r.handle,
            "error": r.error,
            "entity": fixed_plan.operations[r.index].model_dump(),
        }
        for r in result.results
    ]


def _handle_load_project(arguments: Dict[str, Any], ctx: ServerContext) -> Dict[str, Any]:
    project_id = arguments.get("project_id")
    if not project_id:
        return {"success": False, "message": "project_id is required"}
    try:
        project = ctx.project_store.get(project_id)
    except ProjectNotFoundError:
        return {"success": False, "message": f"project '{project_id}' not found"}
    except ValueError as exc:
        return {"success": False, "message": str(exc)}

    fixed_plan, report, applied_fixes, result = run_pipeline(project.plan, ctx)
    if result is None:
        issues = [issue_to_dict(i) for i in report.issues]
        return {"success": False, "message": "validation failed", "issues": issues}
    return {
        "success": result.success,
        "message": f"loaded project '{project.name}' ({len(result.results)} entities)",
        "results": result_entries(fixed_plan, result),
        "warnings": [issue_to_dict(i) for i in report.warnings],
        "autofixed": [issue_to_dict(i) for i in applied_fixes],
    }


def _handle_list_symbols(arguments: Dict[str, Any], ctx: ServerContext) -> Dict[str, Any]:
    return {
        "success": True,
        "symbols": [
            {"name": d.name, "discipline": d.discipline, "description": d.description}
            for d in SYMBOL_LIBRARY.values()
        ],
    }


def _handle_insert_symbol(arguments: Dict[str, Any], ctx: ServerContext) -> Dict[str, Any]:
    symbol_name = arguments.get("symbol_name")
    definition = SYMBOL_LIBRARY.get(symbol_name)
    if definition is None:
        known = sorted(SYMBOL_LIBRARY)
        return {"success": False, "message": f"unknown symbol {symbol_name!r}; known symbols: {known}"}

    position = arguments.get("position", (0.0, 0.0, 0.0))
    scale = arguments.get("scale", 1.0)
    rotation = arguments.get("rotation", 0.0)
    layer = arguments.get("layer")
    color = _resolve_color(arguments.get("color"), ctx.color_parser)

    entities = definition.build(position, scale, rotation)
    if layer is not None:
        entities = [e.model_copy(update={"layer": layer}) for e in entities]
    if color is not None:
        entities = [e.model_copy(update={"color": color}) for e in entities]

    # A symbol is usually more than one entity (e.g. a capacitor's two
    # plates and two leads), so this goes through run_pipeline/
    # result_entries directly rather than the single-entity execute_plan.
    plan = DrawingPlan(name=symbol_name, operations=entities)
    fixed_plan, report, applied_fixes, result = run_pipeline(plan, ctx)
    if result is None:
        issues = [issue_to_dict(i) for i in report.issues]
        return {"success": False, "message": "validation failed", "issues": issues}
    return {
        "success": result.success,
        "message": f"inserted symbol '{symbol_name}' ({len(result.results)} entities)",
        "results": result_entries(fixed_plan, result),
        "warnings": [issue_to_dict(i) for i in report.warnings],
        "autofixed": [issue_to_dict(i) for i in applied_fixes],
    }


def _handle_import_svg(arguments: Dict[str, Any], ctx: ServerContext) -> Dict[str, Any]:
    svg_content = arguments.get("svg_content")
    if not svg_content:
        return {"success": False, "message": "svg_content is required"}

    try:
        entities, import_warnings = import_svg(svg_content)
    except SvgImportError as exc:
        return {"success": False, "message": str(exc)}

    layer = arguments.get("layer")
    color = _resolve_color(arguments.get("color"), ctx.color_parser)
    if layer is not None:
        entities = [e.model_copy(update={"layer": layer}) for e in entities]
    if color is not None:
        entities = [e.model_copy(update={"color": color}) for e in entities]

    plan = DrawingPlan(name="svg_import", operations=entities)
    fixed_plan, report, applied_fixes, result = run_pipeline(plan, ctx)
    if result is None:
        issues = [issue_to_dict(i) for i in report.issues]
        return {"success": False, "message": "validation failed", "issues": issues}
    message = f"imported {len(result.results)} entities from SVG"
    if import_warnings:
        message += f" ({len(import_warnings)} element(s) skipped, see import_warnings)"
    return {
        "success": result.success,
        "message": message,
        "results": result_entries(fixed_plan, result),
        "warnings": [issue_to_dict(i) for i in report.warnings],
        "autofixed": [issue_to_dict(i) for i in applied_fixes],
        "import_warnings": import_warnings,
    }


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
    ToolSpec(
        "get_current_drawing",
        "List every entity successfully drawn so far this session",
        {"type": "object", "properties": {}},
        _handle_get_current_drawing,
    ),
    ToolSpec(
        "clear_current_drawing",
        "Clear the session's drawing history (does not undo anything already sent to the backend)",
        {"type": "object", "properties": {}},
        _handle_clear_current_drawing,
    ),
    ToolSpec(
        "render_current_drawing",
        "Render the current drawing history to a real, CAD-accurate SVG image",
        {"type": "object", "properties": {}},
        _handle_render_current_drawing,
    ),
    ToolSpec(
        "export_script",
        "Export the current drawing history as an AutoCAD Script (.scr); hatch entities are skipped",
        {"type": "object", "properties": {}},
        _handle_export_script,
    ),
    ToolSpec(
        "export_lisp",
        "Export the current drawing history as AutoLISP (.lsp) commands; hatch entities are skipped",
        {"type": "object", "properties": {}},
        _handle_export_lisp,
    ),
    ToolSpec(
        "create_project",
        "Save the current drawing history as a new named project",
        {
            "type": "object",
            "properties": {"name": {"type": "string", "description": "project name"}},
            "required": ["name"],
        },
        _handle_create_project,
    ),
    ToolSpec(
        "list_projects",
        "List all saved projects",
        {"type": "object", "properties": {}},
        _handle_list_projects,
    ),
    ToolSpec(
        "get_project",
        "Get a saved project's full plan and revision history",
        {
            "type": "object",
            "properties": {"project_id": {"type": "string", "description": "project id"}},
            "required": ["project_id"],
        },
        _handle_get_project,
    ),
    ToolSpec(
        "snapshot_project",
        "Save the current drawing history as a new revision of an existing project",
        {
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "project id"},
                "note": {"type": "string", "description": "revision note (optional)"},
            },
            "required": ["project_id"],
        },
        _handle_snapshot_project,
    ),
    ToolSpec(
        "load_project",
        "Re-draw a saved project's plan against the current backend",
        {
            "type": "object",
            "properties": {"project_id": {"type": "string", "description": "project id"}},
            "required": ["project_id"],
        },
        _handle_load_project,
    ),
    ToolSpec(
        "list_symbols",
        "List the available engineering symbols (electrical, piping, architectural)",
        {"type": "object", "properties": {}},
        _handle_list_symbols,
    ),
    ToolSpec(
        "insert_symbol",
        "Insert a symbol from the library at a position, with optional scale/rotation/layer/color",
        {
            "type": "object",
            "properties": {
                "symbol_name": {"type": "string", "description": "see list_symbols"},
                "position": _point_schema("insertion point [x, y, (z)]"),
                "scale": {"type": "number", "description": "uniform scale factor (optional, default 1.0)"},
                "rotation": {"type": "number", "description": "rotation angle in degrees (optional)"},
                **_common_props(),
            },
            "required": ["symbol_name", "position"],
        },
        _handle_insert_symbol,
    ),
    ToolSpec(
        "import_svg",
        "Import a constrained subset of SVG (line/circle/ellipse/rect/polyline/polygon/text/"
        "straight-segment path) as drawing entities",
        {
            "type": "object",
            "properties": {
                "svg_content": {"type": "string", "description": "raw SVG document text"},
                "layer": {
                    "type": "string",
                    "description": "override layer for all imported entities (optional)",
                },
                "color": _common_props()["color"],
            },
            "required": ["svg_content"],
        },
        _handle_import_svg,
    ),
]

TOOLS_BY_NAME: Dict[str, ToolSpec] = {tool.name: tool for tool in TOOL_REGISTRY}
