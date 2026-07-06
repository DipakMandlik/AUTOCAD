import pytest

from apps.server.tools import TOOL_REGISTRY, TOOLS_BY_NAME, ServerContext, run_pipeline
from cad.registry import get_backend
from engine.geometry.primitives import DrawingPlan
from engine.planner.planner import Planner
from engine.validator.engine import ValidationEngine
from nlp.fallback import FallbackParser
from storage.store import ProjectStore


@pytest.fixture
def ctx(tmp_path):
    return ServerContext(
        planner=Planner(),
        validator=ValidationEngine(),
        backend=get_backend("dxf", output_dir=str(tmp_path / "output")),
        color_parser=FallbackParser(),
        project_store=ProjectStore(str(tmp_path / "projects")),
    )


def test_registry_has_expected_tools():
    names = {t.name for t in TOOL_REGISTRY}
    assert names == {
        "draw_line", "draw_circle", "draw_arc", "draw_ellipse", "draw_polyline",
        "draw_rectangle", "draw_text", "draw_hatch", "add_dimension",
        "save_drawing", "create_layer", "process_command",
        "get_current_drawing", "clear_current_drawing", "render_current_drawing",
        "create_project", "list_projects", "get_project", "snapshot_project", "load_project",
    }


def test_every_tool_schema_has_required_object_shape():
    for tool in TOOL_REGISTRY:
        assert tool.input_schema["type"] == "object"
        assert "properties" in tool.input_schema


def test_draw_line_tool_success(ctx):
    result = TOOLS_BY_NAME["draw_line"].handler({"start": [0, 0], "end": [10, 10]}, ctx)
    assert result["success"]
    assert result["handle"]
    # entity/model_dump() returns raw Python values (tuples), not yet
    # JSON-serialized — the wire format (MCP json.dumps / FastAPI) turns
    # these into arrays for the client.
    assert result["entity"]["type"] == "line"
    assert result["entity"]["start"] == (0.0, 0.0, 0.0)


def test_process_command_result_includes_parsed_entity(ctx):
    # a client that only sent natural language has no other way to know
    # what geometry the NLP parser actually resolved it to.
    result = TOOLS_BY_NAME["process_command"].handler({"command": "draw a circle at (5,5) radius 20"}, ctx)
    assert result["entity"]["type"] == "circle"
    assert result["entity"]["center"] == (5.0, 5.0, 0.0)
    assert result["entity"]["radius"] == 20.0


def test_draw_line_tool_resolves_color_name(ctx):
    result = TOOLS_BY_NAME["draw_line"].handler({"start": [0, 0], "end": [10, 10], "color": "red"}, ctx)
    assert result["success"]


def test_invalid_geometry_returns_failure_dict_not_exception(ctx):
    result = TOOLS_BY_NAME["draw_circle"].handler({"center": [0, 0], "radius": -5}, ctx)
    assert result["success"] is False
    assert "message" in result


def test_create_layer_tool(ctx):
    result = TOOLS_BY_NAME["create_layer"].handler({"layer_name": "walls"}, ctx)
    assert result["success"]


def test_save_tool_rejects_path_traversal(ctx):
    result = TOOLS_BY_NAME["save_drawing"].handler({"file_path": "../../etc/passwd"}, ctx)
    assert result["success"] is False


def test_process_command_draws_geometry(ctx):
    result = TOOLS_BY_NAME["process_command"].handler({"command": "draw a circle at (5,5) radius 20"}, ctx)
    assert result["success"]


def test_process_command_dispatches_save(ctx, tmp_path):
    result = TOOLS_BY_NAME["process_command"].handler({"command": 'save the drawing to "test.dxf"'}, ctx)
    assert result["success"]
    assert (tmp_path / "output" / "test.dxf").exists()


def test_process_command_unknown_returns_failure(ctx):
    result = TOOLS_BY_NAME["process_command"].handler({"command": "do something incomprehensible"}, ctx)
    assert result["success"] is False


def test_zero_length_line_is_autofixed_and_reported(ctx):
    result = TOOLS_BY_NAME["draw_line"].handler({"start": [0, 0], "end": [0, 0]}, ctx)
    # the only operation in the plan gets dropped by autofix, leaving an
    # empty plan — nothing to draw, but this must not look like a crash.
    assert result["success"] is True
    assert any(f["code"] == "zero_length_line" for f in result["autofixed"])


def test_run_pipeline_autofixes_warning_only_issues(ctx):
    # regression test: duplicate_entity is a *warning*, not an error, so a
    # plan with nothing but a duplicate must still trigger autofix. Gating
    # autofix on "report.is_valid" (errors only) would silently skip it.
    entity = {"type": "circle", "center": [0, 0], "radius": 5}
    plan = DrawingPlan(operations=[entity, dict(entity)])
    _plan, report, applied, result = run_pipeline(plan, ctx)
    assert any(i.code == "duplicate_entity" for i in applied)
    assert result is not None
    assert len(result.results) == 1


