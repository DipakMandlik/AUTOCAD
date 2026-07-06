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
        "export_script", "export_lisp",
        "create_project", "list_projects", "get_project", "snapshot_project", "load_project",
        "list_symbols", "insert_symbol", "import_svg",
        "get_execution_log", "clear_execution_log", "get_performance_stats", "get_settings",
        "list_templates", "insert_title_block",
        "enqueue_operation", "get_queue", "remove_queue_item", "run_queue", "clear_queue",
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


def test_export_script_tool(ctx):
    TOOLS_BY_NAME["draw_line"].handler({"start": [0, 0], "end": [10, 10]}, ctx)
    result = TOOLS_BY_NAME["export_script"].handler({}, ctx)
    assert result["success"] is True
    assert result["format"] == "scr"
    assert "LINE" in result["script"]
    assert "warning" not in result


def test_export_lisp_tool(ctx):
    TOOLS_BY_NAME["draw_line"].handler({"start": [0, 0], "end": [10, 10]}, ctx)
    result = TOOLS_BY_NAME["export_lisp"].handler({}, ctx)
    assert result["success"] is True
    assert result["format"] == "lsp"
    assert '(command "LINE"' in result["script"]


def test_export_script_warns_on_skipped_hatch(ctx):
    TOOLS_BY_NAME["draw_hatch"].handler({"points": [[0, 0], [10, 0], [10, 10]]}, ctx)
    result = TOOLS_BY_NAME["export_script"].handler({}, ctx)
    assert result["success"] is True
    assert "warning" in result
    assert "hatch" in result["warning"]


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


def test_list_symbols_tool(ctx):
    result = TOOLS_BY_NAME["list_symbols"].handler({}, ctx)
    assert result["success"] is True
    names = {s["name"] for s in result["symbols"]}
    assert "gate_valve" in names
    assert all("discipline" in s and "description" in s for s in result["symbols"])


def test_insert_symbol_tool_draws_multiple_entities(ctx):
    result = TOOLS_BY_NAME["insert_symbol"].handler(
        {"symbol_name": "capacitor", "position": [5, 5]}, ctx
    )
    assert result["success"] is True
    assert len(result["results"]) == 4  # capacitor is 4 entities
    assert len(ctx.history) == 4


def test_insert_symbol_applies_layer_and_color(ctx):
    result = TOOLS_BY_NAME["insert_symbol"].handler(
        {"symbol_name": "resistor", "position": [0, 0], "layer": "schematic", "color": "red"}, ctx
    )
    assert result["success"] is True
    entity = result["results"][0]["entity"]
    assert entity["layer"] == "schematic"
    assert entity["color"] == 1


def test_insert_symbol_unknown_name_fails_cleanly(ctx):
    result = TOOLS_BY_NAME["insert_symbol"].handler({"symbol_name": "not_real", "position": [0, 0]}, ctx)
    assert result["success"] is False
    assert "not_real" in result["message"]


def test_insert_symbol_scale_and_rotation_are_applied(ctx):
    result = TOOLS_BY_NAME["insert_symbol"].handler(
        {"symbol_name": "pump", "position": [0, 0], "scale": 3.0}, ctx
    )
    circle = next(e["entity"] for e in result["results"] if e["entity_type"] == "circle")
    assert circle["radius"] == pytest.approx(1.5)  # local radius 0.5 * scale 3


def test_import_svg_tool_draws_entities(ctx):
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10">'
        '<line x1="0" y1="0" x2="10" y2="10"/><circle cx="5" cy="5" r="2"/>'
        "</svg>"
    )
    result = TOOLS_BY_NAME["import_svg"].handler({"svg_content": svg}, ctx)
    assert result["success"] is True
    assert len(result["results"]) == 2
    assert len(ctx.history) == 2
    assert result["import_warnings"] == []


def test_import_svg_tool_reports_skipped_elements(ctx):
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10">'
        '<circle cx="5" cy="5" r="2"/><path d="M0,0 C1,1 2,2 3,3"/>'
        "</svg>"
    )
    result = TOOLS_BY_NAME["import_svg"].handler({"svg_content": svg}, ctx)
    assert result["success"] is True
    assert len(result["results"]) == 1
    assert len(result["import_warnings"]) == 1
    assert "skipped" in result["message"]


def test_import_svg_tool_applies_layer_and_color(ctx):
    svg = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><circle cx="5" cy="5" r="2"/></svg>'
    result = TOOLS_BY_NAME["import_svg"].handler(
        {"svg_content": svg, "layer": "imported", "color": "red"}, ctx
    )
    entity = result["results"][0]["entity"]
    assert entity["layer"] == "imported"
    assert entity["color"] == 1


def test_import_svg_tool_missing_content_fails_cleanly(ctx):
    result = TOOLS_BY_NAME["import_svg"].handler({}, ctx)
    assert result["success"] is False


