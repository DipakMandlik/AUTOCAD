import pytest

from apps.server.tools import TOOL_REGISTRY, TOOLS_BY_NAME, ServerContext, run_pipeline
from cad.registry import get_backend
from engine.geometry.primitives import DrawingPlan
from engine.planner.planner import Planner
from engine.validator.engine import ValidationEngine
from nlp.fallback import FallbackParser


@pytest.fixture
def ctx(tmp_path):
    return ServerContext(
        planner=Planner(),
        validator=ValidationEngine(),
        backend=get_backend("dxf", output_dir=str(tmp_path)),
        color_parser=FallbackParser(),
    )


def test_registry_has_expected_tools():
    names = {t.name for t in TOOL_REGISTRY}
    assert names == {
        "draw_line", "draw_circle", "draw_arc", "draw_ellipse", "draw_polyline",
        "draw_rectangle", "draw_text", "draw_hatch", "add_dimension",
        "save_drawing", "create_layer", "process_command",
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
    assert (tmp_path / "test.dxf").exists()


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
