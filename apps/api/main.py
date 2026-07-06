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
from apps.server.tools import TOOL_REGISTRY, TOOLS_BY_NAME, issue_to_dict, run_pipeline
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
            "results": [
                {
                    "index": r.index,
                    "entity_type": r.entity_type,
                    "success": r.success,
                    "handle": r.handle,
                    "error": r.error,
                    "entity": fixed_plan.operations[r.index].model_dump(),
                }
                for r in result.results
            ],
            "warnings": [issue_to_dict(i) for i in report.warnings],
            "autofixed": [issue_to_dict(i) for i in applied_fixes],
        }

    if DASHBOARD_STATIC_DIR.is_dir():
        app.mount("/dashboard", StaticFiles(directory=DASHBOARD_STATIC_DIR, html=True), name="dashboard")

    return app


app = create_app(build_context(Settings.load()))