def test_import_svg_tool_invalid_svg_fails_cleanly(ctx):
    result = TOOLS_BY_NAME["import_svg"].handler({"svg_content": "<svg><line x1=0/></svg>"}, ctx)
    assert result["success"] is False
    assert "invalid SVG" in result["message"]


def test_every_tool_call_is_recorded_in_execution_log(ctx):
    TOOLS_BY_NAME["draw_circle"].handler({"center": [0, 0], "radius": 5}, ctx)
    entries = ctx.execution_log.recent()
    assert len(entries) == 1
    assert entries[0].tool == "draw_circle"
    assert entries[0].success is True
    assert entries[0].message == "drawn"
    assert entries[0].duration_ms >= 0


def test_execution_log_records_failures_too(ctx):
    TOOLS_BY_NAME["draw_circle"].handler({"center": [0, 0], "radius": -5}, ctx)
    entries = ctx.execution_log.recent()
    assert entries[-1].tool == "draw_circle"
    assert entries[-1].success is False


def test_get_execution_log_tool(ctx):
    TOOLS_BY_NAME["draw_circle"].handler({"center": [0, 0], "radius": 5}, ctx)
    TOOLS_BY_NAME["get_execution_log"].handler({}, ctx)
    # a call is recorded only after its handler returns, so the first
    # get_execution_log call only shows up in a *second* call's results
    result = TOOLS_BY_NAME["get_execution_log"].handler({}, ctx)
    assert result["success"] is True
    tools_called = [e["tool"] for e in result["entries"]]
    assert "draw_circle" in tools_called
    assert "get_execution_log" in tools_called


def test_get_execution_log_respects_limit(ctx):
    for _ in range(5):
        TOOLS_BY_NAME["draw_circle"].handler({"center": [0, 0], "radius": 1}, ctx)
    result = TOOLS_BY_NAME["get_execution_log"].handler({"limit": 2}, ctx)
    assert len(result["entries"]) == 2


def test_clear_execution_log_tool(ctx):
    TOOLS_BY_NAME["draw_circle"].handler({"center": [0, 0], "radius": 5}, ctx)
    result = TOOLS_BY_NAME["clear_execution_log"].handler({}, ctx)
    assert result["success"] is True
    assert "cleared" in result["message"]
    # only the clear call itself remains, logged after clearing ran
    remaining = ctx.execution_log.recent()
    assert len(remaining) == 1
    assert remaining[0].tool == "clear_execution_log"


def test_get_performance_stats_tool(ctx):
    TOOLS_BY_NAME["draw_circle"].handler({"center": [0, 0], "radius": 5}, ctx)
    TOOLS_BY_NAME["draw_circle"].handler({"center": [0, 0], "radius": -5}, ctx)
    result = TOOLS_BY_NAME["get_performance_stats"].handler({}, ctx)
    assert result["success"] is True
    circle_stats = next(t for t in result["tools"] if t["tool"] == "draw_circle")
    assert circle_stats["calls"] == 2
    assert circle_stats["successes"] == 1
    assert circle_stats["failures"] == 1
    assert result["total_calls"] >= 2
    assert 0.0 <= result["overall_success_rate"] <= 1.0


def test_get_performance_stats_empty_log(ctx):
    result = TOOLS_BY_NAME["get_performance_stats"].handler({}, ctx)
    assert result["success"] is True
    assert result["tools"] == []
    assert result["total_calls"] == 0
    assert result["overall_success_rate"] is None


def test_get_settings_tool(ctx):
    result = TOOLS_BY_NAME["get_settings"].handler({}, ctx)
    assert result["success"] is True
    settings = result["settings"]
    assert settings["cad"]["backend"] == "dxf"
    assert "directory" in settings["output"]
    assert "directory" in settings["storage"]
    assert "directory" in settings["plugins"]


def test_get_settings_reflects_ctx_settings(ctx):
    from config import CADSettings, Settings

    ctx.settings = Settings(cad=CADSettings(backend="dxf", startup_wait_time=5.0))
    result = TOOLS_BY_NAME["get_settings"].handler({}, ctx)
    assert result["settings"]["cad"]["startup_wait_time"] == 5.0


def test_list_templates_tool(ctx):
    result = TOOLS_BY_NAME["list_templates"].handler({}, ctx)
    assert result["success"] is True
    names = {t["name"] for t in result["templates"]}
    assert "a4_landscape" in names
    assert all("description" in t and "width" in t and "height" in t for t in result["templates"])


def test_insert_title_block_tool_draws_multiple_entities(ctx):
    result = TOOLS_BY_NAME["insert_title_block"].handler(
        {"template_name": "a4_landscape", "title": "Test", "drawn_by": "AI"}, ctx
    )
    assert result["success"] is True
    assert len(result["results"]) == 7  # border + box + 3 dividers + 2 text fields
    assert len(ctx.history) == 7