def test_history_accumulates_only_successfully_drawn_entities(ctx):
    assert ctx.history == []
    TOOLS_BY_NAME["draw_circle"].handler({"center": [0, 0], "radius": 5}, ctx)
    # invalid radius never reaches the backend, so it must not join history
    TOOLS_BY_NAME["draw_circle"].handler({"center": [0, 0], "radius": -5}, ctx)
    assert len(ctx.history) == 1
    assert ctx.history[0].type == "circle"


def test_get_and_clear_current_drawing(ctx):
    TOOLS_BY_NAME["draw_line"].handler({"start": [0, 0], "end": [10, 10]}, ctx)
    result = TOOLS_BY_NAME["get_current_drawing"].handler({}, ctx)
    assert len(result["operations"]) == 1

    cleared = TOOLS_BY_NAME["clear_current_drawing"].handler({}, ctx)
    assert cleared["success"] is True
    assert ctx.history == []


def test_render_current_drawing_returns_svg(ctx):
    TOOLS_BY_NAME["draw_circle"].handler({"center": [0, 0], "radius": 5}, ctx)
    result = TOOLS_BY_NAME["render_current_drawing"].handler({}, ctx)
    assert result["success"] is True
    assert result["format"] == "svg"
    assert "<svg" in result["svg"]


def test_render_current_drawing_handles_empty_history(ctx):
    result = TOOLS_BY_NAME["render_current_drawing"].handler({}, ctx)
    assert result["success"] is True
    assert "<svg" in result["svg"]


def test_create_project_snapshots_current_history(ctx):
    TOOLS_BY_NAME["draw_circle"].handler({"center": [0, 0], "radius": 5}, ctx)
    result = TOOLS_BY_NAME["create_project"].handler({"name": "demo"}, ctx)
    assert result["success"] is True
    project_id = result["project_id"]

    fetched = TOOLS_BY_NAME["get_project"].handler({"project_id": project_id}, ctx)
    assert fetched["project"]["name"] == "demo"
    assert len(fetched["project"]["plan"]["operations"]) == 1


def test_create_project_requires_name(ctx):
    result = TOOLS_BY_NAME["create_project"].handler({}, ctx)
    assert result["success"] is False


def test_snapshot_project_adds_revision(ctx):
    TOOLS_BY_NAME["draw_circle"].handler({"center": [0, 0], "radius": 5}, ctx)
    created = TOOLS_BY_NAME["create_project"].handler({"name": "demo"}, ctx)
    project_id = created["project_id"]

    TOOLS_BY_NAME["draw_line"].handler({"start": [0, 0], "end": [10, 10]}, ctx)
    args = {"project_id": project_id, "note": "added a line"}
    snapshot = TOOLS_BY_NAME["snapshot_project"].handler(args, ctx)
    assert snapshot["success"] is True
    assert snapshot["revision"] == 2

    fetched = TOOLS_BY_NAME["get_project"].handler({"project_id": project_id}, ctx)
    assert len(fetched["project"]["revisions"]) == 2


def test_snapshot_unknown_project_fails_cleanly(ctx):
    result = TOOLS_BY_NAME["snapshot_project"].handler({"project_id": "nope"}, ctx)
    assert result["success"] is False


def test_list_projects(ctx):
    TOOLS_BY_NAME["create_project"].handler({"name": "a"}, ctx)
    TOOLS_BY_NAME["create_project"].handler({"name": "b"}, ctx)
    result = TOOLS_BY_NAME["list_projects"].handler({}, ctx)
    assert {p["name"] for p in result["projects"]} == {"a", "b"}


def test_load_project_redraws_saved_plan(ctx):
    TOOLS_BY_NAME["draw_circle"].handler({"center": [0, 0], "radius": 5}, ctx)
    created = TOOLS_BY_NAME["create_project"].handler({"name": "demo"}, ctx)
    project_id = created["project_id"]

    TOOLS_BY_NAME["clear_current_drawing"].handler({}, ctx)
    assert ctx.history == []

    loaded = TOOLS_BY_NAME["load_project"].handler({"project_id": project_id}, ctx)
    assert loaded["success"] is True
    assert len(loaded["results"]) == 1
    assert loaded["results"][0]["entity"]["type"] == "circle"
    assert len(ctx.history) == 1


def test_load_unknown_project_fails_cleanly(ctx):
    result = TOOLS_BY_NAME["load_project"].handler({"project_id": "nope"}, ctx)
    assert result["success"] is False


def test_get_project_rejects_unsafe_id(ctx):
    result = TOOLS_BY_NAME["get_project"].handler({"project_id": "../../etc/passwd"}, ctx)
    assert result["success"] is False
