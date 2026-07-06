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

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles

from apps.context import ServerContext, build_context
from apps.server.tools import TOOL_REGISTRY, TOOLS_BY_NAME, issue_to_dict, result_entries, run_pipeline
from config import Settings
from engine.geometry.primitives import DrawingPlan

DASHBOARD_STATIC_DIR = Path(__file__).resolve().parent.parent / "dashboard" / "static"


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

    if DASHBOARD_STATIC_DIR.is_dir():
        app.mount("/dashboard", StaticFiles(directory=DASHBOARD_STATIC_DIR, html=True), name="dashboard")

    return app


app = create_app(build_context(Settings.load()))
