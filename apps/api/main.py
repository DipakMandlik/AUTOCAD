"""REST API entrypoint.

Nothing here re-implements planning, validation, or execution — every
endpoint is a thin wrapper around the exact same `ServerContext`,
`TOOL_REGISTRY`, and `run_pipeline` the MCP server (`apps/server/server.py`)
uses. That is the point: the engine underneath is transport-agnostic, and
this module is proof, not a parallel implementation.

Run with: `uvicorn apps.api.main:app`
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles

from apps.context import ServerContext, build_context
from apps.server.tools import TOOL_REGISTRY, TOOLS_BY_NAME, issue_to_dict, result_entries, run_pipeline
from config import Settings
from engine.geometry.primitives import DrawingPlan
from export.renderer import render_png, render_svg
from export.script import render_lisp, render_scr
from storage.store import ProjectNotFoundError, ProjectStore
from symbols.library import SYMBOL_LIBRARY

DASHBOARD_STATIC_DIR = Path(__file__).resolve().parent.parent / "dashboard" / "static"


def _get_project_or_404(project_store: ProjectStore, project_id: str):
    try:
        return project_store.get(project_id)
    except ProjectNotFoundError:
        raise HTTPException(status_code=404, detail=f"project '{project_id}' not found") from None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _render_response(plan: DrawingPlan, image_format: str) -> Response:
    if image_format == "svg":
        return Response(content=render_svg(plan), media_type="image/svg+xml")
    try:
        return Response(content=render_png(plan), media_type="image/png")
    except RuntimeError as exc:
        raise HTTPException(status_code=501, detail=str(exc)) from exc


def _export_response(plan: DrawingPlan, script_format: str) -> Response:
    if script_format == "scr":
        content, filename = render_scr(plan), "drawing.scr"
    else:
        content, filename = render_lisp(plan), "drawing.lsp"
    headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
    return Response(content=content, media_type="text/plain", headers=headers)


def create_app(ctx: ServerContext) -> FastAPI:
    """App factory: takes an already-built ServerContext so tests can wire
    up an isolated DXF backend (e.g. pointed at a tmp_path) instead of
    sharing whatever backend `python -m apps.api.main` would construct."""
    app = FastAPI(title="CAD MCP Platform API", version="0.2.0")
    app.state.ctx = ctx

    @app.get("/health")
    def health() -> Dict[str, Any]:
        return {
            "status": "ok",
            "backend": ctx.backend.name,
            "backend_running": ctx.backend.is_running(),
        }

    @app.get("/tools")
    def list_tools() -> List[Dict[str, Any]]:
        return [
            {"name": tool.name, "description": tool.description, "input_schema": tool.input_schema}
            for tool in TOOL_REGISTRY
        ]

    @app.post("/tools/{tool_name}")
    def call_tool(tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        tool = TOOLS_BY_NAME.get(tool_name)
        if tool is None:
            raise HTTPException(status_code=404, detail=f"unknown tool '{tool_name}'")
        return tool.handler(arguments, ctx)

    @app.post("/drawings/validate")
    def validate_drawing(plan: DrawingPlan) -> Dict[str, Any]:
        """Dry-run validation: never touches the backend."""
        report = ctx.validator.validate(plan)
        return {"is_valid": report.is_valid, "issues": [issue_to_dict(i) for i in report.issues]}

    @app.post("/drawings/execute")
    def execute_drawing(plan: DrawingPlan) -> Dict[str, Any]:
        """Execute a full multi-entity plan in one call (validate -> autofix
        if needed -> execute), unlike the single-operation MCP tools."""
        fixed_plan, report, applied_fixes, result = run_pipeline(plan, ctx)
        if result is None:
            issues = [issue_to_dict(i) for i in report.issues]
            return {"success": False, "message": "validation failed", "issues": issues}
        return {
            "success": result.success,
            "results": result_entries(fixed_plan, result),
            "warnings": [issue_to_dict(i) for i in report.warnings],
            "autofixed": [issue_to_dict(i) for i in applied_fixes],
        }

    @app.get("/drawings/current")
    def current_drawing() -> Dict[str, Any]:
        return TOOLS_BY_NAME["get_current_drawing"].handler({}, ctx)

    @app.post("/drawings/clear")
    def clear_drawing() -> Dict[str, Any]:
        return TOOLS_BY_NAME["clear_current_drawing"].handler({}, ctx)

    @app.get("/drawings/current/render")
    def render_current_drawing(
        image_format: str = Query("svg", alias="format", pattern="^(svg|png)$"),
    ) -> Response:
        """A real, CAD-accurate render (via ezdxf), unlike the dashboard's
        coarse hand-rolled SVG preview."""
        plan = DrawingPlan(operations=list(ctx.history))
        return _render_response(plan, image_format)

    @app.get("/drawings/current/export")
    def export_current_drawing(
        script_format: str = Query("scr", alias="format", pattern="^(scr|lisp)$"),
    ) -> Response:
        """Download an AutoCAD Script (.scr) or AutoLISP (.lsp) of the
        current drawing. Hatch entities are skipped — see export/script.py."""
        plan = DrawingPlan(operations=list(ctx.history))
        return _export_response(plan, script_format)

    @app.get("/projects")
    def list_projects() -> Dict[str, Any]:
        return TOOLS_BY_NAME["list_projects"].handler({}, ctx)

    @app.post("/projects")
    def create_project(payload: Dict[str, Any]) -> Dict[str, Any]:
        return TOOLS_BY_NAME["create_project"].handler(payload, ctx)

    @app.get("/projects/{project_id}")
    def get_project(project_id: str) -> Dict[str, Any]:
        result = TOOLS_BY_NAME["get_project"].handler({"project_id": project_id}, ctx)
        if not result["success"]:
            raise HTTPException(status_code=404, detail=result["message"])
        return result

    @app.post("/projects/{project_id}/revisions")
    def snapshot_project(project_id: str, payload: Dict[str, Any] = {}) -> Dict[str, Any]:
        # payload is never mutated (only spread into a new dict below), so
        # the shared mutable default is safe here.
        args = {**payload, "project_id": project_id}
        return TOOLS_BY_NAME["snapshot_project"].handler(args, ctx)

    @app.post("/projects/{project_id}/load")
    def load_project(project_id: str) -> Dict[str, Any]:
        return TOOLS_BY_NAME["load_project"].handler({"project_id": project_id}, ctx)

    @app.get("/projects/{project_id}/render")
    def render_project(
        project_id: str, image_format: str = Query("svg", alias="format", pattern="^(svg|png)$")
    ) -> Response:
        project = _get_project_or_404(ctx.project_store, project_id)
        return _render_response(project.plan, image_format)

    @app.get("/projects/{project_id}/export")
    def export_project(
        project_id: str, script_format: str = Query("scr", alias="format", pattern="^(scr|lisp)$")
    ) -> Response:
        project = _get_project_or_404(ctx.project_store, project_id)
        return _export_response(project.plan, script_format)

    @app.get("/symbols")
    def list_symbols() -> Dict[str, Any]:
        return TOOLS_BY_NAME["list_symbols"].handler({}, ctx)

    @app.get("/symbols/{symbol_name}/preview")
    def preview_symbol(
        symbol_name: str,
        image_format: str = Query("svg", alias="format", pattern="^(svg|png)$"),
        scale: float = Query(1.0, gt=0),
        rotation: float = Query(0.0),
    ) -> Response:
        """Render one symbol in isolation — a quick visual sanity check,
        independent of any drawing history. Reuses the same renderer as
        /drawings/current/render; no symbol-specific rendering code."""
        definition = SYMBOL_LIBRARY.get(symbol_name)
        if definition is None:
            raise HTTPException(status_code=404, detail=f"unknown symbol '{symbol_name}'")
        plan = DrawingPlan(operations=definition.build((0.0, 0.0, 0.0), scale, rotation))
        return _render_response(plan, image_format)

    @app.get("/logs")
    def get_logs(limit: int = Query(100, gt=0, le=500)) -> Dict[str, Any]:
        return TOOLS_BY_NAME["get_execution_log"].handler({"limit": limit}, ctx)

    @app.post("/logs/clear")
    def clear_logs() -> Dict[str, Any]:
        return TOOLS_BY_NAME["clear_execution_log"].handler({}, ctx)

    @app.get("/performance")
    def get_performance() -> Dict[str, Any]:
        return TOOLS_BY_NAME["get_performance_stats"].handler({}, ctx)

    @app.get("/settings")
    def get_settings() -> Dict[str, Any]:
        return TOOLS_BY_NAME["get_settings"].handler({}, ctx)

    if DASHBOARD_STATIC_DIR.is_dir():
        app.mount("/dashboard", StaticFiles(directory=DASHBOARD_STATIC_DIR, html=True), name="dashboard")

    return app


app = create_app(build_context(Settings.load()))