def test_insert_title_block_applies_layer(ctx):
    result = TOOLS_BY_NAME["insert_title_block"].handler(
        {"template_name": "a4_landscape", "layer": "titleblock"}, ctx
    )
    assert result["success"] is True
    assert all(e["entity"]["layer"] == "titleblock" for e in result["results"])


def test_insert_title_block_applies_origin(ctx):
    result = TOOLS_BY_NAME["insert_title_block"].handler(
        {"template_name": "a4_landscape", "origin": [100, 50, 0]}, ctx
    )
    border = result["results"][0]["entity"]
    assert border["corner1"] == pytest.approx([110.0, 60.0, 0.0])


def test_insert_title_block_unknown_template_fails_cleanly(ctx):
    result = TOOLS_BY_NAME["insert_title_block"].handler({"template_name": "not_real"}, ctx)
    assert result["success"] is False
    assert "not_real" in result["message"]


def test_enqueue_operation_tool(ctx):
    result = TOOLS_BY_NAME["enqueue_operation"].handler(
        {"tool_name": "draw_circle", "arguments": {"center": [0, 0], "radius": 5}}, ctx
    )
    assert result["success"] is True
    assert result["item"]["status"] == "queued"
    assert len(ctx.execution_queue) == 1


def test_enqueue_operation_unknown_tool_fails_cleanly(ctx):
    result = TOOLS_BY_NAME["enqueue_operation"].handler({"tool_name": "not_a_real_tool"}, ctx)
    assert result["success"] is False
    assert len(ctx.execution_queue) == 0


def test_get_queue_tool(ctx):
    TOOLS_BY_NAME["enqueue_operation"].handler({"tool_name": "draw_circle", "arguments": {}}, ctx)
    result = TOOLS_BY_NAME["get_queue"].handler({}, ctx)
    assert result["success"] is True
    assert len(result["items"]) == 1
    assert result["items"][0]["tool"] == "draw_circle"


def test_remove_queue_item_tool(ctx):
    enqueued = TOOLS_BY_NAME["enqueue_operation"].handler({"tool_name": "draw_circle", "arguments": {}}, ctx)
    item_id = enqueued["item"]["id"]
    result = TOOLS_BY_NAME["remove_queue_item"].handler({"item_id": item_id}, ctx)
    assert result["success"] is True
    assert len(ctx.execution_queue) == 0


def test_remove_queue_item_unknown_id_fails_cleanly(ctx):
    result = TOOLS_BY_NAME["remove_queue_item"].handler({"item_id": 999}, ctx)
    assert result["success"] is False


def test_run_queue_partial_failure_does_not_block_other_items(ctx):
    TOOLS_BY_NAME["enqueue_operation"].handler(
        {"tool_name": "draw_circle", "arguments": {"center": [0, 0], "radius": 5}}, ctx
    )
    TOOLS_BY_NAME["enqueue_operation"].handler(
        {"tool_name": "draw_circle", "arguments": {"center": [0, 0], "radius": -5}}, ctx
    )
    result = TOOLS_BY_NAME["run_queue"].handler({}, ctx)
    assert result["success"] is True
    statuses = [r["status"] for r in result["results"]]
    assert statuses == ["succeeded", "failed"]
    assert "1 succeeded, 1 failed" in result["message"]
    # both items reflect their outcome afterward, not just in the run response
    queue = TOOLS_BY_NAME["get_queue"].handler({}, ctx)
    assert [i["status"] for i in queue["items"]] == ["succeeded", "failed"]


def test_run_queue_skips_already_run_items(ctx):
    TOOLS_BY_NAME["enqueue_operation"].handler(
        {"tool_name": "draw_circle", "arguments": {"center": [0, 0], "radius": 5}}, ctx
    )
    TOOLS_BY_NAME["run_queue"].handler({}, ctx)
    second_run = TOOLS_BY_NAME["run_queue"].handler({}, ctx)
    assert second_run["results"] == []
    assert "ran 0 item(s)" in second_run["message"]


def test_run_queue_unknown_tool_fails_that_item_only(ctx):
    ctx.execution_queue.enqueue("not_a_real_tool", {})
    result = TOOLS_BY_NAME["run_queue"].handler({}, ctx)
    assert result["results"][0]["status"] == "failed"


def test_clear_queue_tool(ctx):
    TOOLS_BY_NAME["enqueue_operation"].handler({"tool_name": "draw_circle", "arguments": {}}, ctx)
    TOOLS_BY_NAME["enqueue_operation"].handler({"tool_name": "draw_line", "arguments": {}}, ctx)
    result = TOOLS_BY_NAME["clear_queue"].handler({}, ctx)
    assert result["success"] is True
    assert "2" in result["message"]
    assert len(ctx.execution_queue) == 0
